# Contributing to Siftarr

## Development Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) - Python package manager

### Setup

```bash
git clone <repository-url>
cd siftarr
uv sync
source .venv/bin/activate

# Create data directory for SQLite database
mkdir -p data/db

uv run alembic upgrade head
uv run uvicorn app.siftarr.main:app --reload
```

Project versions come from git tags through `setuptools-scm`.
Create release tags in the form `v1.2.3`.

## Development Workflow

### Running the App

```bash
# Development with auto-reload
uv run uvicorn app.siftarr.main:app --reload

# Production
uv run uvicorn app.siftarr.main:app
```

### Versioned Docker Builds

```bash
./docker/rebuild-run-logs.sh
```

The helper script derives the current git version and passes it as a build arg.
The Docker image uses `SIFTARR_VERSION` only at build time because `.git` is excluded from the Docker build context.
Python package metadata is normalized from the tag for PEP 440 compatibility.

### Code Quality

Run these before committing:

```bash
uv run ruff format .      # Format code
uv run ruff check .       # Lint code
uv run ty check           # Type check
uv run pytest             # Run tests
```

### Database Migrations

```bash
# Create migration after modifying models
uv run alembic revision --autogenerate -m "description"

# Run migrations
uv run alembic upgrade head
```

### Adding Dependencies

```bash
uv add <package>          # Runtime dependency
uv add --dev <package>    # Development dependency
```

## Project Structure

```
app/siftarr/
├── main.py           # FastAPI entry point
├── config.py         # Configuration
├── database.py       # SQLAlchemy setup
├── models/           # Database models
├── routers/          # API routes
└── services/         # Business logic
```

## Submitting Changes

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make changes and add tests
3. Run all quality checks: `uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest`
4. Commit with descriptive messages
5. Push and create a pull request

## Questions?

Open an issue for questions or discussion.
