"""The database tests refuse any database they could damage.

Offline: these only exercise the URL checks in tests/_postgres.py, never a
connection. The URLs are made up.
"""

import pytest

from tests import _postgres


def _url(monkeypatch, name):
    monkeypatch.setenv(_postgres.TEST_URL_ENV, f"postgresql://someone@example.invalid:5432/{name}")


def test_an_unset_url_skips(monkeypatch):
    monkeypatch.delenv(_postgres.TEST_URL_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _postgres.postgres_url_or_skip()


def test_a_database_not_named_as_disposable_fails(monkeypatch):
    _url(monkeypatch, "valowithfriendsdb")
    with pytest.raises(pytest.fail.Exception, match="refusing to test against database 'valowithfriendsdb'"):
        _postgres.postgres_url_or_skip()


def test_a_rehearsal_database_may_be_read_by_tests(monkeypatch):
    _url(monkeypatch, "valo_rc3_rehearsal")
    assert _postgres.postgres_url_or_skip().endswith("/valo_rc3_rehearsal")


def test_a_rehearsal_database_is_never_emptied(monkeypatch):
    """It is a restore of production that the release is rehearsed on."""
    _url(monkeypatch, "valo_rc3_rehearsal")
    with pytest.raises(pytest.fail.Exception, match="deletes every row of the tables it uses"):
        _postgres.postgres_url_or_skip(empties_tables=True)


def test_a_rehearsal_database_is_never_written_to(monkeypatch):
    """The dangerous case is the quiet one: the scorer's committing wrapper
    UPDATES existing rows, so a test that rescored a real match here would
    change no row count and leave nothing to notice afterwards."""
    _url(monkeypatch, "valo_rc3_rehearsal")
    with pytest.raises(pytest.fail.Exception, match="commits changes to the tables it uses"):
        _postgres.postgres_url_or_skip(writes=True)


def test_a_test_database_may_be_emptied(monkeypatch):
    _url(monkeypatch, "valo_rc3_test")
    assert _postgres.postgres_url_or_skip(empties_tables=True).startswith("postgresql+psycopg2://")


def test_a_test_database_may_be_written_to(monkeypatch):
    _url(monkeypatch, "valo_rc3_test")
    assert _postgres.postgres_url_or_skip(writes=True).startswith("postgresql+psycopg2://")


@pytest.mark.parametrize('url', ['sqlite:///scratch_test', 'not-a-url',
    'postgresql://someone@example.invalid/production?application_name=/scratch_test',
    'postgresql://someone@example.invalid/scratch_test?dbname=production',
    'postgresql://someone@example.invalid/scratch_test?database=production',
    'postgresql://someone@example.invalid/scratch_test?service=production'])
def test_query_paths_or_database_overrides_cannot_bypass_the_guard(monkeypatch, url):
    monkeypatch.setenv(_postgres.TEST_URL_ENV, url)
    with pytest.raises(pytest.fail.Exception): _postgres.postgres_url_or_skip(writes=True)


def test_safe_query_options_do_not_change_the_checked_database(monkeypatch):
    monkeypatch.setenv(_postgres.TEST_URL_ENV, 'postgresql://u:p@example.invalid/scratch_test?sslmode=require')
    assert _postgres.database_name(_postgres.postgres_url_or_skip(writes=True)) == 'scratch_test'
