# Siftarr

Media search and download decision middleware (FastAPI app).

## Dev Commands

```bash
uv sync                    # Install dependencies
uv run ruff format .       # Format code
uv run ruff check .        # Lint code
uv run ty check            # Type check
uv run pytest              # Run tests

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
- Runs as non-root user `appuser:appgroup` (uid/gid 568)
- Port: 8000
- Data volume: `/data/db` and `/data/staging`

## Docker Test Workflow

When validating the app in Docker, prefer a full rebuild so code changes are definitely picked up:

```bash
cd docker/
docker compose down && docker compose build siftarr && docker compose up -d siftarr
```

This is the preferred way to run and test the app locally in Docker.

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
