def pytest_report_teststatus(report, config):
    if report.when == "call" and report.passed:
        return "passed", "", "PASSED"
