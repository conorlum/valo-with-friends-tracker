"""The timing harness refuses anything but the local corpus copy."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from time_player_views import (  # noqa: E402
    REACH_KEYS, build_reach, check_database_url, pick_filter_key, stats_match_count,
)


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


def test_reach_dict_has_exactly_the_keys_and_integers():
    out = build_reach(circle_size=5, circle_matches=40, stats_players=5, stats_matches=39, sessions=7, squad_players=3)
    assert set(out) - {"fields"} == set(REACH_KEYS)
    assert all(isinstance(out[k], int) for k in REACH_KEYS)
    assert isinstance(out["fields"], str)


def test_stats_match_count_sums_map_buckets():
    stats = {"map_side_stats": {"Ascent": {"matches": 3, "attack_wins": 1, "defense_wins": 2},
                                "Bind": {"matches": 4, "attack_wins": 0, "defense_wins": 0}}}
    assert stats_match_count(stats) == 7
    assert stats_match_count({"map_side_stats": {}}) == 0


def test_pick_filter_key_prefers_duelist_else_biggest_role():
    assert pick_filter_key({"duelist": 2, "sentinel": 9}) == "role:duelist"
    assert pick_filter_key({"duelist": 0, "sentinel": 9, "controller": 3}) == "role:sentinel"
    assert pick_filter_key({}) == "role:duelist"
