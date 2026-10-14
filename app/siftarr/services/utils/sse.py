"""Server-sent events response helper."""

from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_response(events: AsyncIterator[str]) -> StreamingResponse:
    """Stream pre-serialized SSE events without proxy buffering or caching."""
    return StreamingResponse(events, media_type="text/event-stream", headers=SSE_HEADERS)
