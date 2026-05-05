"""Authentication dependency for FastAPI endpoints."""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from app.siftarr.config import get_settings

logger = logging.getLogger(__name__)

AUTHORIZATION_SCHEME = "Bearer"


def _extract_api_key(request: Request) -> str | None:
    """Extract the API key from ``X-API-Key`` or ``Authorization: Bearer <key>``."""
    api_key_header = request.headers.get("X-API-Key")
    if api_key_header:
        return api_key_header
    authorization = request.headers.get("Authorization")
    if authorization and authorization.startswith(f"{AUTHORIZATION_SCHEME} "):
        return authorization[len(AUTHORIZATION_SCHEME) + 1 :]
    return None


async def verify_api_key(request: Request) -> None:
    """FastAPI dependency that verifies the API key.

    Checks ``X-API-Key`` header first, then ``Authorization: Bearer <key>``.
    Returns 401 if the key is missing or invalid (unless auth is disabled).
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return

    key = _extract_api_key(request)
    if key is None or key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
