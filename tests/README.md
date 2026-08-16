# Tests

The test suite uses `pytest` with `pytest-asyncio` enabled in `auto` mode. Tests are
kept close to the application boundary they exercise so targeted runs stay easy to
discover.

## Organization

- `tests/test_*.py` covers cross-cutting integration flows and small focused
  regressions that do not fit a larger package area.
- `tests/routers/auth/` covers Plex SSO login, session, and auth boundary behavior.
- `tests/routers/dashboard/` covers dashboard pages, JSON APIs, details endpoints,
  search behavior, and user-triggered actions.
- `tests/routers/settings/` covers settings pages, connection tests, maintenance,
  background jobs, and streaming imports.
- `tests/routers/stats/` covers Stats page/API behavior.
- `tests/services/admin/` covers settings, scheduler, and Plex polling workflows.
- `tests/services/auth/` covers Plex OAuth and auth-service helpers.
- `tests/services/dashboard/` covers dashboard/detail enrichment services.
- `tests/services/decisions/` covers rule evaluation and movie/TV decisions.
- `tests/services/integrations/` covers Overseerr, Prowlarr, qBittorrent, Plex, and connection clients.
- `tests/services/lifecycle/` covers lifecycle state, episode sync, pending queues, completion, and qBit moves.
- `tests/services/releases/` covers parsing, storage, serializers, staging, and release selection.
- `tests/services/utils/` covers shared helpers.

## Fixtures and mocking

- Shared fixtures live in the nearest `conftest.py`. Prefer the most specific scope
  possible, such as a router or service subdirectory, instead of adding broad global
  fixtures.
- Most database-facing tests use `unittest.mock.AsyncMock` for async SQLAlchemy
  sessions. Configure awaited methods such as `execute`, `commit`, `flush`, and
  `refresh` explicitly in the test that needs them.
- Use `monkeypatch`, `patch`, `MagicMock`, and `AsyncMock` to isolate external
  services. Tests should not require live Overseerr, Prowlarr, qBittorrent, or Plex
  instances.
- Keep fixture state isolated. If a fixture mutates module-level state, clear it in a
  `yield` fixture or an `autouse` fixture near the tests that need it.

## Async testing conventions

- `asyncio_mode = "auto"` is configured in `pyproject.toml`, so async tests can be
  written directly as `async def` tests.
- Existing tests often include `@pytest.mark.asyncio`; either style is acceptable,
  but keep the local file style consistent when editing nearby tests.
- Await application coroutines and async mocks. Do not hide async work behind sync
  wrappers unless the code under test is itself synchronous.

## Targeted commands

Run the full suite:

```bash
uv run pytest
```

Run a file, directory, class, or single test:

```bash
uv run pytest tests/services/decisions/test_rule_engine.py
uv run pytest tests/routers/dashboard/
uv run pytest tests/services/decisions/test_rule_service.py::TestRuleService
uv run pytest tests/services/decisions/test_rule_service.py::TestRuleService::test_get_all_rules
```

Run tests matching a name expression:

```bash
uv run pytest -k plex
```

For final validation, run the repository quality gates in the order documented in
[AGENTS.md](../AGENTS.md) and [CONTRIBUTING.md](../CONTRIBUTING.md).

## Frontend tests

Install Node dependencies with `npm ci`, then run the jsdom unit tests with:

```bash
npm run test:unit:js
```

The stable dashboard core modules under `frontend-src/` are strict TypeScript.
Their generated ES modules are committed under the existing
`app/siftarr/static/js/` browser paths; do not edit those generated files by
hand. After changing TypeScript, run:

```bash
npm run typecheck:frontend
npm run build:frontend
npm run verify:frontend-build  # CI check that committed output is current
```

The Playwright smoke test uses the normal production API-key authentication path; it
does not bypass or disable auth. Initialize the database, install Chromium, and start
Siftarr with a dedicated test API key:

```bash
mkdir -p data/db
SIFTARR_DB_PATH=data/db/siftarr.db uv run alembic upgrade head
npx playwright install chromium
AUTH_ENABLED=true SIFTARR_API_KEY=e2e-local-key SIFTARR_DB_PATH=data/db/siftarr.db uv run uvicorn app.siftarr.main:app
```

In another shell, pass the same key to Playwright (and optionally override the base
URL, which defaults to `http://127.0.0.1:8000`):

```bash
SIFTARR_E2E_API_KEY=e2e-local-key npm run test:e2e
# SIFTARR_E2E_BASE_URL=http://localhost:8000 SIFTARR_E2E_API_KEY=e2e-local-key npm run test:e2e
```

Use `npm run check:js` for JavaScript/TypeScript formatting, lint, and DOM unit-test
validation. Browser smoke tests remain a separate command because they require a
running application.
