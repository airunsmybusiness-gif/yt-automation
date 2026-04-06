"""Domain exceptions for the YouTube automation pipeline."""


class YTAutomationError(Exception):
    """Base exception for all pipeline errors."""


class QuotaExhaustedError(YTAutomationError):
    """All YouTube API keys have exhausted their quota."""


class VideoNotFoundError(YTAutomationError):
    """Video is deleted, private, or does not exist."""


class TranscriptUnavailableError(YTAutomationError):
    """Could not fetch transcript from any source."""


class AgentPipelineError(YTAutomationError):
    """An AI agent returned invalid output or failed."""


class GmailError(YTAutomationError):
    """Gmail API operation failed."""


class BatchJobError(YTAutomationError):
    """A batch job (TTS or Imagen) failed."""


class RenderError(YTAutomationError):
    """FFmpeg video rendering failed."""
