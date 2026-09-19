"""The Gemini client. The only module in the package that imports the provider SDK."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from ..errors import LlmError
from ..settings import Settings
from .protocol import InlineFile, LlmResult
from .retry import build_retrying

log = logging.getLogger(__name__)

MS_PER_S = 1000


class GeminiClient:
    def __init__(self, settings: Settings) -> None:
        self._client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self._timeout_s = settings.llm_timeout_s
        self._max_attempts = settings.llm_max_attempts
        self._base_delay = settings.llm_base_delay_s
        self._thinking = (
            types.ThinkingConfig(thinking_level=settings.gemini_thinking_level)
            if settings.gemini_thinking_level
            else None
        )
        self._media_resolution = settings.gemini_media_resolution

    def generate_structured[T: BaseModel](
        self,
        *,
        call_id: str,
        model: str,
        schema: type[T],
        prompt: str,
        files: Sequence[InlineFile] = (),
        max_output_tokens: int | None = None,
    ) -> LlmResult[T]:
        retrying = build_retrying(max_attempts=self._max_attempts, base_delay=self._base_delay)
        for attempt in retrying:
            with attempt:
                log.debug("calling %s for %s", model, call_id)
                return self._call(
                    model=model,
                    schema=schema,
                    prompt=prompt,
                    files=files,
                    max_output_tokens=max_output_tokens,
                )
        raise LlmError(f"{call_id}: retries exhausted")  # pragma: no cover - tenacity reraises

    def _call[T: BaseModel](
        self,
        *,
        model: str,
        schema: type[T],
        prompt: str,
        files: Sequence[InlineFile],
        max_output_tokens: int | None,
    ) -> LlmResult[T]:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=max_output_tokens,
            thinking_config=self._thinking,
            http_options=types.HttpOptions(timeout=int(self._timeout_s * MS_PER_S)),
        )
        parts: list[types.Part] = [types.Part.from_text(text=prompt)]
        parts.extend(
            types.Part.from_bytes(
                data=item.data,
                mime_type=item.mime_type,
                media_resolution=self._media_resolution or None,
            )
            for item in files
        )

        response = self._client.models.generate_content(model=model, contents=parts, config=config)
        return self._parse(response, schema)

    @staticmethod
    def _parse[T: BaseModel](
        response: types.GenerateContentResponse, schema: type[T]
    ) -> LlmResult[T]:
        candidate = (response.candidates or [None])[0]
        if candidate is not None and candidate.finish_reason == types.FinishReason.MAX_TOKENS:
            raise LlmError("response was cut off at the token limit", retryable=True)

        if response.parsed is None:
            feedback = response.prompt_feedback
            if feedback is not None and feedback.block_reason is not None:
                raise LlmError(f"request was blocked: {feedback.block_reason}", retryable=False)
            raise LlmError("model returned no parsable content", retryable=True)

        try:
            value = schema.model_validate(response.parsed)
        except ValidationError as exc:
            raise LlmError(f"response did not match the schema: {exc}", retryable=True) from exc

        usage = response.usage_metadata
        return LlmResult(
            value=value,
            model=response.model_version or "",
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
        )
