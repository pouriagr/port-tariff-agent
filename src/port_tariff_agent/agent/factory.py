"""The composition root for the query phase (ADR-026).

Both entry points build their agent here: the CLI for one terminal conversation, the API
for one session. The validation suite calls it too, with a replaying client.
"""

from __future__ import annotations

from datetime import date

from ..llm.client import GeminiClient
from ..llm.protocol import LlmClient
from ..settings import Settings
from .loop import TariffAgent
from .selector import ChargeSelector


def build_agent(
    settings: Settings,
    *,
    max_iterations: int | None = None,
    client: LlmClient | None = None,
    today: date | None = None,
) -> TariffAgent:
    """One place to swap the agent, which is what the CLI and API tests replace.

    `client` and `today` default to the provider and the system clock. Both are injectable
    because the answer depends on them: the validation suite replays a recorded client, and
    document selection is a function of the arrival date against the day of the query.
    """
    client = client or GeminiClient(settings)
    agent_args = {} if max_iterations is None else {"max_iterations": max_iterations}
    return TariffAgent(
        client=client,
        model=settings.gemini_model_agent,
        data_dir=settings.data_dir,
        selector=ChargeSelector(client, model=settings.gemini_model_extract),
        today=today,
        **agent_args,
    )
