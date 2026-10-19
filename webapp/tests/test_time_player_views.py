"""The timing harness refuses anything but the local corpus copy."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from time_player_views import check_database_url  # noqa: E402


@pytest.mark.parametrize("url", [
    "postgresql://postgres@localhost:5432/valo_groups_corpus",
    "postgresql://postgres@127.0.0.1:5432/valo_groups_corpus",
    "postgresql+psycopg2://postgres:pw@localhost/valo_groups_corpus",
])
def test_accepts_local_corpus(url):
    assert check_database_url(url) is True


@pytest.mark.parametrize("url", [
    "postgresql://u:p@db.example.com:5432/valo_groups_corpus",
    "postgresql://postgres@localhost:5432/valorant_igl_tutor",
    "postgresql://postgres@localhost:5432/valo_groups_corpus2",
    "postgresql://postgres@localhost.evil.com/valo_groups_corpus",
    "",
])
def test_refuses_others(url):
    assert check_database_url(url) is False
