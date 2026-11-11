from datetime import datetime


class DomainError(Exception):
    """Base for errors the application understands."""


class NotFound(DomainError):
    pass


class TransientError(DomainError):
    """Network, 5xx, 429: safe to retry with backoff."""


class PermanentError(DomainError):
    """Retrying won't help; goes to the dead-letter table."""


class ReconnectNeeded(PermanentError):
    def __init__(self, channel_id: str):
        super().__init__(f"Google access for channel {channel_id} expired or was revoked. Reconnect it.")
        self.channel_id = channel_id


class QuotaExceeded(DomainError):
    """YouTube quota is gone for today; retry after reset_at (next Pacific midnight)."""

    def __init__(self, reset_at: datetime, message: str = "YouTube API quota exhausted"):
        super().__init__(message)
        self.reset_at = reset_at


class QuotaBudgetBlocked(QuotaExceeded):
    """Our own budget stopped a non-critical call to keep quota for critical ones."""


class BudgetExceeded(PermanentError):
    """Daily LLM budget reached; non-critical steps are blocked."""


class LLMOutputInvalid(PermanentError):
    """The model's output failed schema validation, even after one repair attempt."""
