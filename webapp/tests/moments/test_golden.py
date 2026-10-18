"""Golden test: three real rounds of a Summit match (slots only; the fixture holds no names), through the real
engine and Summit's real geometry. Slot 1 is the match's owner, slot 3 their teammate."""

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from app.control import engine, geometry as G
from app.moments import fights

FIXTURES = Path(__file__).parent / "fixtures"
GZ = FIXTURES / "ac9fd52a_rounds.json.gz"
PLAIN = FIXTURES / "ac9fd52a_rounds.json"

# sha256 of the player names (full Riot id and name part, as written and lower-cased) in the source snapshot
NAME_DIGESTS = frozenset("""
06aa398265038ec5e86b5f45cb15ef48d4998548cf56bbdc54dfde44b23940c8
c72f915c793fa3b735751cd4bf81922da64bd34cdb1b5700b00ab44115a29176
577f0296ba08bd2786029c926be5d685af484334490bbb19a447f88e24c16bff
a1139a1f40cfaa22ede14ada214219dd7c0cb7edae6c96f751d7af6bb977b41e
c3b545d4458adb76c0dbbcbf7c51fe815af00854bc51258e080d6130879c9da3
e28a6d5af8bbc27cd9a34430263240e39a14446856007de6fb6f403af3427c86
9acf39e0f76e9cfcb306749ebe54e90b567a55b366e6b564923068ffcd6f6ef1
45ca521973d5bd01e7d479e09528b5e9847dc6a3bba9528c18970963c6cfd16c
f9680b49ba55c74c2006c4fdccb2ef7e5b71549fa69f2be4086bbd30b5169792
146358f30c2d8d2ebb5267596b216953146d33cf315e0c35816167be88d0a7da
63ba090524ca8b1d0640f352feb25fb80be81b0fb93aebea69717978ffa9fb7b
6a71eb8f9a683281c1876bebcbf230cdb65ec9f1d88a4a47639749a6ee20707b
781ea846370672d8a0ab23d95d97c2e00b4ceac8f0575f083c2d73c6b3f24e6a
4879b246743218ff59ea282ba7ca56b2a90468a260bead215d6250d077c48cb5
5653e1a36d11e4b0f8698cb8dd3fb8e230e128ec9f8a770ab19420a782c3a52a
3771fcc4afe0e11b755d81db0bc315c53f2a52621d586fb0357771829a202a03
5dc6b61eb8ff31a45b9c7c2fd8c68c229961ce2f887fbab565c43adb9319d9e9
a99c0da6fdfe528e2c10d2974c502daea02d9c352d5f018e52c8377864a6dbc8
51bec29b339c9dc8afa805fa2b58fa064b2be497769123a7e1e938ef01ffcb1e
5e2de311a9957a6dd784a4c46043f72edb0c05b845553c59d33c42624177fd8a
04e793144cd2073071d02485642b04909a01408839fdd7bcd807c47f4ea0be13
23787f9a8babb8bbda45b3a92cbf3d25d0bf36063d05ae470dcac93eeb4f2a14
f74262e6678ad6091a0d2cd6aaf39399c54b3f3f9079004fb3e921278623d5f1
bad4d0b31b3c2b3264a396817daa4ed92b969c820f6011834c9984084c34c05f
59ebb8d49d1ee4cbccf05a7219db44e697a02c8ffbb233c6ba3029f79e1a8ca1
06527d9c15d89eeb17a7534efe62639a6797c8e660d1b148056fa4c8736cadec
78e946f59a54d8b81605611979b35d8094815f0de96991f70b97a269502a7337
f0b3c30c1b24ff5257095e60838956737afabeae8d23b4cb66115a31255919d8
ba5dfec5e23d6775ad8b2e0b2f215beed91378874870c91264f1b0bac1e35b39
b02b479d7df1c1becfb3ec890510513979e13b8644d96ef71136c893b936a89c
4c90a3b0bbee3ca7c2712f9657cc81815f93c7548ac66f95fa9e6beec8d53a56
bda78dee1dd4cbb55fb5110538273d223c7fed79533d3529252da73ffb8ce4bc
8f7c789e8018a7def9c55f1dcffb043ceb3b7d414c2d086b25a86ecd6d44e421
ff3a238a028d45074dea0483d668d15ca13e3ad7cbd6109b04964daaf27d944c
""".split())


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


def _strings(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


def test_the_fixture_holds_no_player_name(raw):
    assert NAME_DIGESTS
    bad = [s for s in _strings(raw) if hashlib.sha256(s.encode()).hexdigest() in NAME_DIGESTS]
    assert bad == []
