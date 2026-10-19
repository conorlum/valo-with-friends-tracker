"""Golden test: three real rounds of a Summit match (slots only; the fixture holds no names), through the real
engine and Summit's real geometry. Slot 1 is the match's owner, slot 3 their teammate."""

import gzip
import json
from pathlib import Path

import pytest

from app.control import engine, geometry as G
from app.moments import fights

FIXTURES = Path(__file__).parent / "fixtures"
GZ = FIXTURES / "ac9fd52a_rounds.json.gz"
PLAIN = FIXTURES / "ac9fd52a_rounds.json"

# The only fields that may hold text (slot-keyed dicts aside). The fixture builder (run folder) checks the
# snapshot's names are absent; this keeps a new text field, where a name could hide, from slipping in unseen.
TEXT_KEYS = frozenset({
    "ability", "actor", "agent", "class_path", "code", "evidence", "from", "gun", "k", "kind", "map",
    "name", "owner_by", "qualification", "rpc", "s", "sha256", "side", "source_sha256", "src", "state",
    "status"})
SLOT_KEY_TEXT = frozenset({"attack", "defense", "kill", "round_end"})


def _load() -> dict:
    if GZ.exists():
        with gzip.open(GZ, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(PLAIN.read_text(encoding="utf-8"))   # a missing fixture fails here, it does not skip


@pytest.fixture(scope="module")
def raw():
    return _load()


@pytest.fixture(scope="module")
def moments(raw):
    try:
        geo = G.load_geometry("Summit")
    except (FileNotFoundError, KeyError):
        pytest.skip("Summit geometry asset is absent")
    G.visibility(geo)
    out = {}
    for rn, r in raw.items():
        link = r["link"]
        rnd = engine.RoundInputs(r["blob"], geo, engine.ControlLink(
            sides={int(k): v for k, v in link["sides"].items()},
            db_deaths=tuple(tuple(x) for x in link["db_deaths"] or ())))
        out[int(rn)] = fights.round_moments(rnd, r["blob"])["fights"]
    return out


def _slot1(moments, rn):
    return [f for f in moments[rn] if f["slot"] == 1]


def test_round_9_killing_a_player_who_could_not_see_you_is_killed_unseen(moments):
    [f] = [f for f in _slot1(moments, 9) if any(abs(k["t"] - 29.77) < 0.02 and k["victim"] == 8
                                                for k in f["kill_detail"])]
    assert "killed_unseen" in f["tags"]


def test_round_15_the_late_fight_is_three_v_three(moments):
    [f] = [f for f in _slot1(moments, 15) if abs(f["t0"] - 34.65) < 0.02 and abs(f["t1"] - 35.29) < 0.02]
    assert f["man"] == [3, 3]


def test_round_33_the_teammate_two_metres_away_is_with(moments):
    # the owner saw 2.0 m at 3.0 s; the engine measures just before the fight's first event (D6)
    [f] = [f for f in _slot1(moments, 33) if abs(f["t0"] - 2.56) < 0.02 and abs(f["t1"] - 3.10) < 0.02]
    assert f["opponents"] == [4]
    assert "with_teammate" in f["tags"]
    assert f["mates"]["with_slot"] == 3
    assert f["mates"]["with_m"] == pytest.approx(2.6, abs=0.15)


def _texts(o, key=None):
    """(key, text) for every string value, under the dict key that holds it (lists inherit it)."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _texts(v, k)
    elif isinstance(o, list):
        for v in o:
            yield from _texts(v, key)
    elif isinstance(o, str):
        yield key, o


def test_the_fixture_holds_text_only_in_known_fields(raw):
    texts = list(_texts(raw))
    assert texts
    bad = [(k, v) for k, v in texts
           if not (k in TEXT_KEYS or (str(k).isdigit() and v in SLOT_KEY_TEXT))]
    assert bad == []
    assert not [v for _, v in texts if "#" in v]        # no Riot id (name#tag) anywhere
    for r in raw.values():
        for p in r["blob"]["players"]:
            assert set(p) <= {"agent", "side", "slot"}
