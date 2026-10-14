"""Tests for the auth router."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.siftarr.routers import auth_router
from app.siftarr.routers.auth_router import PlexAuthRequest


@pytest.fixture(autouse=True)
def _mock_get_templates(monkeypatch):
    """Prevent template rendering from hitting the filesystem."""
    mock_templates = MagicMock()
    mock_templates.TemplateResponse = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(auth_router, "_get_templates", MagicMock(return_value=mock_templates))
    return mock_templates


class TestLoginPage:
    """Tests for the GET /auth/login endpoint."""

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_login_template(self):
        """Should render login.html for unauthenticated users."""
        request = MagicMock()
        request.session = {}
        request.query_params = {}

        result = await auth_router.login_page(request)

        # Should return a response (the mock template response)
        assert result is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("next_param", "expected"),
        [
            (None, "/"),
            ("/settings?tab=api", "/settings?tab=api"),
            ("https://evil.example/", "/"),
        ],
        ids=["default", "safe_local_next", "external_next_blocked"],
    )
    async def test_authenticated_redirect_target(self, next_param, expected):
        """Authenticated users keep safe local next targets; external ones fall back to /."""
        from starlette.responses import RedirectResponse

        request = MagicMock()
        request.session = {"plex_user_id": "123"}
        request.query_params = {} if next_param is None else {"next": next_param}

        result = await auth_router.login_page(request)

        assert isinstance(result, RedirectResponse)
        assert result.headers.get("location") == expected

    @pytest.mark.asyncio
    async def test_authenticated_with_initial_sync_gate_redirects_to_sync_page(self):
        """Gated first-claim sessions should return to the sync page."""
        from starlette.responses import RedirectResponse

        request = MagicMock()
        request.session = {"plex_user_id": "123", "initial_plex_sync_required": True}
        request.query_params = {"next": "/dashboard"}

        result = await auth_router.login_page(request)

        assert isinstance(result, RedirectResponse)
        assert result.headers.get("location") == "/auth/initial-plex-sync?next=%2Fdashboard"


class TestPlexAuth:
    """Tests for the POST /auth/plex endpoint."""

    @pytest.mark.asyncio
    async def test_first_user_claims_instance(self, monkeypatch):
        """First user to authenticate should claim the instance."""
        # Mock PlexOAuthService.validate_token
        monkeypatch.setattr(
            auth_router.PlexOAuthService,
            "validate_token",
            AsyncMock(
                return_value={
                    "id": "12345",
                    "username": "testuser",
                    "thumb": "http://example.com/thumb.jpg",
                }
            ),
        )

        # Mock SettingsStore — first user so claimed_id is None
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value=None)
        mock_store.set = AsyncMock()
        mock_store.load_into_environ = AsyncMock()
        monkeypatch.setattr(auth_router, "SettingsStore", MagicMock(return_value=mock_store))
        launch_sync = MagicMock()
        monkeypatch.setattr(auth_router, "_launch_plex_sign_in_sync", launch_sync)

        request = MagicMock()
        request.session = {}
        body = PlexAuthRequest(authToken="valid-token", next="/settings")

        result = await auth_router.plex_auth(request, body, db=MagicMock())

        assert result["username"] == "testuser"
        assert result["thumb"] == "http://example.com/thumb.jpg"
        assert result["redirect_url"] == "/auth/initial-plex-sync?next=%2Fsettings"
        # Should have persisted user info and token
        assert mock_store.set.call_count >= 4
        mock_store.set.assert_any_call("plex_claimed_id", "12345")
        mock_store.set.assert_any_call("plex_username", "testuser")
        mock_store.set.assert_any_call("plex_thumb", "http://example.com/thumb.jpg")
        mock_store.set.assert_any_call("plex_token", "valid-token")
        mock_store.load_into_environ.assert_called_once()
        launch_sync.assert_not_called()
        # Session should be set
        assert request.session["plex_user_id"] == "12345"
        assert request.session["initial_plex_sync_required"] is True
        assert request.session["initial_plex_sync_next"] == "/settings"

    @pytest.mark.asyncio
    async def test_same_user_allowed(self, monkeypatch):
        """Same user claiming again should be allowed."""
        monkeypatch.setattr(
            auth_router.PlexOAuthService,
            "validate_token",
            AsyncMock(
                return_value={
                    "id": "12345",
                    "username": "testuser",
                    "thumb": "",
                }
            ),
        )

        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="12345")  # Already claimed by same user
        mock_store.set = AsyncMock()
        mock_store.load_into_environ = AsyncMock()
        monkeypatch.setattr(auth_router, "SettingsStore", MagicMock(return_value=mock_store))
        launch_sync = MagicMock()
        monkeypatch.setattr(auth_router, "_launch_plex_sign_in_sync", launch_sync)

        request = MagicMock()
        request.session = {}
        body = PlexAuthRequest(authToken="valid-token", next="/settings")

        result = await auth_router.plex_auth(request, body, db=MagicMock())

        assert result["username"] == "testuser"
        assert result["redirect_url"] == "/settings"
        # Should refresh metadata/token for existing admin
        mock_store.set.assert_any_call("plex_username", "testuser")
        mock_store.set.assert_any_call("plex_thumb", "")
        mock_store.set.assert_any_call("plex_token", "valid-token")
        mock_store.load_into_environ.assert_called_once()
        launch_sync.assert_called_once()
        assert "initial_plex_sync_required" not in request.session
        assert "initial_plex_sync_next" not in request.session

    @pytest.mark.asyncio
    async def test_initial_sync_error_retry_keeps_gate(self, _mock_get_templates):
        """Retrying after a failed stream should keep rendering while the gate remains set."""
        request = MagicMock()
        request.session = {
            "plex_user_id": "123",
            "initial_plex_sync_required": True,
            "initial_plex_sync_next": "/settings",
        }
        request.query_params = {"next": "/settings"}

        result = await auth_router.initial_plex_sync_page(request)

        assert result is not None
        assert request.session["initial_plex_sync_required"] is True
        assert request.session["initial_plex_sync_next"] == "/settings"
        _mock_get_templates.TemplateResponse.assert_called_once()

    @pytest.mark.asyncio
    async def test_initial_sync_page_requires_gate(self):
        """The initial sync page should redirect once the gate is clear."""
        from starlette.responses import RedirectResponse

        request = MagicMock()
        request.session = {"plex_user_id": "123"}
        request.query_params = {"next": "/settings"}

        result = await auth_router.initial_plex_sync_page(request)

        assert isinstance(result, RedirectResponse)
        assert result.headers.get("location") == "/settings"

    @pytest.mark.asyncio
    async def test_initial_sync_complete_clears_gate(self):
        """Successful initial full-sync completion should clear the session gate."""
        request = MagicMock()
        request.session = {
            "plex_user_id": "123",
            "initial_plex_sync_required": True,
            "initial_plex_sync_next": "/settings",
            "initial_plex_sync_gate_id": "gate-1",
        }
        auth_router.pop_initial_plex_sync_completion("gate-1", "123")
        from app.siftarr.services.admin.settings_service import record_initial_plex_sync_completion

        record_initial_plex_sync_completion("gate-1", "123")

        result = await auth_router.complete_initial_plex_sync(request)

        assert result.status_code == 200
        assert "initial_plex_sync_required" not in request.session
        assert "initial_plex_sync_next" not in request.session
        assert "initial_plex_sync_gate_id" not in request.session

    @pytest.mark.asyncio
    async def test_initial_sync_complete_requires_server_side_completion(self):
        """Direct POST must not clear the gate without a completed full-sync record."""
        request = MagicMock()
        request.session = {
            "plex_user_id": "123",
            "initial_plex_sync_required": True,
            "initial_plex_sync_next": "/settings",
            "initial_plex_sync_gate_id": "missing-proof",
        }

        with pytest.raises(HTTPException) as exc_info:
            await auth_router.complete_initial_plex_sync(request)

        assert exc_info.value.status_code == 409
        assert request.session["initial_plex_sync_required"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scheduler_available", [True, False])
    async def test_successful_sign_in_schedules_sync_when_scheduler_available(
        self, monkeypatch, scheduler_available
    ):
        """Admin auth schedules the Plex sign-in sync, or skips it without a scheduler."""
        monkeypatch.setattr(
            auth_router.PlexOAuthService,
            "validate_token",
            AsyncMock(return_value={"id": "12345", "username": "testuser", "thumb": ""}),
        )

        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="12345")
        mock_store.set = AsyncMock()
        mock_store.load_into_environ = AsyncMock()
        monkeypatch.setattr(auth_router, "SettingsStore", MagicMock(return_value=mock_store))

        async def sign_in_sync():
            return None

        scheduler = MagicMock()
        scheduler.trigger_plex_sign_in_sync = MagicMock(return_value=sign_in_sync())
        monkeypatch.setattr(
            auth_router,
            "_get_active_scheduler_service",
            lambda: scheduler if scheduler_available else None,
        )

        scheduled = []

        def fake_create_task(work):
            scheduled.append(work)
            work.close()
            return MagicMock()

        monkeypatch.setattr(auth_router.asyncio, "create_task", fake_create_task)

        request = MagicMock()
        request.session = {}

        result = await auth_router.plex_auth(
            request, PlexAuthRequest(authToken="valid-token"), db=MagicMock()
        )

        assert result["username"] == "testuser"
        assert request.session["plex_user_id"] == "12345"
        assert len(scheduled) == (1 if scheduler_available else 0)

    @pytest.mark.asyncio
    async def test_sign_in_sync_launch_failure_does_not_block_session(self, monkeypatch):
        """Scheduler launch errors should be logged but auth should succeed."""
        monkeypatch.setattr(
            auth_router.PlexOAuthService,
            "validate_token",
            AsyncMock(return_value={"id": "12345", "username": "testuser", "thumb": ""}),
        )

        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="12345")
        mock_store.set = AsyncMock()
        mock_store.load_into_environ = AsyncMock()
        monkeypatch.setattr(auth_router, "SettingsStore", MagicMock(return_value=mock_store))

        async def sign_in_sync():
            return None

        scheduler = MagicMock()
        scheduler.trigger_plex_sign_in_sync = MagicMock(return_value=sign_in_sync())
        monkeypatch.setattr(auth_router, "_get_active_scheduler_service", lambda: scheduler)
        monkeypatch.setattr(
            auth_router.asyncio,
            "create_task",
            MagicMock(side_effect=RuntimeError("no loop")),
        )

        request = MagicMock()
        request.session = {}

        result = await auth_router.plex_auth(
            request, PlexAuthRequest(authToken="valid-token"), db=MagicMock()
        )

        assert result["username"] == "testuser"
        assert request.session["plex_user_id"] == "12345"

    @pytest.mark.asyncio
    async def test_second_user_rejected(self, monkeypatch):
        """Different user should be rejected if instance is already claimed."""
        monkeypatch.setattr(
            auth_router.PlexOAuthService,
            "validate_token",
            AsyncMock(
                return_value={
                    "id": "99999",  # Different user
                    "username": "otheruser",
                    "thumb": "",
                }
            ),
        )

        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="12345")  # Already claimed by user 12345
        monkeypatch.setattr(auth_router, "SettingsStore", MagicMock(return_value=mock_store))
        launch_sync = MagicMock()
        monkeypatch.setattr(auth_router, "_launch_plex_sign_in_sync", launch_sync)

        request = MagicMock()
        request.session = {}
        body = PlexAuthRequest(authToken="other-token")

        with pytest.raises(HTTPException) as exc:
            await auth_router.plex_auth(request, body, db=MagicMock())

        assert exc.value.status_code == 403
        assert str(exc.value.detail) == auth_router.ADMIN_LOGIN_MESSAGE
        launch_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self, monkeypatch):
        """Invalid Plex tokens should be rejected."""
        monkeypatch.setattr(
            auth_router.PlexOAuthService,
            "validate_token",
            AsyncMock(return_value=None),
        )

        request = MagicMock()
        request.session = {}
        body = PlexAuthRequest(authToken="bad-token")
        launch_sync = MagicMock()
        monkeypatch.setattr(auth_router, "_launch_plex_sign_in_sync", launch_sync)

        with pytest.raises(HTTPException) as exc:
            await auth_router.plex_auth(request, body, db=MagicMock())

        assert exc.value.status_code == 401
        launch_sync.assert_not_called()


class TestLogout:
    """Tests for the /auth/logout endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("handler_name", ["logout", "logout_get"])
    async def test_logout_clears_session(self, handler_name):
        """Logging out by POST or GET should clear the session and redirect to login."""
        from starlette.responses import RedirectResponse

        request = MagicMock()
        request.session = {"plex_user_id": "123", "plex_username": "testuser"}

        result = await getattr(auth_router, handler_name)(request)

        assert isinstance(result, RedirectResponse)
        assert result.headers.get("location") == "/auth/login"
        assert request.session == {}


class TestMe:
    """Tests for the GET /auth/me endpoint."""

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        """GET /auth/me should return 401 when not authenticated."""
        request = MagicMock()
        request.session = {}

        with pytest.raises(HTTPException) as exc:
            await auth_router.me(request)

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_authenticated_returns_user_info(self):
        """GET /auth/me should return user info when authenticated."""
        request = MagicMock()
        request.session = {
            "plex_user_id": "123",
            "plex_username": "testuser",
            "plex_thumb": "http://example.com/thumb.jpg",
        }

        result = await auth_router.me(request)

        assert result["username"] == "testuser"
        assert result["thumb"] == "http://example.com/thumb.jpg"
