# `app.siftarr`

Main FastAPI application package for Siftarr.

## Responsibilities

- `main.py` creates the FastAPI app, mounts static assets, registers routers, starts schedulers, and handles app startup/shutdown.
- `config.py` owns environment-backed defaults; runtime settings persisted in the database are loaded by services that need them.
- `database.py` owns the async SQLAlchemy engine, session dependency, and SQLite connection pragmas.
- `routers/` handles HTTP, UI, and API boundaries.
- `services/` owns business workflows, external integrations, background jobs, and domain coordination.
- `models/` owns persisted SQLAlchemy entities and enums.
- `templates/` and `static/` own server-rendered UI markup and browser assets.

## Runtime flow

1. Overseerr webhooks, UI actions, or scheduled sync jobs create or update requests.
2. Decision services search Prowlarr by stable media IDs, evaluate releases with rules, and store candidates.
3. Accepted releases are either staged under `/data/staging/` or sent to qBittorrent.
4. Background services retry pending requests, poll Plex, update lifecycle state, and detect completion.
5. Dashboard, Rules, Settings, and Staged routes expose status and control surfaces.

## Extension points

- Add a new page or API endpoint in `routers/`, then register the router in `main.py`.
- Add business rules, integrations, or multi-step workflows in `services/`; keep routers thin.
- Add persisted state in `models/` and update the Alembic migration when schema changes are required.
- Add shared UI layout changes in `templates/base.html`; feature-specific UI belongs near the owning template or static JS/CSS file.

## Testing guidance

- Use router tests for HTTP status, validation, templates, redirects, and dependency wiring.
- Use service tests for decision logic, integration boundaries, scheduler behavior, and state transitions.
- Use model/database tests when changing relationships, enums, indexes, or persistence assumptions.
- For package-wide confidence, run `uv run pytest` after focused tests.

Related docs: [repository map](../../repo-map.md), [contributing guide](../../CONTRIBUTING.md), [routers](routers/README.md), [services](services/README.md), and [models](models/README.md).
