"""Authentication router for Plex SSO login, logout, and session management."""

from __future__ import annotations

import asyncio
import inspect
import logging
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.siftarr.database import get_db
from app.siftarr.services.admin.settings_service import (
    SettingsStore,
    pop_initial_plex_sync_completion,
)
from app.siftarr.services.auth.plex_oauth_service import PlexOAuthService
from app.siftarr.templating import configure_templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_templates: Jinja2Templates | None = None


def _get_templates() -> Jinja2Templates:
    """Return the lazily-initialized Jinja2Templates instance."""
    global _templates
    if _templates is None:
        _templates = configure_templates(
            Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
        )
    return _templates


class PlexAuthRequest(BaseModel):
    """Request body for Plex SSO authentication."""

    authToken: str
    next: str | None = None


ADMIN_LOGIN_MESSAGE = "please login with the admin plex account"


def _safe_next_url(value: str | None) -> str:
    """Return a safe local redirect target, defaulting to dashboard."""
    if not value:
        return "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _initial_sync_url(next_url: str) -> str:
    return f"/auth/initial-plex-sync?next={quote(next_url, safe='')}"


async def _maybe_await(value: Any) -> None:
    if inspect.isawaitable(value):
        await value


def _get_active_scheduler_service() -> Any | None:
    """Return the active app scheduler service when available."""
    from app.siftarr import main

    return main.scheduler_service


def _launch_plex_sign_in_sync() -> None:
    """Launch non-blocking Plex sign-in full sync if a scheduler is active."""
    scheduler_service = _get_active_scheduler_service()
    if scheduler_service is None:
        logger.info("Plex sign-in sync skipped; scheduler unavailable")
        return

    trigger = getattr(scheduler_service, "trigger_plex_sign_in_sync", None)
    if trigger is None:
        logger.info("Plex sign-in sync skipped; scheduler does not support trigger")
        return

    sync_work = trigger()
    if not inspect.isawaitable(sync_work):
        logger.info("Plex sign-in sync skipped; scheduler returned no awaitable")
        return

    try:
        asyncio.create_task(sync_work)
    except Exception:
        close = getattr(sync_work, "close", None)
        if callable(close):
            close()
        logger.exception("Failed to launch Plex sign-in sync")


@router.get("/login")
async def login_page(request: Request) -> Response:
    """Render the login page.

    If the user already has a valid session, redirect to the dashboard instead.
    """
    from app.siftarr.services.auth_service import get_session_user

    next_url = _safe_next_url(request.query_params.get("next"))
    if get_session_user(request) is not None:
        if request.session.get("initial_plex_sync_required"):
            request.session.setdefault("initial_plex_sync_next", next_url)
            return RedirectResponse(url=_initial_sync_url(next_url))
        return RedirectResponse(url=next_url)

    templates = _get_templates()
    message = ADMIN_LOGIN_MESSAGE if request.query_params.get("denied") else None
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next_url": next_url, "message": message},
    )


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

    user_id = str(user_info.get("id") or "").strip()
    username = str(user_info.get("username") or "")
    thumb = str(user_info.get("thumb") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid Plex user identity")

    settings_store = SettingsStore(db)

    # Check if this instance is already claimed
    claimed_id = await settings_store.get("plex_claimed_id")

    is_first_claim = claimed_id is None

    if is_first_claim:
        # First-time claim — persist user info and token
        try:
            await settings_store.set("plex_claimed_id", user_id)
            await settings_store.set("plex_username", username)
            await settings_store.set("plex_thumb", thumb)
            await settings_store.set("plex_token", body.authToken)
            await _maybe_await(db.flush())
        except Exception:
            await _maybe_await(db.rollback())
            claimed_id = await settings_store.get("plex_claimed_id")
            if claimed_id != user_id:
                raise HTTPException(status_code=403, detail=ADMIN_LOGIN_MESSAGE) from None
            await settings_store.set("plex_username", username)
            await settings_store.set("plex_thumb", thumb)
            await settings_store.set("plex_token", body.authToken)
        # Push token and claim into runtime environment so auth/Plex services see it
        await settings_store.load_into_environ()
        logger.info("Instance claimed by Plex user %s (id=%s)", username, user_id)
    elif claimed_id != user_id:
        # Instance already claimed by a different user — reject
        raise HTTPException(status_code=403, detail=ADMIN_LOGIN_MESSAGE)
    else:
        await settings_store.set("plex_username", username)
        await settings_store.set("plex_thumb", thumb)
        await settings_store.set("plex_token", body.authToken)
        await settings_store.load_into_environ()
        _launch_plex_sign_in_sync()

    # Create session
    request.session["plex_user_id"] = user_id
    request.session["plex_username"] = username
    request.session["plex_thumb"] = thumb
    safe_next = _safe_next_url(body.next)
    if is_first_claim:
        request.session["initial_plex_sync_required"] = True
        request.session["initial_plex_sync_next"] = safe_next
        request.session["initial_plex_sync_gate_id"] = secrets.token_urlsafe(24)
        redirect_url = _initial_sync_url(safe_next)
    else:
        request.session.pop("initial_plex_sync_required", None)
        request.session.pop("initial_plex_sync_next", None)
        request.session.pop("initial_plex_sync_gate_id", None)
        redirect_url = safe_next

    logger.info("Plex user %s logged in (id=%s)", username, user_id)

    return {"username": username, "thumb": thumb, "redirect_url": redirect_url}


@router.get("/initial-plex-sync")
async def initial_plex_sync_page(request: Request) -> Response:
    """Render the blocking first-claim Plex sync page."""
    from app.siftarr.services.auth_service import get_session_user

    next_url = _safe_next_url(request.query_params.get("next"))
    if get_session_user(request) is None:
        return RedirectResponse(url=f"/auth/login?next={quote(next_url, safe='')}")
    if not request.session.get("initial_plex_sync_required"):
        return RedirectResponse(url=next_url)
    request.session.setdefault("initial_plex_sync_gate_id", secrets.token_urlsafe(24))

    stored_next = _safe_next_url(request.session.get("initial_plex_sync_next"))
    templates = _get_templates()
    return templates.TemplateResponse(
        request,
        "initial_plex_sync.html",
        {"next_url": stored_next},
    )


@router.post("/initial-plex-sync/complete")
async def complete_initial_plex_sync(request: Request) -> JSONResponse:
    """Clear the first-claim sync gate after the full Plex sync completes."""
    from app.siftarr.services.auth_service import get_session_user

    if get_session_user(request) is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not request.session.get("initial_plex_sync_required"):
        raise HTTPException(status_code=409, detail="Initial Plex sync is not required")
    if not pop_initial_plex_sync_completion(
        request.session.get("initial_plex_sync_gate_id"),
        request.session.get("plex_user_id"),
    ):
        raise HTTPException(status_code=409, detail="Initial Plex sync has not completed")
    redirect_url = _safe_next_url(request.session.get("initial_plex_sync_next"))
    request.session.pop("initial_plex_sync_required", None)
    request.session.pop("initial_plex_sync_next", None)
    request.session.pop("initial_plex_sync_gate_id", None)
    return JSONResponse({"redirect_url": redirect_url})


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear the session and redirect to the login page."""
    request.session.clear()
    return RedirectResponse(url="/auth/login")


@router.get("/logout")
async def logout_get(request: Request) -> RedirectResponse:
    """Safe browser logout wrapper for the navbar link."""
    return await logout(request)


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
