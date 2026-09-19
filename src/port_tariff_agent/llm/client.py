"""The Gemini client. The only module in the package that imports the provider SDK."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from ..errors import LlmError
from ..settings import Settings
from .protocol import (
    InlineFile,
    LlmResult,
    Message,
    ModelTurn,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)
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

    def generate_with_tools(
        self,
        *,
        call_id: str,
        model: str,
        system_instruction: str,
        history: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        retrying = build_retrying(max_attempts=self._max_attempts, base_delay=self._base_delay)
        for attempt in retrying:
            with attempt:
                log.debug("calling %s for %s with %d tools", model, call_id, len(tools))
                return self._call_with_tools(
                    model=model,
                    system_instruction=system_instruction,
                    history=history,
                    tools=tools,
                )
        raise LlmError(f"{call_id}: retries exhausted")  # pragma: no cover - tenacity reraises

    def _call_with_tools(
        self,
        *,
        model: str,
        system_instruction: str,
        history: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[types.Tool(function_declarations=[_declaration(tool) for tool in tools])],
            # This loop dispatches its own tools; the SDK must not call anything itself.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=self._thinking,
            http_options=types.HttpOptions(timeout=int(self._timeout_s * MS_PER_S)),
        )
        response = self._client.models.generate_content(
            model=model, contents=[_content(message) for message in history], config=config
        )
        return _read_turn(response)

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


def _declaration(tool: ToolSpec) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description,
        parameters_json_schema=dict(tool.parameters),
    )


def _content(message: Message) -> types.Content:
    match message:
        case UserMessage():
            return types.Content(role="user", parts=[types.Part.from_text(text=message.text)])
        case ToolResult():
            return types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name=message.name, response=dict(message.payload)
                    )
                ],
            )
        case ModelTurn():
            parts = (
                [_signed(types.Part.from_text(text=message.text), message.signature)]
                if message.text
                else []
            )
            parts.extend(
                _signed(
                    types.Part.from_function_call(name=call.name, args=dict(call.args)),
                    call.signature,
                )
                for call in message.tool_calls
            )
            return types.Content(role="model", parts=parts)


def _signed(part: types.Part, signature: bytes | None) -> types.Part:
    """A thinking model rejects a conversation whose earlier parts lost their signature."""
    if signature is not None:
        part.thought_signature = signature
    return part


def _read_turn(response: types.GenerateContentResponse) -> ModelTurn:
    candidate = (response.candidates or [None])[0]
    if candidate is None or candidate.content is None:
        feedback = response.prompt_feedback
        if feedback is not None and feedback.block_reason is not None:
            raise LlmError(f"request was blocked: {feedback.block_reason}", retryable=False)
        raise LlmError("model returned no content", retryable=True)
    if candidate.finish_reason == types.FinishReason.MAX_TOKENS:
        raise LlmError("response was cut off at the token limit", retryable=True)

    texts: list[str] = []
    calls: list[ToolCall] = []
    signature: bytes | None = None
    for part in candidate.content.parts or []:
        if part.text:
            texts.append(part.text)
            signature = signature or part.thought_signature
        if part.function_call is not None and part.function_call.name:
            calls.append(
                ToolCall(
                    name=part.function_call.name,
                    args=part.function_call.args or {},
                    signature=part.thought_signature,
                )
            )

    usage = response.usage_metadata
    return ModelTurn(
        text="\n".join(texts).strip(),
        tool_calls=tuple(calls),
        model=response.model_version or "",
        prompt_tokens=getattr(usage, "prompt_token_count", None),
        output_tokens=getattr(usage, "candidates_token_count", None),
    )
