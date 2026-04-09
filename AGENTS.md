# Siftarr

Media search and download decision middleware (FastAPI app).

## Dev Commands

```bash
uv sync -- extra dev       # Install dependencies
uv run alembic upgrade head # Run database migrations
uv run uvicorn app.siftarr.main:app --reload  # Dev server
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
- `docker/Dockerfile` - Container build

## Docker

- Base image: `python:3.12-slim`
- Installs `uv` at build time from ghcr.io/astral-sh/uv
- Runs as non-root user `appuser:appgroup` (uid/gid 1000)
- Port: 8000
- Data volume: `/data/db` and `/data/staging`

## Docker Test Workflow

```bash
docker/rebuild-run-logs.sh
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

ALLWAYS use subagents where possible and practical.
ALLWAYS use feature branches and PRs — never push directly to `main`. All 3 CI quality gates must pass before merge.