"""Shared fakes for admin service tests."""

from __future__ import annotations

import pytest

from app.siftarr.services.admin import scheduler_service


class FakeSessionContext:
    """Async context manager that yields a fixed session object."""

    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeScheduler:
    """Records jobs and lifecycle calls instead of scheduling anything."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.jobs: list[dict] = []
        self.started = False
        self.stopped = False

    def add_job(self, func, trigger=None, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})

    def start(self):
        self.started = True

    def shutdown(self):
        self.stopped = True


@pytest.fixture
def session_factory_for():
    """Return a factory that builds a SchedulerService session factory for one session."""

    def build(db):
        return lambda: FakeSessionContext(db)

    return build


@pytest.fixture
def fake_scheduler(monkeypatch):
    """Replace AsyncIOScheduler in scheduler_service and return the instance it builds."""
    holder: dict[str, FakeScheduler] = {}

    def build(**kwargs):
        holder["scheduler"] = FakeScheduler(**kwargs)
        return holder["scheduler"]

    monkeypatch.setattr(scheduler_service, "AsyncIOScheduler", build)

    class Proxy:
        def __getattr__(self, name):
            return getattr(holder["scheduler"], name)

    return Proxy()
