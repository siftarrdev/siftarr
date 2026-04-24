# `app.siftarr.services`

Business logic, external integrations, and background workflows live here. Services are the primary place to change Siftarr behavior.

## Responsibilities

- Decision flow: `movie_decision_service.py` and `tv_decision_service.py` search, evaluate, and choose releases. TV requests prefer packs when appropriate and fall back to episode-level coverage.
- Rules: `rule_engine.py` evaluates exclusions, requirements, scoring, and size limits; `rule_service.py` owns rule persistence behavior.
- Release handling: `release_storage.py`, `release_serializers.py`, `staging_service.py`, `staging_actions.py`, and `torrent_service.py` persist releases, stage torrents, and hand off accepted items.
- Request lifecycle: `request_service.py`, `pending_queue_service.py`, `lifecycle_service.py`, `download_completion_service.py`, and `unreleased_service.py` manage state transitions and retries.
- Integrations: `overseerr_service.py`, `prowlarr_service.py`, `qbittorrent_service.py`, `plex_service/`, and `plex_polling_service.py` wrap external APIs.
- App support: `dashboard_service.py`, `settings_service.py`, `scheduler_service.py`, `background_tasks.py`, `activity_log_service.py`, and shared helpers support UI and recurring work.

## Extension points

- Add new domain behavior as a focused service with explicit dependencies passed into `__init__`.
- Keep integration clients responsible for protocol details and service workflows responsible for orchestration.
- Preserve strict media-ID searches where available; do not replace TMDB/TVDB lookups with title-only matching unless explicitly intended.
- Use `get_settings()` for environment defaults and settings services for runtime DB-backed values.

## Testing guidance

- Prefer service-level tests for release selection, rule evaluation, staging behavior, lifecycle changes, schedulers, and integration wrappers.
- Mock HTTP clients and qBittorrent/Plex/Prowlarr/Overseerr responses; avoid real network calls.
- Cover both success and rejection/pending paths when changing decision services.
- Use async pytest patterns for services that require `AsyncSession` or async clients.

Related docs: [app package](../README.md), [routers](../routers/README.md), [models](../models/README.md), [tests](../../../tests/README.md), and [contributing guide](../../../CONTRIBUTING.md).
