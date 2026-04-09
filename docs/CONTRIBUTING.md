# Contributing to Siftarr

Thanks for your interest in contributing! This guide covers everything you need to get started.

---

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager

## Local setup

```bash
git clone <repository-url>
cd siftarr
uv sync

# Create the data directory and run migrations
mkdir -p data/db
uv run alembic upgrade head

# Start the dev server with auto-reload
uv run uvicorn app.siftarr.main:app --reload
```

The app will be at `http://localhost:8000`.

## Project structure

```
app/siftarr/
├── main.py               # FastAPI app factory, lifespan, startup migrations
├── config.py              # Pydantic settings (env vars → app config)
├── database.py            # Async SQLAlchemy engine & session
├── version.py             # Git-tag-derived version resolution
├── models/                # SQLAlchemy ORM models
│   ├── rule.py            #   Rule (exclusion/requirement/scorer/size_limit)
│   ├── release.py         #   Release (Prowlarr search results)
│   ├── request.py         #   Request (Overseerr media requests + status enum)
│   ├── staged_torrent.py  #   StagedTorrent (staging area)
│   ├── pending_queue.py   #   PendingQueue (retry queue)
│   └── settings.py        #   Settings (key/value runtime overrides)
├── routers/               # HTTP route handlers (Jinja2 + JSON)
│   ├── dashboard.py       #   Main UI, bulk actions, request details
│   ├── webhooks.py        #   Overseerr webhook receiver
│   ├── rules.py           #   CRUD + test endpoint for rules
│   ├── settings.py        #   Connection tests, staging toggle, sync
│   └── staged.py          #   Approve/discard staged torrents
├── services/              # Business logic
│   ├── rule_engine.py             # Core rule evaluation
│   ├── rule_service.py            # Rule CRUD + default seeding
│   ├── prowlarr_service.py        # Prowlarr API client
│   ├── overseerr_service.py       # Overseerr API client
│   ├── qbittorrent_service.py     # qBittorrent API client
│   ├── movie_decision_service.py # Movie search → rules → staging/qBit
│   ├── tv_decision_service.py     # TV season-first search → fallback to episodes
│   ├── release_selection_service.py # Store results, decide staging vs. direct
│   ├── staging_service.py        # Save .torrent files to /data/staging/
│   ├── staging_decision_logger.py # Append-only JSONL decision log
│   ├── pending_queue_service.py  # Retry queue management
│   ├── lifecycle_service.py      # Request status state machine
│   ├── scheduler_service.py      # APScheduler background jobs
│   ├── connection_tester.py      # Test connectivity to all services
│   ├── runtime_settings.py       # Merge env vars + DB overrides
│   ├── media_helpers.py          # Extract title/year from Overseerr
│   └── torrent_service.py       # Download & validate .torrent files
├── templates/              # Jinja2 HTML templates (Tailwind, dark mode)
└── static/                 # Favicon and brand icons
```

### Key data flow

```
Overseerr webhook
       │
       ▼
  webhooks.py  ──►  lifecycle_service.py (create Request)
       │
       ▼
  movie_decision_service.py / tv_decision_service.py
       │
       ├── prowlarr_service.py (search)
       ├── rule_engine.py (evaluate releases)
       └── release_selection_service.py
              │
              ├── staging_service.py  (if staging on)
              └── qbittorrent_service.py (if staging off)
```

---

## Development workflow

### Running the app

```bash
# Dev server with auto-reload
uv run uvicorn app.siftarr.main:app --reload

# Production mode (no reload)
uv run uvicorn app.siftarr.main:app
```

### Docker

The helper script builds from the current git tag, starts the container, and tails logs:

```bash
./docker/rebuild-run-logs.sh
```

For a full rebuild cycle:

```bash
cd docker/
docker compose down && docker compose build siftarr && docker compose up -d siftarr
```

The Docker image:
- Uses `python:3.12-slim` with `uv` for dependency management
- Runs as non-root `appuser` (uid/gid 1000)
- Runs Alembic migrations at startup
- Has a health check on `GET /health`

### Versioning

Versions come from git tags via `setuptools-scm`. Create release tags in `v1.2.3` format. The Docker build passes the tag as `SIFTARR_VERSION`; Python package metadata is normalized for PEP 440 compatibility.

---

## Quality checks

Run all four before every commit — these are enforced by CI:

```bash
uv run ruff format .       # Format
uv run ruff check .        # Lint
uv run ty check            # Type check
uv run pytest              # Tests
```

### Tests

```bash
uv run pytest                        # Run all tests
uv run pytest tests/test_rule_engine.py  # Run a single test file
```

Tests live in `tests/` and cover services, routers, models, and config using `pytest-asyncio` with an in-memory SQLite database.

---

## Database migrations

```bash
# Auto-generate a migration after changing models
uv run alembic revision --autogenerate -m "add_new_column"

# Apply all pending migrations
uv run alembic upgrade head
```

The app also runs migrations at startup, so fresh instances are always up to date.

---

## Adding dependencies

```bash
uv add <package>           # Runtime dependency
uv add --dev <package>    # Development dependency
```

---

## Submitting changes

1. **Branch** — Create a feature branch: `git checkout -b feature/my-feature`
2. **Code** — Make your changes and add tests for new behavior
3. **Check** — Run all quality gates: `uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest`
4. **Commit** — Write descriptive commit messages
5. **PR** — Push and open a pull request against `main`

All three CI quality gates must pass before merge:
1. `ruff format` + `ruff check`
2. `ty check`
3. `pytest`

---

## Questions?

Open an issue for bugs, feature requests, or discussion.