"""Exception hierarchy.

Every failure the user can hit is one of these, so the CLI can turn it into a message and
an exit code instead of a traceback.
"""

from __future__ import annotations


class PortTariffError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(PortTariffError):
    """Configuration is missing or invalid."""


class LlmError(PortTariffError):
    """A model call failed, or returned something unusable."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class IngestionError(PortTariffError):
    """Ingestion could not complete."""


class TranscriptionError(IngestionError):
    def __init__(self, failed_pages: list[int]) -> None:
        pages = ", ".join(str(page) for page in failed_pages)
        super().__init__(f"Transcription failed for page(s): {pages}")
        self.failed_pages = failed_pages


class ClassificationError(IngestionError):
    def __init__(self, section_ids: list[str]) -> None:
        sections = ", ".join(section_ids)
        super().__init__(f"Classification failed for section(s): {sections}")
        self.section_ids = section_ids


class DocumentNotFoundError(PortTariffError):
    """No ingested document matches the request."""


class AgentError(PortTariffError):
    """The agent could not produce a valid answer."""


class CalculationError(PortTariffError):
    """An expression was not something the evaluator is allowed to compute."""
