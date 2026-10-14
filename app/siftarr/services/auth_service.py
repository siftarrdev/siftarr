"""Authentication dependencies for FastAPI endpoints."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request, status

from app.siftarr.config import PLACEHOLDER_API_KEY, get_settings

logger = logging.getLogger(__name__)

AUTHORIZATION_SCHEME = "Bearer"


class BrowserAuthRequired(Exception):
    """Raised when a browser request should be redirected to Plex login."""


class InitialPlexSyncRequired(Exception):
    """Raised when the first admin session must finish the initial Plex sync."""


def _request_next_path(request: Request) -> str:
    path = request.url.path or "/"
    query = request.url.query
    return f"{path}?{query}" if query else path


def build_login_redirect_url(request: Request) -> str:
    """Return the login URL with a safe local next path for this request."""
    return f"/auth/login?next={quote(_request_next_path(request), safe='')}"


def build_initial_plex_sync_url(next_path: str) -> str:
    """Return the initial Plex sync URL that continues to ``next_path``."""
    return f"/auth/initial-plex-sync?next={quote(next_path, safe='')}"


def build_initial_plex_sync_redirect_url(request: Request) -> str:
    """Return the initial Plex sync URL with a safe local next path."""
    return build_initial_plex_sync_url(_request_next_path(request))


def _allows_initial_plex_sync_gate(request: Request) -> bool:
    path = request.scope.get("path", "/")
    return path in {
        "/auth/initial-plex-sync",
        "/auth/initial-plex-sync/complete",
        "/auth/logout",
        "/settings/api/rescan-plex/stream",
    }


def _api_key_matches(provided_key: str | None, configured_key: str) -> bool:
    """Return true only for a non-placeholder configured API key match."""
    return bool(
        provided_key
        and configured_key
        and provided_key != PLACEHOLDER_API_KEY
        and configured_key != PLACEHOLDER_API_KEY
        and provided_key == configured_key
    )


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

    claimed_id = os.environ.get("PLEX_CLAIMED_ID")
    if claimed_id and str(plex_user_id) != claimed_id:
        logger.info("Clearing stale Plex session for user id %s", plex_user_id)
        request.session.clear()
        return None

    return {
        "plex_user_id": str(plex_user_id),
        "plex_username": request.session.get("plex_username"),
        "plex_thumb": request.session.get("plex_thumb"),
    }


def is_browser_request(request: Request) -> bool:
    """Classify unauthenticated requests that should redirect to login."""
    if request.scope.get("method", "GET") not in {"GET", "HEAD"}:
        return False
    if _extract_api_key(request) is not None or request.headers.get("Authorization"):
        return False

    path = request.scope.get("path", "/")
    if path.startswith(("/api", "/requests", "/settings/api", "/staged", "/webhook")):
        return False

    accept = request.headers.get("accept", "")
    content_type = request.headers.get("content-type", "")
    if "text/event-stream" in accept or "application/json" in accept:
        return False
    if content_type.startswith(
        ("application/json", "application/x-www-form-urlencoded", "multipart/form-data")
    ):
        return False

    return "text/html" in accept or accept in {"", "*/*"}


def _authenticated_via_session_or_key(request: Request) -> bool:
    """Return True for a valid session (browser) or API key (programmatic) caller.

    Raises InitialPlexSyncRequired when the session still has to finish the
    initial Plex sync.
    """
    if get_session_user(request) is not None:
        if request.session.get("initial_plex_sync_required") and not _allows_initial_plex_sync_gate(
            request
        ):
            raise InitialPlexSyncRequired()
        return True

    return _api_key_matches(_extract_api_key(request), get_settings().api_key)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide a valid session cookie or API key.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_session_or_api_key(request: Request) -> None:
    """Require a valid browser session or API key regardless of auth toggle."""
    if _authenticated_via_session_or_key(request):
        return

    raise _unauthorized()


async def require_auth(request: Request) -> None:
    """FastAPI dependency that requires authentication.

    Checks the session cookie first (for browser users logged in via Plex SSO),
    then falls back to the API key header (for programmatic/automated access).
    Returns 401 if neither is valid (unless auth is disabled).
    """
    if _authenticated_via_session_or_key(request):
        return

    if is_browser_request(request):
        raise BrowserAuthRequired()

    if not get_settings().auth_enabled:
        return

    raise _unauthorized()
