"""SSE progress helpers for settings flows."""

import asyncio
import contextlib
import json
from typing import Any


def build_sse_progress(
    phase: str,
    *,
    current: int | None = None,
    total: int | None = None,
    title: str | None = None,
    active: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a progress payload for SSE consumers."""
    payload: dict[str, Any] = {"phase": phase}
    if current is not None:
        payload["current"] = current
    if total is not None:
        payload["total"] = total
    if title is not None:
        payload["title"] = title
    if active is not None:
        payload["active"] = active[:16]
    payload.update(extra)
    return payload


def serialize_sse(data: dict[str, Any]) -> str:
    """Serialize a payload as an SSE event."""
    return f"data: {json.dumps(data)}\n\n"


async def run_bounded_with_progress(
    items: list[Any],
    limit: int,
    worker,
    *,
    on_event,
    phase: str,
    build_sse_progress_func,
) -> list[Any]:
    """Run async work with bounded concurrency and progress callbacks."""
    semaphore = asyncio.Semaphore(max(1, limit))
    active_titles: list[str] = []
    active_lock = asyncio.Lock()
    started = 0
    finished = 0

    async def emit(payload: dict[str, Any]) -> None:
        result = on_event(payload)
        if asyncio.iscoroutine(result):
            await result

    async def run(item: Any) -> Any:
        nonlocal started, finished
        title = getattr(item, "title", None) or f"Request #{getattr(item, 'id', '?')}"

        async with semaphore:
            async with active_lock:
                started += 1
                active_titles.append(title)
                active_snapshot = active_titles[:16]

            await emit(
                build_sse_progress_func(
                    phase,
                    current=started,
                    total=len(items),
                    title=title,
                    active=active_snapshot,
                )
            )

            try:
                return await worker(item)
            finally:
                async with active_lock:
                    with contextlib.suppress(ValueError):
                        active_titles.remove(title)
                    finished += 1
                    active_snapshot = active_titles[:16]

                await emit(
                    build_sse_progress_func(
                        phase,
                        current=finished,
                        total=len(items),
                        title=title,
                        active=active_snapshot,
                    )
                )

    return await asyncio.gather(*(run(item) for item in items))
