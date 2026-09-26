"""W-d: the viewer's JavaScript decodes blobs exactly like the Python reader, and the standalone
page renders every round with no identities. The JS checks need Node; they skip without it."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WEBAPP / "scripts"))

import render_replay_standalone as standalone  # noqa: E402
from replay_synthetic import DEFAULT_KILLS, MATCH_UUID, SyntheticMatch  # noqa: E402

from app.replays import format as fmt  # noqa: E402
from app.replays.condense import condense_export_dir  # noqa: E402

REPLAY_JS = WEBAPP / "app" / "static" / "js" / "replay.js"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def condensed(tmp_path_factory):
    kills = {k: list(v) for k, v in DEFAULT_KILLS.items()}
    kills[1].append((25.0, 5, 5))  # an uncertain life, so the flags reach the JS too
    match = SyntheticMatch(shape="swiftplay", kills=kills)
    root = tmp_path_factory.mktemp("viewer")
    return condense_export_dir(match.write(root / "export"), source_sha256=match.source_sha256,
                               vrf_path=match.write_vrf(root / f"{MATCH_UUID}.vrf"))


def run_node(script: str, payload) -> dict:
    completed = subprocess.run([NODE, "-e", script, str(REPLAY_JS)], input=json.dumps(payload), capture_output=True,
                               text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_js_decoder_matches_the_python_decoder(condensed):
    blob = condensed.rounds[1]
    expected = {slot: [[list(s) for s in fmt.decode_segment(seg, blob["hz"])] for seg in segments]
                for slot, segments in blob["tracks"].items()}
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const blob = JSON.parse(input);
        const tracks = R.decodeRound(blob);
        const out = {};
        for (const slot of Object.keys(tracks)) out[slot] = tracks[slot].map(seg => seg.map(s => [s.t, s.u, s.v, s.yaw]));
        process.stdout.write(JSON.stringify(out));
      });"""
    got = run_node(script, blob)
    assert got.keys() == expected.keys()
    for slot in expected:
        for mine, theirs in zip(got[slot], expected[slot]):
            assert len(mine) == len(theirs)
            for a, b in zip(mine, theirs):
                assert a[0] == pytest.approx(b[0], abs=1e-6) and a[1:] == b[1:]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_js_position_alive_and_version_helpers(condensed):
    blob = condensed.rounds[1]
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const blob = JSON.parse(input);
        const tracks = R.decodeRound(blob);
        const seg = tracks["0"][0];
        const mid = (seg[10].t + seg[11].t) / 2;
        const at = R.trackAt(tracks["0"], mid);
        const after = R.trackAt(tracks["5"], 10.0 + 1.5);   // slot 5 died at 10 s: last known
        const gone = R.trackAt(tracks["5"], 10.0 + 2.5);    // ...and hidden after 2 s
        let refused = false;
        try { R.decodeRound(Object.assign({}, blob, {v: 99})); } catch (e) { refused = true; }
        process.stdout.write(JSON.stringify({
          mid_u: at.u, expect_u: (seg[10].u + seg[11].u) / 2, live: at.live,
          after_live: after && after.live, gone: gone,
          alive5: R.aliveAt(blob.alive["5"], 5, blob.t_end), dead5: R.aliveAt(blob.alive["5"], 20, blob.t_end),
          yaw: R.lerpYaw(350, 10, 0.5), refused: refused }));
      });"""
    got = run_node(script, blob)
    assert got["mid_u"] == pytest.approx(got["expect_u"]) and got["live"] is True
    assert got["after_live"] is False and got["gone"] is None
    assert got["alive5"]["flags"] == ["uncertain"] and got["dead5"] is None
    assert got["yaw"] == pytest.approx(0) and got["refused"] is True


def test_the_standalone_page_embeds_every_round_and_no_identity(condensed, tmp_path):
    page = standalone.render(condensed.rounds)
    assert page.count('class="replay-round"') == 0, "the round strip is built by the script"
    data = json.loads(page.split("window.REPLAY_DATA = ", 1)[1].split(";\n", 1)[0])
    assert sorted(data["rounds"], key=int) == ["1", "2", "3"]
    assert data["map"].startswith("data:image/png;base64,")
    assert all(uri and uri.startswith("data:image/png;base64,") for uri in data["icons"].values())
    assert MATCH_UUID not in page.lower() and "00000000-0000-4000-8000-0000000000" not in page
    assert "https://" not in page.split("<script>", 1)[1], "nothing is fetched from the network"


def test_the_standalone_script_writes_blobs_from_a_folder(condensed, tmp_path):
    folder = tmp_path / "blobs"
    folder.mkdir()
    for n, data in condensed.encoded_rounds().items():
        (folder / f"{n}.json.gz").write_bytes(data)
    out = tmp_path / "page.html"
    assert standalone.main(["--blobs", str(folder), "--out", str(out)]) == 0
    assert out.stat().st_size > 10_000


def test_the_page_templates_parse_and_the_partial_renders():
    env = Environment(loader=FileSystemLoader(str(WEBAPP / "app" / "templates")))
    env.get_template("replays/replay.html")  # extends base.html; parses without the site's globals
    html = env.get_template("replays/_player.html").render(replay={"map_name": "Ascent", "round_numbers": [1, 2]},
                                                         linked=False)
    for hook in ("data-replay-canvas", "data-replay-play", "data-replay-scrub", "data-replay-strip",
                 "data-replay-prev", "data-replay-next", "data-replay-speed", "data-replay-ticks"):
        assert hook in html
    assert "Not linked to a match on this site" in html and "<nav" not in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_linked_mode_helpers():
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const abilities = [
          {kind: "Zone", code: "Wraith", name: "4_Smoke", slot: 3, t0: 5, t1: 20},
          {kind: "GameObject", code: "Gumshoe", name: "4_TripWire", slot: 1, t0: 0, t1: null},
          {kind: "GameObject", code: "Gumshoe", name: "4_TripWire_SecondWire", slot: 6, t0: 0, t1: null},
          {kind: "GameObject", code: "Gumshoe", name: "4_TripWire_SecondWire", slot: 1, t0: 0.2, t1: null},
          {kind: "Projectile", code: "Wraith", name: "4_Smoke", slot: 3, t0: 4, t1: 5},
          {kind: "Bomb", code: "", name: "Spike", slot: 2, t0: 40, t1: null},
          {kind: "GameObject", code: "Newagent", name: "Q_Thing", slot: 0, t0: 1, t1: 2}
        ];
        const pairs = R.pairWires(abilities);
        process.stdout.write(JSON.stringify({
          styles: abilities.map(a => R.abilityStyle(a).shape),
          label: R.abilityStyle(abilities[6]).label,
          at10: R.abilitiesAt(abilities, 10, 90).length, at95: R.abilitiesAt(abilities, 95, 90).length,
          open: R.abilitiesAt(abilities, 89, 90).length,
          pairs: Object.keys(pairs).map(k => [Number(k), abilities.indexOf(pairs[k])]),
          signed: [R.signed(12.4), R.signed(-3.6), R.signed(0), R.signed(null)],
          tally: R.tallyAt([{t: 1, killer: 0, victim: 5}, {t: 2, killer: 5, victim: 5}, {t: 9, killer: 1, victim: 6}], 5)
        }));
      });"""
    got = run_node(script, {})
    assert got["styles"] == ["smoke", "wire", "hidden", "hidden", "hidden", "spike", "point"]
    assert got["label"] == "Q Thing"
    assert got["at10"] == 4 and got["at95"] == 0 and got["open"] == 4
    assert got["pairs"] == [[1, 3]], "a wire pairs with its own owner's second anchor"
    assert got["signed"] == ["+12", "\u22124", "0", ""]
    assert got["tally"] == {"0": {"k": 1, "d": 0}, "5": {"k": 0, "d": 2}}
