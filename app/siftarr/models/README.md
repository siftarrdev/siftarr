# `app.siftarr.models`

SQLAlchemy ORM models and domain enums for persisted Siftarr state.

## Responsibilities

- `_base.py` provides the declarative base used by all models.
- `request.py` stores media requests, status, retry metadata, Overseerr IDs, Plex metadata, and request relationships.
- `season.py` and `episode.py` track TV coverage and episode availability.
- `release.py` stores searched releases and evaluation/selection metadata.
- `rule.py` stores filtering, requirement, scoring, and size-limit rules.
- `staged_torrent.py` stores staged torrent review records.
- `activity_log.py` stores audit/history events for user-visible timelines.
- `__init__.py` exports model classes and enums for application imports and migration discovery.

## Extension points

- Add new persisted entities as separate modules and export them from `__init__.py`.
- Keep model methods small and persistence-focused; put workflow logic in services.
- Add indexes for fields used by dashboard filters, schedulers, retry scans, or relationship lookups.
- Update the Alembic migration when schema changes are required; this project keeps migration history intentionally compact while the schema is in flux.

## Testing guidance

- Add database tests for new relationships, cascade behavior, enum values, defaults, and query-critical indexes.
- Add service tests for behavior that uses model state rather than testing workflows through model methods.
- Run migration/database-focused tests after schema changes, then run `uv run pytest` before review.

Related docs: [app package](../README.md), [services](../services/README.md), [tests](../../../tests/README.md), and [contributing guide](../../../CONTRIBUTING.md).
