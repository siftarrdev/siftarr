# Tests

The test suite uses `pytest` with `pytest-asyncio` enabled in `auto` mode. Tests are
kept close to the application boundary they exercise so targeted runs stay easy to
discover.

## Organization

- `tests/test_*.py` covers cross-cutting services, integrations, parsers, config,
  lifecycle flows, and smaller router behaviors.
- `tests/routers/dashboard/` covers dashboard pages, JSON APIs, details endpoints,
  search behavior, and user-triggered actions.
- `tests/routers/settings/` covers settings pages, connection tests, maintenance,
  background jobs, and streaming imports.
- `tests/services/release_selection_service/` covers release persistence, staging,
  replacement, and related selection behavior.
- `tests/services/plex_service/` covers Plex lookup, library scan, and availability
  helpers.
- `tests/services/plex_polling_service/` covers Plex polling, incremental scans, and
  full reconciliation.

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
uv run pytest tests/test_rule_engine.py
uv run pytest tests/routers/dashboard/
uv run pytest tests/test_rule_service.py::TestRuleService
uv run pytest tests/test_rule_service.py::TestRuleService::test_get_all_rules
```

Run tests matching a name expression:

```bash
uv run pytest -k plex
```

For final validation, run the repository quality gates in the order documented in
[AGENTS.md](../AGENTS.md) and [CONTRIBUTING.md](../CONTRIBUTING.md).
