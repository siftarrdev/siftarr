"""Plex OAuth service for plex.tv API integration.

Provides PIN-based OAuth flow helpers and token/user validation
via the plex.tv public API. Used by the JS-driven login flow
to validate auth tokens server-side.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.siftarr.services.utils.http_client import get_shared_client
from app.siftarr.version import __version__

logger = logging.getLogger(__name__)

PLEX_API_BASE = "https://plex.tv"
REQUEST_TIMEOUT = 30.0


class PlexOAuthService:
    """Wraps the plex.tv OAuth and account API calls."""

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------

    @staticmethod
    def build_device_headers(client_identifier: str) -> dict[str, str]:
        """Build Plex-standard device identification headers.

        Parameters
        ----------
        client_identifier:
            A UUID string uniquely identifying this device/client session.

        Returns
        -------
        A dictionary of HTTP headers suitable for plex.tv API requests.
        """
        return {
            "X-Plex-Product": "Siftarr",
            "X-Plex-Version": __version__,
            "X-Plex-Client-Identifier": client_identifier,
            "X-Plex-Platform": "Siftarr",
            "X-Plex-Platform-Version": "1.0",
            "X-Plex-Device": "Server",
            "X-Plex-Device-Name": "Siftarr",
            "X-Plex-Model": "Siftarr SSO",
            "X-Plex-Language": "en",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # PIN flow
    # ------------------------------------------------------------------

    @classmethod
    async def request_pin(cls, client_identifier: str) -> dict[str, Any] | None:
        """Request a new Plex OAuth PIN from plex.tv.

        Parameters
        ----------
        client_identifier:
            A UUID string uniquely identifying this device/client session.

        Returns
        -------
        The JSON response dict (containing ``id`` and ``code`` keys)
        or ``None`` on failure.
        """
        url = f"{PLEX_API_BASE}/api/v2/pins?strong=true"
        headers = cls.build_device_headers(client_identifier)

        client: httpx.AsyncClient = await get_shared_client()
        try:
            response = await client.post(url, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("Failed to request Plex PIN: %s", exc)
            return None

    @classmethod
    async def check_pin(cls, pin_id: int, client_identifier: str) -> dict[str, Any] | None:
        """Check the status of a previously requested Plex OAuth PIN.

        Once the user authorises via the Plex web flow the response
        will include an ``authToken`` key.

        Parameters
        ----------
        pin_id:
            The numeric PIN id returned by :meth:`request_pin`.
        client_identifier:
            The same UUID used when requesting the PIN.

        Returns
        -------
        The JSON response dict, or ``None`` on failure.
        """
        url = f"{PLEX_API_BASE}/api/v2/pins/{pin_id}"
        headers = cls.build_device_headers(client_identifier)

        client: httpx.AsyncClient = await get_shared_client()
        try:
            response = await client.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("Failed to check Plex PIN %s: %s", pin_id, exc)
            return None

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
