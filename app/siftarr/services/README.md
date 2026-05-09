# `app.siftarr.services`

Business logic, external integrations, and background workflows live here. Services are the primary place to change Siftarr behavior.

## Responsibilities

Services are organized into thematic subpackages under `app.siftarr.services`:

- **`decisions/`** — Decision flow: `movie_decision_service` and `tv_decision_service` search, evaluate, and choose releases. TV requests prefer packs when appropriate and fall back to episode-level coverage. `rule_engine` evaluates exclusions, requirements, scoring, and size limits; `rule_service` owns rule persistence behavior.
- **`releases/`** — Release handling: `release_storage`, `release_serializers`, and `staging_service` (with integrated torrent download/validation) persist releases, stage torrents, and hand off accepted items.
- **`lifecycle/`** — Request lifecycle: `request_service`, `pending_queue_service`, `lifecycle_service`, `download_completion_service`, `episode_sync_service`, `episode_derive`, and `unreleased_service` manage state transitions and retries.
- **`integrations/`** — External APIs: `overseerr_service`, `prowlarr_service`, `qbittorrent_service`, `plex_service/`, `connection_tester`
- **`admin/`** — App support: `settings_service`, `scheduler_service`, `plex_polling_service`
- **`dashboard/`** — UI support: `dashboard_service`, `detail_service`, `tv_details_service`, `tv_enrichment_service`, `search_service`
- **`utils/`** — Shared helpers: `http_client`, `async_utils`, `type_utils`, `media_helpers`, `background_tasks`
- **Flat** — Cross-cutting: `auth_service`, `metadata_service`, `request_service`

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
