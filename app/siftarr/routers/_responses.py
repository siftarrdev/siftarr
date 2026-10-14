"""Shared JSON-or-redirect response helpers for form-driven router actions."""

from typing import Any

from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse, RedirectResponse

from app.siftarr.services.request_service import safe_local_redirect_url

__all__ = ["finalize_action_response", "safe_local_redirect_url", "wants_json"]


def wants_json(http_request: FastAPIRequest) -> bool:
    """Return True when the client asked for a JSON response."""
    return "application/json" in http_request.headers.get("accept", "")


def finalize_action_response(
    http_request: FastAPIRequest,
    message: str,
    *,
    redirect_url: str,
    payload: dict[str, Any] | None = None,
) -> JSONResponse | RedirectResponse:
    """Return a JSON status payload for fetch clients, else a 303 redirect."""
    if wants_json(http_request):
        return JSONResponse({"status": "ok", "message": message, **(payload or {})})
    return RedirectResponse(url=redirect_url, status_code=303)
