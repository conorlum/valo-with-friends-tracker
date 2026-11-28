"""The replay-search filter parser: one parser, errors name the key (R2)."""

import dataclasses

import pytest

from app.search.filters import FilterError, Filters, parse


def test_full_example_sets_fields():
    f = parse(["kind=state", "state=3v4", "post_plant=1", "left=20", "side=attack"])
    assert f.kind == "state"
    assert f.state == (3, 4)
    assert f.post_plant is True
    assert f.left == 20
    assert f.side == "attack"


def test_empty_is_all_defaults():
    f = parse([])
    assert f == Filters()
    assert f.tags == ()
    assert f.page == 1


def test_frozen():
    f = parse([])
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.kind = "kill"  # type: ignore[misc]


def test_unknown_key_names_the_key():
    with pytest.raises(FilterError, match="kidn"):
        parse(["kidn=state"])


def test_filter_error_is_value_error():
    assert issubclass(FilterError, ValueError)


def test_our_filter_needs_perspective():
    with pytest.raises(FilterError):
        parse(["state=3v4"])


@pytest.mark.parametrize(
    "arg",
    ["won=1", "our_score=3", "their_score=3", "our_buy=full", "their_buy=eco", "state=3v4", "team=ours"],
)
def test_each_perspective_key_errors_without_perspective(arg):
    key = arg.split("=")[0]
    with pytest.raises(FilterError, match=key):
        parse([arg])


@pytest.mark.parametrize("arg", ["won=1", "our_score=3", "state=3v4", "team=ours", "our_buy=full"])
def test_perspective_keys_ok_with_side_or_as(arg):
    parse([arg, "side=attack"])
    parse([arg, "as=Alpha#1"])


def test_as_with_side_is_an_error():
    with pytest.raises(FilterError):
        parse(["as=A#1", "side=attack"])


def test_tags_repeat():
    f = parse(["tag=alone", "tag=moving"])
    assert f.tags == ("alone", "moving")


def test_repeated_key_is_an_error():
    with pytest.raises(FilterError, match="kind"):
        parse(["kind=kill", "kind=death"])


@pytest.mark.parametrize(
    "arg",
    [
        "kind=x",
        "state=6v1",
        "state=3-4",
        "page=0",
        "page=abc",
        "since=2026-13-01",
        "since=20261101",
        "until=2026-02-30",
        "won=2",
        "planted=yes",
        "site=D",
        "side=up",
        "our_buy=rich",
        "team=us",
        "left=-3",
        "after=abc",
        "map=",
        "noequals",
    ],
)
def test_bad_values_error(arg):
    with pytest.raises(FilterError):
        parse([arg, "side=attack"] if not arg.startswith("side=") else [arg])


def test_bad_range():
    with pytest.raises(FilterError):
        parse(["our_score=5..3", "side=attack"])


def test_round_range_and_single():
    assert parse(["round=3..5"]).round == (3, 5)
    assert parse(["round=7"]).round == (7, 7)


def test_scores_are_ranges():
    f = parse(["our_score=0..4", "their_score=12", "as=A#1"])
    assert f.our_score == (0, 4)
    assert f.their_score == (12, 12)


def test_dates_and_map():
    f = parse(["since=2026-01-02", "until=2026-03-04", "map=Ascent"])
    assert f.since.isoformat() == "2026-01-02"
    assert f.until.isoformat() == "2026-03-04"
    assert f.map.lower() == "ascent"


def test_site_is_upper():
    assert parse(["site=b"]).site == "B"


def test_left_implies_post_plant():
    f = parse(["left=20"])
    assert f.left == 20
    assert f.post_plant is True


def test_left_conflicts_with_post_plant_zero():
    with pytest.raises(FilterError):
        parse(["left=20", "post_plant=0"])


def test_riot_ids_keep_hash():
    f = parse(["with=Alpha#1", "circle=Beta#2", "player=Gamma#3"])
    assert (f.with_, f.circle, f.player) == ("Alpha#1", "Beta#2", "Gamma#3")


def test_flags_and_page():
    f = parse(["ult=1", "first=0", "planted=1", "page=3", "ability=hunter", "agent=Sova", "after=5", "before=60.5"])
    assert f.ult is True and f.first is False and f.planted is True
    assert f.page == 3
    assert f.ability == "hunter"
    assert f.agent == "Sova"
    assert f.after == 5 and f.before == 60.5
