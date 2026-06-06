"""Top-level pytest config: e2e options + opt-in gating.

``pytest_addoption`` must live in the rootdir conftest, so the real-fleet
options are declared here. E2E tests (anything under ``tests/e2e/``) are skipped
unless ``--include-hosts`` is given, so a stray ``pytest`` can never touch the
fleet.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--fleet", action="store", default=None, help="Fleet CSV path.")
    parser.addoption(
        "--launchpad-cred",
        action="store",
        default=None,
        help="File with a kcdh_password=... line for kcdhlaunchpad.",
    )
    parser.addoption(
        "--include-hosts",
        action="store",
        default=None,
        help="Comma-separated host names to test (opt-in; required to run e2e).",
    )
    parser.addoption(
        "--keep", action="store_true", default=False, help="Keep deploy dirs."
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip every e2e test unless --include-hosts was given (opt-in safety)."""
    if config.getoption("--include-hosts"):
        return
    skip = pytest.mark.skip(reason="e2e: pass --include-hosts to run real-fleet tests")
    for item in items:
        if "/e2e/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)
