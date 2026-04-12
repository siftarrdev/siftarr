"""Shared httpx.AsyncClient with connection pooling and lifecycle management."""

import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_LIMITS = httpx.Limits(
    max_keepalive_connections=20,
    max_connections=100,
    keepalive_expiry=60,
)

_shared_client: httpx.AsyncClient | None = None

DEFAULT_LIMITS = httpx.Limits(
    max_keepalive_connections=20,
    max_connections=100,
    keepalive_expiry=60,
)


async def get_shared_client() -> httpx.AsyncClient:
    """Get or lazily create the shared httpx.AsyncClient."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            limits=DEFAULT_LIMITS, timeout=30.0, follow_redirects=True
        )
        logger.info("Created shared httpx.AsyncClient")
    return _shared_client


async def close_shared_client() -> None:
    """Close the shared httpx.AsyncClient (call on app shutdown)."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        logger.info("Closed shared httpx.AsyncClient")
    _shared_client = None
