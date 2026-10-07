"""Time order in the utility rules (the 2026-10-06 code review of the utility-review branch): nothing moves faster
than the unknown's own speed once a wall is gone, a pause holds a region that began at a sighting, a later
observation never changes an earlier picture, and a reader's sight uses the walls of its own instant."""

import numpy as np
import pytest

from app.control import engine as ce
from app.control import utility as ut
from tests.replays.control_toys import blob, open_hall, toy_ability, uv

R = 10.0
LINE = [uv(250, 96), uv(250, 296)]


@pytest.fixture(autouse=True)
def short_hearing(monkeypatch):
    monkeypatch.setitem(ut.FIGURES, "hearing_m", {k: R for k in ut.FIGURES["hearing_m"]})


def regions(b, geo, slot=5):
    out = {}

    class Watch:
        def on_tick(self, tick, unknown):
            r = unknown.reached["A"].get(slot)
            out[round(tick.t, 6)] = np.zeros(geo.n, bool) if r is None else np.isfinite(r)

    rc = ce.compute_round(b, geo, knowledge=False, observer=Watch())
    return rc, out


# ---------------------------------------------------------------- 1: a wall that ends


def test_after_a_wall_ends_the_unknown_crosses_it_at_its_own_speed():
    geo = open_hall()
    mesh = toy_ability("Cable", "E_CableJam_Root", 250, 196, 5, t=1.0, t1=8.0, kind="GameObject",
                       on=[[2.0, 8.0]], arms=[[*uv(250, 96), 10, None], [*uv(250, 296), 10, None]])
    b = blob({0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 200, 0)])}, t_end=20.0, util=[mesh])
    _, out = regions(b, geo)
    x = geo.centres[:, 0]
    assert not (out[7.5] & (x < 240)).any(), "held while it is up"
    # half a second after it ends: at most 0.5 s of walking past the wall's own line (two cells of slack)
    reach_px = (ce.UNKNOWN_MPS * 0.5 + 2 * geo.cell_m) / geo.m_per_px
    assert not (out[8.5] & (x < 250 - reach_px)).any()
    assert (out[12.0] & (x < 240)).any(), "and it does cross"


# ---------------------------------------------------------------- 2: a pause after a sighting


def test_a_pause_holds_a_region_that_began_at_a_sighting():
    geo = open_hall()
    # A watches Omen (east of them) until 0.8 s, then turns away; Omen channels from 1 to 3 s to a spot A hears
    a = ("A", [(0.0, 200, 200, 0), (0.8, 200, 200, 180)])
    marker = toy_ability("Wraith", "X_GlobalTeleport_Intention", 150, 200, 5, t=1.0, t1=3.0, kind="GameObject",
                         outcome="cancelled", evidence="test")
    b = blob({0: a, 5: ("B", [(0.0, 300, 200, 180)])}, t_end=8.0, util=[marker])
    assert [i.kind for i in ce.RoundInputs(b, geo).infos] == ["pause", "resume"]
    _, out = regions(b, geo)
    held = int(out[1.0].sum())
    assert held >= 1
    assert int(out[2.0].sum()) == held and int(out[2.5].sum()) == held, "nothing spreads while he channels"
    assert int(out[5.0].sum()) > held, "and it spreads again after"


# ---------------------------------------------------------------- 3: Omen's destination seen late


def omen_round(a_track):
    marker = toy_ability("Wraith", "X_GlobalTeleport_Intention", 380, 120, 5, t=4.0, t1=7.5, kind="GameObject",
                         outcome="completed", evidence="test")
    return blob({0: ("A", a_track), 5: ("B", [(0.0, 300, 280, 0)])}, t_end=12.0, util=[marker])


def test_a_destination_seen_later_does_not_change_what_was_known_before():
    geo = open_hall()
    never = omen_round([(0.0, 104, 200, 180)])                                  # faces away throughout
    later = omen_round([(0.0, 104, 200, 180), (6.0, 104, 200, -16)])           # turns to look at it at 6 s
    kinds = [(i.t, i.kind, i.reason) for i in ce.RoundInputs(later, geo).infos]
    assert (4.0, "broaden", "omen_unheard") in kinds, "decided on what was known at 4 s"
    assert [k for k in kinds if k[1] == "retract"] == [(6.0, "retract", "omen_seen")]
    _, a = regions(never, geo)
    _, b = regions(later, geo)
    for t in (4.0, 4.5, 5.0, 5.5):
        assert np.array_equal(a[t], b[t]), f"the same evidence until 6 s, the same picture at {t}"
    assert int(b[6.0].sum()) < int(b[5.5].sum()) / 2, "seeing where he is going takes the guess back"
    assert int(a[6.0].sum()) >= int(a[5.5].sum()) - 30


def test_a_destination_seen_from_the_start_adds_nothing():
    geo = open_hall()
    b = omen_round([(0.0, 150, 120, 0)])
    assert [i.kind for i in ce.RoundInputs(b, geo).infos] == ["pause", "resume"]


# ---------------------------------------------------------------- 4: sight at the event's own instant


def test_a_pulse_sees_with_the_walls_up_at_its_own_instant():
    geo = open_hall()
    pulse = toy_ability("BountyHunter", "E_LoSReveal_Source_Reactivate", 200, 200, 0, t=6.21, t1=8.0, kind="GameObject")
    blaze = toy_ability("Phoenix", "Q_FlameWallManager_Production", 250, 96, 5, t=6.2, t1=12.0, kind="GameObject",
                        points=LINE, on=[[6.2, 12.0]])
    b = blob({0: ("A", [(0.0, 104, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=12.0, util=[pulse, blaze])
    rnd = ce.RoundInputs(b, geo)
    assert ce.snap(6.21) < 6.2, "the frame before the wall: what the reader must not use"
    infos = [i for i in rnd.infos if i.kind == "exclude"]
    assert infos
    assert not (infos[0].mask & (geo.centres[:, 0] > 262)).any(), "nothing behind the wall raised at 6.2 s"


# ---------------------------------------------------------------- 5: Leer seen after it was cast


def test_a_leer_seen_late_allows_for_the_time_reyna_had_to_move():
    b = blob({0: ("A", [(0.0, 200, 200, 180), (11.0, 200, 200, 0)]), 5: ("B", [(0.0, 400, 280, 0)]),
              6: ("B", [(0.0, 390, 120, 0)])}, t_end=20.0,
             util=[toy_ability("Vampire", "4_NearsightAOE_Source", 300, 200, 5, t=10.0, t1=12.0, kind="GameObject")])
    [info] = ce.RoundInputs(b, open_hall()).infos
    assert info.t == pytest.approx(11.0)
    assert info.radius_m == pytest.approx(ut.FIGURES["leer_cast_range_m"] + ce.UNKNOWN_MPS * 1.0)


# ---------------------------------------------------------------- 7: an Info after the last frame


def test_an_info_between_the_last_frame_and_the_rounds_end_is_evaluated():
    geo = open_hall()
    b = blob({0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 200, 0)])}, t_end=10.03)
    info = ut.Info(10.01, "A", 5, "locate", "test", x=400.0, y=200.0)
    rc = ce.compute_round(b, geo, knowledge=False, infos=[info])
    assert max(float(t) for t in rc.ticks) == 10.0
    assert 10.01 in {round(float(t), 6) for t in rc.analytic}
    explicit = ce.compute_round(b, geo, knowledge=False, infos=[info], ticks=[0.0, 5.0])
    assert max(float(t) for t in explicit.analytic) == 5.0, "a caller's own frames are never run past"
