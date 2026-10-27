"""Our own model errors. Provider adapters translate their SDK errors into these, so the rest of
the code never depends on one provider's exception types."""

from __future__ import annotations


class ModelError(Exception):
    """Base class for every model failure. `retryable` says whether trying again can help."""

    retryable = False

    def __init__(self, message: str, *, provider: str = "unknown") -> None:
        super().__init__(message)
        self.provider = provider


class ModelAuthError(ModelError):
    """The credentials were missing, wrong or not allowed to do this."""


class ModelBadRequestError(ModelError):
    """The request was rejected as invalid (including content that is too long)."""


class ModelRateLimitError(ModelError):
    retryable = True


class ModelTimeoutError(ModelError):
    retryable = True


class ModelProviderError(ModelError):
    """The provider failed or returned something unusable."""

    retryable = True


def error_from_http_status(status: int, message: str, *, provider: str) -> ModelError:
    """Map an HTTP status from any provider to one of our errors. The message is passed on as
    given, so callers must make sure it never contains a key or other secret."""
    if status in (401, 403):
        return ModelAuthError(message, provider=provider)
    if status == 429:
        return ModelRateLimitError(message, provider=provider)
    if status in (408, 504):
        return ModelTimeoutError(message, provider=provider)
    if 400 <= status < 500:
        return ModelBadRequestError(message, provider=provider)
    return ModelProviderError(message, provider=provider)
