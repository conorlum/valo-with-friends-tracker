"""Moment links pause playback at their endpoint without advancing rounds (synthetic inputs)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPLAY_JS = Path(__file__).resolve().parents[2] / "app/static/js/replay.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def node(body, payload=None):
    script = """
const assert = require('assert/strict');
const R = require(process.argv[1]), p = JSON.parse(process.argv[2]);
global.requestAnimationFrame = () => {};
function viewer(t = 3, end = 10) {
  const v = Object.create(R.ReplayViewer.prototype);
  Object.assign(v, {t, number: 1, current: {blob: {t_end: end}}, rounds: [1, 2],
    speed: 1, playing: true, lastFrame: 0, updates: [], paints: []});
  v.updateControls = () => v.updates.push([v.t, v.playing]);
  v.draw = () => v.paints.push([v.t, v.playing]);
  return v;
}
(async () => { BODY })().catch(e => { console.error(e.stack); process.exitCode = 1; });
""".replace("BODY", body)
    done = subprocess.run([NODE, "-e", script, str(REPLAY_JS), json.dumps(payload)],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr


def test_half_second_frames_pause_exactly_at_until_and_resume_normally():
    node("""
const v = viewer();
v.setUntil(5);
for (const now of [500, 1000, 1500]) { v.tick(now); assert.equal(v.playing, true); }
v.tick(2000);
assert.equal(v.t, 5); assert.equal(v.playing, false); assert.equal(v.until, null);
assert.deepEqual(v.updates.at(-1), [5, false]);
assert.deepEqual(v.paints.at(-1), [5, false]);
v.tick(2500); assert.equal(v.t, 5);
v.toggle(); v.tick(3000); assert.equal(v.t, 5.5); assert.equal(v.playing, true);
""")


@pytest.mark.parametrize("end", [10, 5])
def test_overshooting_until_pauses_in_the_same_round_even_at_t_end(end):
    node("""
const v = viewer(4.9, p), current = v.current;
let finishes = 0, plays = 0;
v.finishRound = () => { finishes++; };
v.playRound = () => { plays++; };
v.setUntil(5); v.tick(800);
assert.equal(v.t, 5); assert.equal(v.playing, false); assert.equal(v.until, null);
assert.equal(v.number, 1); assert.equal(v.current, current);
assert.equal(finishes, 0); assert.equal(plays, 0);
assert.deepEqual(v.updates.at(-1), [5, false]);
assert.deepEqual(v.paints.at(-1), [5, false]);
""", end)


def test_invalid_until_is_ignored_and_normal_round_completion_still_runs():
    node("""
const v = viewer();
for (const bad of [2, 3, 10.1, NaN, Infinity, -Infinity, null, undefined, '5']) {
  v.setUntil(5); v.setUntil(bad); assert.equal(v.until, null);
}
v.setUntil(2); v.tick(500);
assert.equal(v.t, 3.5); assert.equal(v.playing, true);
let finishes = 0;
v.finishRound = () => { finishes++; v.playing = false; };
v.tick(8000); assert.equal(v.t, 10); assert.equal(finishes, 1);
v.current = null; v.setUntil(5); assert.equal(v.until, null);
""")


def test_switching_round_clears_until():
    node("""
const v = viewer();
v.fetchRound = async n => ({blob: {round: n, t_end: 10}});
for (const name of ['renderStrip', 'renderTicks', 'renderBanner', 'renderFeed',
  'renderMapFeatureEvents', 'renderAnalysis', 'renderUtilList', 'refreshControl',
  'renderControlTable', 'refreshGaps']) v[name] = () => {};
v.setUntil(5); await v.showRound(2);
assert.equal(v.until, null); assert.equal(v.number, 2);
v.playing = true; v.lastFrame = 0; v.tick(6000);
assert.equal(v.t, 6); assert.equal(v.playing, true);
""")


def test_seeking_to_or_past_until_clears_it_but_seeking_before_keeps_it():
    node("""
const v = viewer();
v.setUntil(5); v.seek(4); assert.equal(v.until, 5);
v.seek(5); assert.equal(v.until, null);
v.seek(3); v.setUntil(5); v.seek(6); assert.equal(v.until, null);
v.tick(500); assert.equal(v.t, 6.5); assert.equal(v.playing, true);
""")


@pytest.mark.parametrize("t_in, t_out, expected", [
    (3, None, "/replays/clip-id?round=2&t=3.00"),
    (7.5, 12, "/replays/clip-id?round=2&t=7.50&until=12.00"),
    (1.234, 5.678, "/replays/clip-id?round=2&t=1.23&until=5.68"),
    (0, 0, "/replays/clip-id?round=2&t=0.00&until=0.00"),
])
def test_moment_link_has_two_decimal_times_and_optional_until(t_in, t_out, expected):
    node("assert.equal(R.momentLink('clip-id', 2, p[0], p[1]), p[2]);", [t_in, t_out, expected])
