"""Plex OAuth service for plex.tv API integration.

Provides token/user validation via the plex.tv public API. The browser
runs the PIN flow; the server validates the resulting auth token.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.siftarr.services.utils.http_client import get_shared_client

logger = logging.getLogger(__name__)

PLEX_API_BASE = "https://plex.tv"
REQUEST_TIMEOUT = 30.0


class PlexOAuthService:
    """Wraps the plex.tv OAuth and account API calls."""

    # ------------------------------------------------------------------
    # User identity
    # ------------------------------------------------------------------

    @staticmethod
    async def get_user_identity(
        auth_token: str,
    ) -> dict[str, Any] | None:
        """Fetch the authenticated user's identity from plex.tv.

        Parameters
        ----------
        auth_token:
            The Plex auth token obtained via the OAuth PIN flow.

        Returns
        -------
        The full JSON response from ``/users/account.json``, which
        contains a ``user`` key with ``id``, ``email``, ``username``,
        and ``thumb``, or ``None`` on failure.
        """
        url = f"{PLEX_API_BASE}/users/account.json"
        headers = {
            "X-Plex-Token": auth_token,
            "Accept": "application/json",
        }

        client: httpx.AsyncClient = await get_shared_client()
        try:
            response = await client.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch Plex user identity: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Token validation
    # ------------------------------------------------------------------

    @classmethod
    async def validate_token(cls, auth_token: str) -> dict[str, Any] | None:
        """Validate a Plex auth token and return the associated user info.

        This is a convenience wrapper around :meth:`get_user_identity`
        that extracts and returns only the ``user`` dict on success.

        Parameters
        ----------
        auth_token:
            The Plex auth token to validate.

        Returns
        -------
        A dict with ``id``, ``email``, ``username``, ``thumb`` keys
        (the ``user`` sub-object from the account endpoint), or
        ``None`` if the token is invalid or the request fails.
        """
        account_data = await cls.get_user_identity(auth_token)
        if account_data is None:
            return None
        user = account_data.get("user")
        if user is None:
            logger.warning("Plex user identity response missing 'user' key")
            return None
        return user
