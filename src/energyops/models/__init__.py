"""Model interface: one common contract, our own errors, and a labelled test adapter."""

from energyops.models.base import ModelClient
from energyops.models.errors import (
    ModelAuthError,
    ModelBadRequestError,
    ModelError,
    ModelProviderError,
    ModelRateLimitError,
    ModelTimeoutError,
    error_from_http_status,
)
from energyops.models.types import Message, ModelRequest, ModelResponse, StreamChunk, Usage

__all__ = [
    "Message",
    "ModelAuthError",
    "ModelBadRequestError",
    "ModelClient",
    "ModelError",
    "ModelProviderError",
    "ModelRateLimitError",
    "ModelRequest",
    "ModelResponse",
    "ModelTimeoutError",
    "StreamChunk",
    "Usage",
    "error_from_http_status",
]
