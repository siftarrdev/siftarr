"""Authentication router for Plex SSO login, logout, and session management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db
from app.siftarr.services.admin.settings_service import SettingsStore
from app.siftarr.services.auth.plex_oauth_service import PlexOAuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_templates: Jinja2Templates | None = None


def _get_templates() -> Jinja2Templates:
    """Return the lazily-initialized Jinja2Templates instance."""
    global _templates
    if _templates is None:
        _templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
    return _templates


class PlexAuthRequest(BaseModel):
    """Request body for Plex SSO authentication."""

    authToken: str


@router.get("/login")
async def login_page(request: Request) -> Response:
    """Render the login page.

    If the user already has a valid session, redirect to the dashboard instead.
    """
    from app.siftarr.services.auth_service import get_session_user

    if get_session_user(request) is not None:
        return RedirectResponse(url="/dashboard")

    templates = _get_templates()
    return templates.TemplateResponse(request, "login.html")


@router.post("/plex")
async def plex_auth(
    request: Request,
    body: PlexAuthRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Validate a Plex auth token, claim or verify the instance, and create a session.

    Expects JSON ``{"authToken": "..."}`` from the JS-driven OAuth flow.
    """
    # Validate the token and fetch user identity from plex.tv
    user_info = await PlexOAuthService.validate_token(body.authToken)
    if user_info is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid Plex auth token",
        )

    user_id = str(user_info.get("id", ""))
    username = user_info.get("username", "")
    thumb = user_info.get("thumb", "")

    settings_store = SettingsStore(db)

    # Check if this instance is already claimed
    claimed_id = await settings_store.get("plex_claimed_id")

    if claimed_id is None:
        # First-time claim — persist user info and token
        await settings_store.set("plex_claimed_id", user_id)
        await settings_store.set("plex_username", username)
        await settings_store.set("plex_thumb", thumb)
        await settings_store.set("plex_token", body.authToken)
        # Push token into runtime environment so PlexService picks it up
        await settings_store.load_into_environ()
        logger.info("Instance claimed by Plex user %s (id=%s)", username, user_id)
    elif claimed_id != user_id:
        # Instance already claimed by a different user — reject
        raise HTTPException(
            status_code=403,
            detail="This instance is already claimed by another Plex user",
        )

    # Create session
    request.session["plex_user_id"] = user_id
    request.session["plex_username"] = username
    request.session["plex_thumb"] = thumb

    logger.info("Plex user %s logged in (id=%s)", username, user_id)

    return {"username": username, "thumb": thumb}


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear the session and redirect to the login page."""
    request.session.clear()
    return RedirectResponse(url="/auth/login")


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    """Return the current session user info, or 401 if not authenticated."""
    from app.siftarr.services.auth_service import get_session_user

    user = get_session_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )
    return {
        "username": user.get("plex_username"),
        "thumb": user.get("plex_thumb"),
    }
