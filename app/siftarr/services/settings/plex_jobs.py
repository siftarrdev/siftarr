"""Plex job status/message helpers for settings."""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


def serialize_datetime(value: datetime | None) -> str | None:
    """Serialize datetimes for compact status rendering."""
    return value.isoformat(sep=" ", timespec="seconds") if value is not None else None


def build_compact_metrics_snapshot(metrics_payload: dict[str, Any] | None) -> str | None:
    """Render a compact operator-facing metrics summary."""
    if not isinstance(metrics_payload, dict):
        return None

    parts: list[str] = []
    if "completed_requests" in metrics_payload:
        parts.append(f"completed={metrics_payload['completed_requests']}")
    for source_key, label in [
        ("scanned_items", "scanned"),
        ("matched_requests", "matched"),
        ("skipped_on_error_items", "errors"),
    ]:
        value = metrics_payload.get(source_key)
        if value is not None:
            parts.append(f"{label}={value}")

    return ", ".join(parts) if parts else None


def _coerce_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def build_plex_run_outcome_summary(
    metrics_payload: dict[str, Any] | None,
    *,
    locked: bool = False,
    lock_owner: str | None = None,
    last_error: str | None = None,
) -> str | None:
    """Summarize the last known operator-facing outcome for a Plex job."""
    if locked:
        return f"Skipped due to lock ({lock_owner or 'another worker'})"

    if not isinstance(metrics_payload, dict):
        return "No completed run recorded"

    scanned = metrics_payload.get("scanned_items")
    matched = metrics_payload.get("matched_requests")
    skipped = _coerce_int(metrics_payload.get("skipped_on_error_items"))
    completed = _coerce_int(metrics_payload.get("completed_requests"))

    if scanned is not None:
        if skipped or last_error:
            return (
                "Recent scan partial; "
                f"completed {completed}, matched {matched or 0}, scanned {scanned}, errors {max(skipped, 1)}"
            )
        return f"Recent scan completed; completed {completed}, matched {matched or 0}, scanned {scanned}"

    return f"Plex poll completed; transitioned {completed} request(s)"


def build_manual_plex_job_message(job_label: str, result: Any) -> tuple[str, str]:
    """Build a concise manual-trigger status message for Plex jobs."""
    if result.status == "locked":
        return f"{job_label} is already in progress.", "error"
    if result.status != "completed":
        return f"{job_label} failed: {result.error}", "error"

    message = f"{job_label} completed. Transitioned {result.completed_requests} request(s)."
    outcome_summary = build_plex_run_outcome_summary(result.metrics_payload)
    if outcome_summary and outcome_summary.startswith("Recent scan completed;"):
        message = (
            f"{job_label} completed. Transitioned {result.completed_requests} request(s). "
            f"{outcome_summary}."
        )
    elif outcome_summary and outcome_summary.startswith("Recent scan partial;"):
        message = (
            f"{job_label} completed partially. "
            f"Transitioned {result.completed_requests} request(s). "
            f"{outcome_summary.removeprefix('Recent scan partial; ').capitalize()}."
        )
    elif outcome_summary and outcome_summary.startswith("Plex poll completed;"):
        message = (
            f"{job_label} completed. "
            f"{outcome_summary.removeprefix('Plex poll completed; ').capitalize()}."
        )

    return message, "success"


async def build_plex_job_statuses(
    db: AsyncSession,
    *,
    incremental_job_name: str,
    full_job_name: str,
) -> list[dict[str, Any]]:
    """Load in-memory scheduler status for Plex scan jobs."""
    del db
    from app.siftarr.main import scheduler_service

    job_rows = [
        (incremental_job_name, "Recent Plex Scan", "Recent-additions scan for active requests"),
        (full_job_name, "Plex Poll", "Full active-request availability poll"),
    ]
    job_state = (
        await scheduler_service.get_plex_job_state_snapshot()
        if scheduler_service is not None
        else {}
    )

    statuses: list[dict[str, Any]] = []
    for job_name, label, description in job_rows:
        state = job_state.get(job_name, {})
        metrics_payload = state.get("metrics_payload")
        locked = bool(state.get("locked", False))
        lock_owner = state.get("lock_owner")
        last_error = state.get("last_error")
        statuses.append(
            {
                "job_name": job_name,
                "label": label,
                "description": description,
                "last_success": serialize_datetime(state.get("last_success")),
                "last_run": serialize_datetime(state.get("last_run")),
                "last_started": serialize_datetime(state.get("last_started")),
                "locked": locked,
                "lock_owner": lock_owner,
                "last_error": last_error,
                "run_summary": build_plex_run_outcome_summary(
                    metrics_payload,
                    locked=locked,
                    lock_owner=lock_owner,
                    last_error=last_error,
                ),
                "metrics_snapshot": build_compact_metrics_snapshot(metrics_payload),
                "metrics_payload": metrics_payload,
            }
        )
    return statuses
