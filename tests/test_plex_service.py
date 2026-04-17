"""Tests for PlexService movie methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.siftarr.services.plex_service import PlexService


class TestPlexServiceMovie:
    """Test cases for PlexService movie lookup methods."""

    @pytest.fixture
    def service(self):
        settings = MagicMock()
        settings.plex_url = "http://plex:32400"
        settings.plex_token = "test-token"
        svc = PlexService(settings=settings)
        return svc

    @pytest.fixture
    def mock_client(self, service, monkeypatch):
        client = AsyncMock()
        monkeypatch.setattr(service, "_get_client", AsyncMock(return_value=client))
        return client

    @pytest.mark.asyncio
    async def test_check_movie_available_true(self, service, mock_client):
        """Movie with Media entries is available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "123",
                        "title": "Test Movie",
                        "year": 2024,
                        "guid": "plex://movie/abc",
                        "Media": [{"id": 1}],
                    }
                ]
            }
        }
        mock_client.get.return_value = mock_response

        result = await service.check_movie_available(12345)
        assert result is True

    @pytest.mark.asyncio
    async def test_check_movie_available_false_no_media(self, service, mock_client):
        """Movie without Media entries is not available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "123",
                        "title": "Test Movie",
                        "year": 2024,
                        "guid": "plex://movie/abc",
                    }
                ]
            }
        }
        mock_client.get.return_value = mock_response

        result = await service.check_movie_available(12345)
        assert result is False

    @pytest.mark.asyncio
    async def test_check_movie_available_false_not_found(self, service, mock_client):
        """Movie not in Plex returns False."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"MediaContainer": {"Metadata": []}}
        mock_client.get.return_value = mock_response

        result = await service.check_movie_available(99999)
        assert result is False

    @pytest.mark.asyncio
    async def test_check_movie_available_false_http_error(self, service, mock_client):
        """HTTP error returns False."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_client.get.return_value = mock_response

        result = await service.check_movie_available(12345)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_movie_by_tmdb_returns_metadata(self, service, mock_client):
        """get_movie_by_tmdb returns movie metadata dict."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "movie",
                        "ratingKey": "456",
                        "title": "A Movie",
                        "year": 2023,
                        "guid": "plex://movie/xyz",
                        "Media": [{"id": 2}],
                    }
                ]
            }
        }
        mock_client.get.return_value = mock_response

        result = await service.get_movie_by_tmdb(555)
        assert result is not None
        assert result["rating_key"] == "456"
        assert result["title"] == "A Movie"

    @pytest.mark.asyncio
    async def test_get_movie_by_tmdb_no_config(self):
        """Returns None when Plex is not configured."""
        settings = MagicMock()
        settings.plex_url = None
        settings.plex_token = None
        svc = PlexService(settings=settings)

        result = await svc.get_movie_by_tmdb(123)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_movie_available_no_config(self):
        """Returns False when Plex is not configured."""
        settings = MagicMock()
        settings.plex_url = None
        settings.plex_token = None
        svc = PlexService(settings=settings)

        result = await svc.check_movie_available(123)
        assert result is False
