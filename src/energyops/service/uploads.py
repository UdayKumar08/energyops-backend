"""Reading an uploaded body without trusting its declared size."""

from __future__ import annotations

from fastapi import HTTPException, Request


async def read_capped(request: Request, limit: int) -> bytes:
    """The request body, refused with 413 as soon as it is over `limit` bytes. The declared
    Content-Length is checked first, and the bytes actually received are counted as well because
    the declaration can be missing or wrong."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=f"The upload is larger than {limit} bytes.")
    received = bytearray()
    async for part in request.stream():
        received.extend(part)
        if len(received) > limit:
            raise HTTPException(status_code=413, detail=f"The upload is larger than {limit} bytes.")
    return bytes(received)
