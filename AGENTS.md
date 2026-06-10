# Siftarr

Media search and download decision middleware (FastAPI app).

## Dev Commands

```bash
uv sync --extra dev       # Install dependencies
uv run alembic upgrade head # Run database migrations
uv run uvicorn app.siftarr.main:app --reload  # Dev server
npm run build:css         # Rebuild Tailwind CSS after template/style changes
```

## Project Structure

- `app/siftarr/` - Main application code
  - `main.py` - FastAPI entry point (`app.siftarr.main:app`)
  - `config.py` - Configuration
  - `database.py` - SQLAlchemy setup
  - `models/` - Database models
  - `routers/` - API routes
  - `services/` - Business logic
- `db/alembic/` - Alembic migrations
- `data/db/` - SQLite database (create before running locally)
- `docker/` - Container build, Compose, and helper scripts
- `docs/` - Documentation index and cross-cutting docs
- `tests/` - Pytest suite, grouped by routers/services/features

## Docker

- Base image: `python:3.14-slim`
- Node stage builds Tailwind CSS from `app/siftarr/static/css/tailwind-input.css`
- Installs `uv` at build time from ghcr.io/astral-sh/uv
- Runs as non-root user `appuser:appgroup` (uid/gid 1000)
- Port: 8000
- Data volume: `/data/db` and `/data/staging`

## Docker Test Workflow

```bash
docker/rebuild-run-logs.sh
docker/dev-up.sh
```

## Setup (Local)

```bash
mkdir -p data/db
uv run alembic upgrade head
```

## Quality Gates (in order)

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest
```

## General Rules

Always use subagents where possible and practical.
Always use feature branches and PRs — never push directly to `main`. All 4 quality gates must pass before merge.

## GitHub CLI

Before using `gh` commands (PRs, issues, etc.), verify the active account is correct with `gh auth status`. If the wrong account is active, switch with `gh auth switch -u <username>`.

## Database guidance

- Production schema changes must use focused, compact Alembic migrations.
- Docker startup applies Alembic migrations to `head`; do not stamp production databases over unapplied revisions.
- Fresh local databases should be initialized with `uv run alembic upgrade head`.

## Repo Map Maintenance

- `repo-map.md` is the committed, living repository map for contributors and agents.
- Update `repo-map.md` in the same PR/commit whenever you add, remove, rename, or significantly repurpose repo structure, key modules, core workflows, or important docs/scripts.
- Keep `repo-map.md` concise and high-signal: summarize responsibilities and boundaries, remove stale entries, and avoid low-value implementation detail.
- If a task changes application architecture, top-level directories, routers, services, models, tests, or operational workflows, checking and updating `repo-map.md` is required work, not optional cleanup.
