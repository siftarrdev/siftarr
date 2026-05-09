import contextlib
import logging

logger = logging.getLogger(__name__)


async def extract_media_title_and_year(
    overseerr_service, media_type: str, external_id: int
) -> tuple[str, int | None]:
    try:
        media_details = await overseerr_service.get_media_details(media_type, external_id)
        if not media_details:
            return "", None
        title = media_details.get("title") or media_details.get("name") or ""
        date_str = media_details.get("releaseDate") or media_details.get("firstAirDate") or ""
        year = None
        if date_str and len(date_str) >= 4:
            with contextlib.suppress(ValueError, TypeError):
                year = int(date_str[:4])
        return title, year
    except Exception:
        logger.warning("Failed to extract media title/year for %s/%s", media_type, external_id)
        return "", None
