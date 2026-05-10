"""Authentication dependencies for FastAPI endpoints."""

from __future__ import annotations

import logging
from typing import Any

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


def get_session_user(request: Request) -> dict[str, Any] | None:
    """Return the authenticated user from the session, or ``None``.

    Expects ``request.session`` to be available (SessionMiddleware must be installed).
    The session should contain ``plex_user_id`` and optionally ``plex_username`` and ``plex_thumb``.
    """
    plex_user_id = request.session.get("plex_user_id")
    if plex_user_id is None:
        return None
    return {
        "plex_user_id": plex_user_id,
        "plex_username": request.session.get("plex_username"),
        "plex_thumb": request.session.get("plex_thumb"),
    }


async def verify_api_key(request: Request) -> None:
    """FastAPI dependency that verifies the API key via header.

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


async def require_auth(request: Request) -> None:
    """FastAPI dependency that requires authentication.

    Checks the session cookie first (for browser users logged in via Plex SSO),
    then falls back to the API key header (for programmatic/automated access).
    Returns 401 if neither is valid (unless auth is disabled).
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return

    # Check session first (browser users)
    if get_session_user(request) is not None:
        return

    # Fall back to API key (programmatic access)
    key = _extract_api_key(request)
    if key is not None and key == settings.api_key:
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide a valid session cookie or API key.",
        headers={"WWW-Authenticate": "Bearer"},
    )
