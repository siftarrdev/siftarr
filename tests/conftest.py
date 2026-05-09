import pytest


def pytest_report_teststatus(report, config):
    if report.when == "call" and report.passed:
        return "passed", "", "PASSED"


@pytest.fixture(autouse=True)
def _clear_rule_engine_caches() -> None:
    """Clear rule engine caches before each test to avoid cross-test pollution."""
    from app.siftarr.services.decisions.rule_engine import clear_engine_caches

    clear_engine_caches()
