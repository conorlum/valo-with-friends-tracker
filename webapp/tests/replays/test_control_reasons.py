"""Why the unknown changed, stored and shown (W21; the 2026-10-05 review item 7): the display reasons are their own
stream, collapsed for reading, at each event's own time; the gap detector's locating history is untouched."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.control import engine as ce
from app.control import utility as ut
from app.control.encode import REASON_REPEAT_S, encode_data, knowledge_events
from app.replays import control_format as cf
from tests.replays.control_toys import blob, open_hall

CONTROL_JS = Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "replay_control.js"
NODE = shutil.which("node")


def run(b, infos=()):
    seen_events = []

    class Watch:
        def on_tick(self, tick, unknown):
            seen_events.extend((tick.t, *e) for e in unknown.events["A"])

    rc = ce.compute_round(b, open_hall(), knowledge=False, observer=Watch(), infos=list(infos))
    header, _ = cf.unpack_data(encode_data(rc, b))
    return rc, header, seen_events


def test_a_continuous_sighting_is_one_reason_but_every_tick_of_locating_history():
    # A looks straight at B5 for the whole round
    b = blob({0: ("A", [(0.0, 120, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=6.0)
    rc, header, events = run(b)
    assert [e for e in header["knowledge_events"]["A"] if e[2] == "seen"] == [[0.0, 5, "seen"]]
    assert len([e for e in events if e[3] == "seen"]) == len(rc.ticks), "the gap detector still gets every tick"


def test_a_sighting_that_stops_and_starts_again_is_listed_twice():
    rows = [(t, 5, "locate", "seen", "") for t in (1.0, 1.5, 2.0, 5.0, 5.5)]
    assert knowledge_events({"A": rows, "B": []})["A"] == [[1.0, 5, "seen"], [5.0, 5, "seen"]]
    assert REASON_REPEAT_S == 1.0


def test_two_enemies_at_one_time_are_both_kept_and_a_channels_end_is_not_listed():
    rows = [(3.0, 5, "locate", "neural_theft", "x"), (3.0, 6, "locate", "neural_theft", "x"),
            (4.0, 5, "pause", "omen_channel", "y"), (7.5, 5, "resume", "omen_channel", "y"),
            (8.0, 5, "cleanup", "hypothesis_cleared", "z")]
    assert knowledge_events({"A": rows, "B": []}) == {
        "A": [[3.0, 5, "neural_theft"], [3.0, 6, "neural_theft"], [4.0, 5, "omen_channel"], [8.0, 5, "hypothesis_cleared"]],
        "B": []}


def test_a_reason_keeps_its_own_time_not_the_frames_and_broadening_is_not_locating_evidence():
    b = blob({0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 200, 0)])}, t_end=10.0)
    infos = [ut.Info(6.2, "A", 5, "broaden", "omen_unheard", x=200, y=200, radius_m=5.0),
             ut.Info(6.21, "A", 5, "hypothesis", "yoru_beacon", x=300, y=250, source="b1")]
    rc, header, events = run(b, infos)
    listed = header["knowledge_events"]["A"]
    assert [6.2, 5, "omen_unheard"] in listed and [6.21, 5, "yoru_beacon"] in listed
    assert 6.2 * header["hz"] != round(6.2 * header["hz"]), "6.2 s is not on the frame grid"
    assert not [e for e in events if e[3] in ("omen_unheard", "yoru_beacon")], "neither resets the last-located time"


def test_a_round_encoded_without_reasons_has_no_list():
    b = blob({0: ("A", [(0.0, 120, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=3.0)
    rc = ce.compute_round(b, open_hall(), knowledge=False)
    rc.reasons = None
    header, _ = cf.unpack_data(encode_data(rc, b))
    assert "knowledge_events" not in header


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_panel_lists_only_what_was_known_by_now_newest_first():
    script = """
      const C = require(process.argv[1]);
      const header = {knowledge_events: {A: [[1.0, 5, "seen"], [6.2, 5, "neural_theft"], [6.2, 6, "neural_theft"], [9.0, 5, "died"]],
                                         B: [[6.2, 0, "footsteps"], [30.0, 1, "made_up_reason"]]}};
      const at = (t, w, n) => C.reasonsAt(header, t, w, n).rows.map(r => [r.side, r.t, r.slot, r.reason]);
      process.stdout.write(JSON.stringify({
        before: at(6.19, 10, 12), at_event: at(6.2, 10, 12), later: at(9.5, 10, 12), window: at(20, 10, 12),
        limit: at(9.5, 10, 2), back: at(1.0, 10, 12),
        text: [C.reasonsAt(header, 6.2, 10, 12).rows[0].text, C.reasonsAt(header, 31, 5, 12).rows[0].text],
        old: C.reasonsAt({ticks: []}, 5, 10, 12), none: C.reasonsAt(null, 5, 10, 12).status
      }));"""
    done = subprocess.run([NODE, "-e", script, str(CONTROL_JS)], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    assert got["before"] == [["A", 1.0, 5, "seen"]], "seeking to just before an event never shows it"
    assert got["at_event"] == [["A", 6.2, 5, "neural_theft"], ["A", 6.2, 6, "neural_theft"], ["B", 6.2, 0, "footsteps"],
                               ["A", 1.0, 5, "seen"]]
    assert got["later"][0] == ["A", 9.0, 5, "died"] and len(got["later"]) == 5
    assert got["window"] == [], "older than the window"
    assert got["limit"] == [["A", 9.0, 5, "died"], ["A", 6.2, 5, "neural_theft"]]
    assert got["back"] == [["A", 1.0, 5, "seen"]], "seeking back shows what was known then"
    assert got["text"] == ["revealed by Neural Theft", "made up reason"]
    assert got["old"] == {"status": "unavailable", "rows": []} and got["none"] == "unavailable"


def test_every_reason_the_engine_can_emit_has_words_in_the_panel():
    source = CONTROL_JS.read_text(encoding="utf-8")
    reasons = {"seen", "kill", "plant", "damage", "gunfire", "footsteps", "revived", "died", "hypothesis_cleared",
               *ut.REVEAL_REASONS.values(), "reveal", *ut.PULSE_SOURCES.values(), "knife_zero", "knife_all",
               "skye_no_cue", "leer_seen", "omen_channel", "omen_unheard", "waylay_recall", "yoru_beacon",
               "yoru_drift_unheard"}
    for reason in sorted(reasons):
        assert f"{reason}:" in source, reason


def test_the_panel_has_the_list_and_seeks_to_the_events_own_time():
    webapp = Path(__file__).resolve().parents[2]
    assert "data-replay-control-reasons" in (webapp / "app/templates/replays/_player.html").read_text(encoding="utf-8")
    source = (webapp / "app/static/js/replay.js").read_text(encoding="utf-8")
    assert 'self.seek(Number(row.getAttribute("data-reason-t")));' in source
    assert "if (key === this.reasonsKey) return;" in source
