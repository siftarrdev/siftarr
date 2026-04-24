# Contributing to Siftarr

Thanks for helping improve Siftarr. This guide is for developers working on the FastAPI app, tests, docs, or operational tooling.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency and virtual environment management
- Git
- Docker, if you need to test the container workflow

## Local setup

```bash
git clone <repository-url>
cd siftarr
uv sync --extra dev
mkdir -p data/db
uv run alembic upgrade head
uv run uvicorn app.siftarr.main:app --reload
```

The development server runs at <http://localhost:8000>. Local runtime data under `data/` is gitignored.

## Dependency management

Use `uv` for all dependency changes so `pyproject.toml` and `uv.lock` stay in sync.

```bash
uv add <package>        # runtime dependency
uv add --optional dev <package>  # development/test dependency in the dev extra
uv sync --extra dev     # install locked dependencies for development
```

Do not hand-edit `uv.lock`.

## Database migrations

Siftarr uses Alembic with async SQLAlchemy. For local development:

```bash
mkdir -p data/db
uv run alembic upgrade head
```

When model changes require a schema update, generate and review the migration:

```bash
uv run alembic revision --autogenerate -m "describe_change"
uv run alembic upgrade head
```

The schema is still in flux; keep only a single init migration until the database design settles. If migration history is collapsed, reset or stamp local databases as instructed in the related PR.

## Running and modifying the app

- App entry point: `app.siftarr.main:app`
- Dev server: `uv run uvicorn app.siftarr.main:app --reload`
- Production-style local run: `uv run uvicorn app.siftarr.main:app`
- Docker helper: `./docker/rebuild-run-logs.sh` (see [docker/README.md](docker/README.md))

Keep changes in the layer that owns the behavior:

- routers handle HTTP/UI/API boundaries
- services own business logic and integration workflows
- models own persisted entities and enums
- templates/static files own server-rendered UI behavior
- tests should cover new or changed behavior close to the affected area

Use [repo-map.md](repo-map.md) for the current codebase map instead of duplicating detailed structure here. Component-specific notes live beside the code in READMEs such as [app/siftarr/README.md](app/siftarr/README.md), [app/siftarr/routers/README.md](app/siftarr/routers/README.md), [app/siftarr/services/README.md](app/siftarr/services/README.md), and [app/siftarr/models/README.md](app/siftarr/models/README.md).

## Tests

Tests live under `tests/` and use `pytest` with `pytest-asyncio`. Router tests are grouped under `tests/routers/`; service and domain tests are grouped by feature or kept as top-level `tests/test_*.py` files. See [tests/README.md](tests/README.md) for fixture, async, and targeted-run guidance.

Useful commands:

```bash
uv run pytest
uv run pytest tests/test_rule_engine.py
uv run pytest tests/routers/settings/
```

Prefer focused tests while iterating, then run the full quality gates before opening a PR.

## Quality gates

Run all gates in this order before merge:

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest
```

Docs-only changes should still run the validation command requested by the task or reviewer.

## Branching and pull requests

- Work on feature branches; do not push directly to `main`.
- Keep changes scoped and include tests or docs updates for changed behavior.
- Use clear commit messages that explain why the change is needed.
- Open a PR against `main` and wait for CI/review before merge.
- All quality gates must pass before merge.

## Repo-map maintenance

`repo-map.md` is the committed, living map of repository structure and important workflows. Update it in the same PR when you add, remove, rename, or significantly repurpose top-level directories, routers, services, models, test areas, scripts, operational workflows, or documentation locations.

Keep the map concise: document responsibilities and boundaries, remove stale entries, and avoid implementation detail that belongs beside the code.

## Questions

Open an issue for bugs, feature requests, or development questions.
