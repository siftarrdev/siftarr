# Arbitratarr

Media search and download decision middleware.

## Features

- Integration with Overseerr, Prowlarr, and qBittorrent
- Async FastAPI architecture
- SQLAlchemy with Alembic migrations

## Setup

1. Copy `.env.example` to `.env` and configure
2. Install dependencies: `uv sync`
3. Run the application: `uv run uvicorn arbitratarr.main:app --reload`

## Development

```bash
# Install dev dependencies
uv sync --extra dev

# Run linter
uv run ruff check .

# Format code
uv run ruff format .
```
