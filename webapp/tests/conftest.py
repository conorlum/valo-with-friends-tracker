"""How much of the machine a test run may use: a third of its cores as xdist workers (`-n auto`), and never its
last four cores, which are kept free for whatever else is running. The whole pytest process is pinned off those
cores before any worker starts, so the workers and every process a test starts (node, a fresh python) inherit
the pin. A machine with four cores or fewer has none to spare and is not pinned."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

pytest_plugins = ('pytester', 'tests.support.postgres')

RESERVED_CORES = 4


def _allowed_cores() -> list[int] | None:
    count = os.cpu_count() or 1
    if count <= RESERVED_CORES:
        return None
    return list(range(count - RESERVED_CORES))


def _pin(cores: list[int]) -> None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
        kernel32.SetProcessAffinityMask.restype = wintypes.BOOL
        if not kernel32.SetProcessAffinityMask(kernel32.GetCurrentProcess(), sum(1 << c for c in cores)):
            raise ctypes.WinError(ctypes.get_last_error())
    elif hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, cores)


def pytest_configure(config):
    if os.environ.get('VALO_TEST_DATABASE_URL') and config.getoption('numprocesses', default=0):
        raise pytest.UsageError('A shared VALO_TEST_DATABASE_URL requires -n 0; its tests delete and rename shared tables')
    for option, executable in [('require_node', 'node')]:
        if config.getoption(option) and shutil.which(executable) is None:
            raise pytest.UsageError(f'{executable} is required for this lane')
    if config.getoption('require_postgres'):
        if not os.environ.get('VALO_TEST_DATABASE_URL'):
            raise pytest.UsageError('VALO_TEST_DATABASE_URL is required for the PostgreSQL lane')
    if config.getoption('require_fixtures'):
        for mode in ('swiftplay', 'competitive'):
            for name in ('events.ndjson', 'movement.ndjson', 'manifest.json'):
                if not (Path(__file__).parent/'fixtures'/'replay'/mode/name).is_file():
                    raise pytest.UsageError(f'Required sanitized replay fixture missing: {mode}/{name}')
    cores = _allowed_cores()
    if cores is not None:
        _pin(cores)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config):
    return max(1, (os.cpu_count() or 1) // 3)


def pytest_addoption(parser):
    for name in ('postgres', 'node', 'fixtures', 'browser'):
        parser.addoption('--require-' + name, action='store_true', help=f'Fail if required {name} prerequisites are missing')


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    from tests.support.lanes import CORPUS_MODULES, item_uses_postgres
    for item in items:
        if item_uses_postgres(item):
            item.add_marker(pytest.mark.postgres)
            if item.path.name in CORPUS_MODULES: item.add_marker(pytest.mark.corpus)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    report = result.get_result()
    if report.skipped and ((item.config.getoption('require_postgres') and item.get_closest_marker('postgres')
                            and not item.get_closest_marker('corpus')) or
                           (item.config.getoption('require_browser') and item.get_closest_marker('browser'))):
        report.outcome = 'failed'
        report.longrepr = f'Required lane skipped {item.nodeid}: {report.longrepr}'


@pytest.fixture(autouse=True)
def _isolate_required_postgres_case(request, monkeypatch):
    """Legacy PG fixtures delete whole tables: give each required-lane case its own migrated database.

    New integration fixtures already request scratch_database, so they own
    their isolation. Corpus measurements deliberately keep their input DB.
    """
    if (not request.config.getoption('require_postgres') or not request.node.get_closest_marker('postgres')
            or request.node.get_closest_marker('corpus') or 'scratch_database' in request.fixturenames):
        return
    url = request.getfixturevalue('migrated_database')
    monkeypatch.setenv('VALO_TEST_DATABASE_URL', url)
    if request.node.path.name in {
        'test_release_write_gate.py', 'test_swap_impact_scores.py', 'test_swap_parameterised_names.py',
        'test_release_players_protection.py', 'test_release_preflight.py', 'test_impact_artifact_export.py',
    }:
        from tests.support.postgres import command
        from sqlalchemy.engine import make_url
        command(url, 'scripts/install_release_write_gate.py', '--expect-database', make_url(url).database,
                '--state', 'closed', '--release-id', 'impact-rc3', '--admin-id', 'rc3-runbook')
