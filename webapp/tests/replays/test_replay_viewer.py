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
    assert got["styles"] == ["smoke", "wire", "hidden", "hidden", "hidden", "spike", "badge"]
    assert got["label"] == "Q Thing"
    assert got["at10"] == 4 and got["at95"] == 0 and got["open"] == 4
    assert got["pairs"] == [[1, 3]], "a wire pairs with its own owner's second anchor"
    assert got["signed"] == ["+12", "\u22124", "0", ""]
    assert got["tally"] == {"0": {"k": 1, "d": 0}, "5": {"k": 0, "d": 2}}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_ability_names_icons_and_drone_paths():
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const style = R.abilityStyle({kind: "Zone", code: "Wraith", name: "4_Smoke", agent: "Omen"});
        const dart = R.abilityStyle({kind: "GameObject", code: "Gumshoe", name: "RemovableObject_GumshoeTrackingDart", agent: "Cypher"});
        const path = [[1, 0, 0], [2, 100, 0], [4, 100, 200]];
        process.stdout.write(JSON.stringify({
          style: [style.ability, style.agent, style.shape], dart: [dart.agent, dart.ability, dart.label, dart.small],
          util: [R.utilAbility("phoenix_curveball_left"), R.utilAbility("breach_flashpoint"), R.utilAbility("x")],
          path: [R.pathAt(path, 1.5), R.pathAt(path, 3), R.pathAt(path, 0.5), R.pathAt(path, 5)]
        }));
      });"""
    got = run_node(script, {})
    assert got["style"] == ["Dark Cover", "Omen", "smoke"]
    assert got["dart"] == ["Cypher", "Spycam", "Spycam dart", True]
    assert got["util"] == [{"agent": "Phoenix", "ability": "Curveball"}, {"agent": "Breach", "ability": "Flashpoint"},
                           {"agent": None, "ability": "x"}]
    assert got["path"] == [{"u": 50, "v": 0}, {"u": 100, "v": 100}, None, None]


def test_every_named_ability_has_a_vendored_icon():
    """Each ability the viewer names (ABILITY_STYLES, UTIL_ABILITIES) is in abilities.json with a file."""
    import re

    index = json.loads((WEBAPP / "app" / "static" / "data" / "abilities.json").read_text(encoding="utf-8"))
    icons = {name: url for table in index.values() for name, url in table.items()}
    source = REPLAY_JS.read_text(encoding="utf-8")
    named = set(re.findall(r'ability: "([^"]+)"', source)) | set(re.findall(r'\["[A-Za-z]+", "([^"]+)"\]', source))
    assert named, "no ability names found"
    for name in sorted(named):
        assert name in icons, f"{name} has no icon in abilities.json"
        assert (WEBAPP / "app" / icons[name].lstrip("/")).is_file(), icons[name]


# Every non-projectile ability archetype in the seven exports so far (Ascent, Abyss, Split, Summit,
# Sunset, Haven and the Swiftplay test replay), as extras.py keys it: `<code>_<name>` after
# normalize_archetype. Each must resolve to a style, not the unknown-archetype fallback.
SEEN_ARCHETYPES = """
Aggrobot_Reclaim_Orb_ExplodeyPatch Aggrobot_Reclaim_Orb_SeekerNade Aggrobot_Reclaim_Orb_SeekerNadePlantSuccessful
Aggrobot_Reclaim_Orb_Turret Aggrobot_X_Reclaim_Orb BountyHunter_E_LoSReveal_Source_Reactivate BountyHunter_X_WaveForm
Breach_4_FusionBlast Breach_E_SweetSpotFissure Breach_X_Shockwave Cashew_4_SonarPing Cashew_E_AirStrikeMortar
Cashew_E_Explosion Cashew_E_MapMissileMarker Cashew_E_MapMissileMarker_SecondRocket Cashew_Q_ShellShockGrenade
Cashew_X_Segment Cashew_X_SegmentManager Clay_Q_Explosion Deadeye_E_Teleporter_Tether Deadeye_E_Trap
Guide_4_Heal_AOE Guide_E_HawkFlash_FlashSource Gumshoe_4_TripWire Gumshoe_4_TripWire_SecondWire Gumshoe_Q_CageTrap
Gumshoe_X_InterrogateHat Hunter_4_ExplosiveBolt_Explosion Hunter_E_Drone_RevealDart Hunter_Q_SonarBolt
Hunter_Q_SonarPing Iris_E_Smoke Iris_X_SonicWave Pandemic_E_SmokeScreenManager Phoenix_Q_FlameWallManager_Production
Phoenix_X_ResTarget_Production Pine_4_UsableTeleport Pine_Q_SeizeTrap Pine_Q_Tether_SphereExpansion
BountyHunter_Q_Tether_SphereExpansion Gumshoe_RemovableObject_GumshoeTrackingDart Rift_4_BlackHole Rift_E_SmokeZone
Rift_E_SmokeZone_Fake Rift_Q_FlashBurst Rift_X_GlobalWall Rift_X_Markers Sarge_4_SmokeManager_Production
Sarge_4_Smoke_ProductionNEW Sarge_E_SpeedStim Sarge_X_OrbitalStrike_Production Sequoia_4_MovingCover Sequoia_E_Orb
Sequoia_E_Shield Sequoia_Q_FragileMissile_TrajectoryWarning Sequoia_X_LineCapture Smonk_NewSmoke Smonk_NewSmoke_PDS
Smonk_Q_DecayExplosion Stealth_DecoySpawner Terra_C_TimeSlowGrenade_Explosion Terra_E_RewindTime_RewindTarget
Terra_X_DelayedBeam_Beam Thorne_E_Wall_Fortifying Thorne_E_Wall_Segment_Fortifying Iris_Concuss Iris_ConcussPulse
Iris_Heal Vampire_4_NearsightAOE_Source Vampire_Q_Heal_HealPool_AutoActivate Vampire_Q_Heal_HealPool_High
Wraith_Q_NearsightMissile_TrajectoryWarning Wushu_4_SmokeZone Aggrobot_C_ExplodeyPatch Deadeye_E_Slow_Large
Pandemic_AcidMolotov_NewMolotov Phoenix_MolotovFire Sarge_Q_Molotov_Production Thorne_4_SlowField_Production
Aggrobot_RollyPolly Aggrobot_SeekerNade Clay_E_Boomba Guide_Q_PossessableScout Gumshoe_E_PossessableCamera
Hunter_E_Drone Pine_E_RadEater Stealth_4_Decoy_V2 Gumshoe_Q_Cage Wraith_4_Smoke
""".split()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_every_seen_archetype_has_a_style_naming_a_real_ability():
    static = WEBAPP / "app" / "static" / "data"
    agents = json.loads((static / "agents.json").read_text(encoding="utf-8"))
    icons = json.loads((static / "abilities.json").read_text(encoding="utf-8"))
    rows = []
    for key in SEEN_ARCHETYPES:
        code, _, name = key.partition("_")
        rows.append({"kind": "GameObject", "code": code, "name": name, "agent": agents[code]})
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        process.stdout.write(JSON.stringify(JSON.parse(input).map(a => R.abilityStyle(a))));
      });"""
    styles = run_node(script, rows)
    for row, style in zip(rows, styles):
        key = row["code"] + "_" + row["name"]
        assert style.get("ability"), f"{key} has no style"
        assert style["ability"] in icons[style["agent"]], f"{key}: {style['ability']!r} isn't one of {style['agent']}'s"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_line_ends_along_across_and_full():
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const at = {u: 5000, v: 5000, yaw: 0};
        const r = x => x && x.map(p => p.map(Math.round));
        process.stdout.write(JSON.stringify({
          across: r(R.lineEnds(at, {dir: "across", len: 1000}, 0.5)),
          aim: r(R.lineEnds({...at, yaw: 90}, {dir: "along", from: -800, len: 800}, 1)),
          full: r(R.lineEnds(at, {dir: "along", full: true}, 1)),
          noYaw: R.lineEnds({u: 1, v: 1}, {dir: "along", len: 1}, 1)
        }));
      });"""
    got = run_node(script, {})
    assert got["across"] == [[5000, 4750], [5000, 5250]]     # a wall centred on the object, across its yaw
    assert got["aim"] == [[5000, 4200], [5000, 5000]]        # from the caster to where the object spawned
    assert got["full"] == [[-15000, 5000], [25000, 5000]]
    assert got["noYaw"] is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_brief_ability_lingers_a_second_and_fades_after_its_close():
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const pulse = {t0: 10, t1: 10.3}, long = {t0: 10, t1: 20}, open = {t0: 10, t1: null};
        const at = t => R.abilitiesAt([pulse, long, open], t, 90).length;
        const a = (x, t) => Math.round(R.abilityAlpha(x, t) * 100) / 100;
        process.stdout.write(JSON.stringify({
          shown: [at(10.2), at(10.9), at(11.1), at(20.5)],
          pulse: [a(pulse, 10.1), a(pulse, 10.3), a(pulse, 10.65), a(pulse, 11)],
          long: [a(long, 15), a(long, 20)]
        }));
      });"""
    got = run_node(script, {})
    assert got["shown"] == [3, 3, 2, 1]          # the pulse shows until 11 s; the long one until 20 s
    assert got["pulse"] == [0.4, 1, 0.5, 0]      # fades in over 0.25 s, then out from its close to 11 s
    assert got["long"] == [1, 1]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_js_reads_stored_abilities_and_shots_back_from_util():
    from app.replays.extras import rounds_extras, util_entries

    extras = {"abilities": [{"t0": 1.5, "t1": 9.0, "kind": "Zone", "code": "Wraith", "name": "4_Smoke",
                             "agent": "Omen", "slot": 6, "owner_by": "agent", "u": 10, "v": 20}],
              "shots": [{"t": 3.25, "slot": 2, "u": 1, "v": 2, "gun": "Vandal", "n": 1}]}
    util = [{"k": "flash", "t": 1.0, "by": 0, "u": 5, "v": 5, "ability": "x", "targets": []}, *util_entries(extras)]
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const util = JSON.parse(input);
        process.stdout.write(JSON.stringify({extras: R.extrasFromUtil(util), casts: R.castUtil(util).map(u => u.k)}));
      });"""
    got = run_node(script, util)
    assert got["extras"] == rounds_extras(util) == extras
    assert got["casts"] == ["flash"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_badge_reads_the_steps_and_no_helper_shadows_another():
    script = """
      const R = require(process.argv[1]);
      const src = require("fs").readFileSync(process.argv[1], "utf8");
      const names = [...src.matchAll(/^  function (\\w+)\\(/gm)].map(m => m[1]);
      const dupes = names.filter((n, i) => names.indexOf(n) !== i);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const steps = JSON.parse(input);
        process.stdout.write(JSON.stringify({dupes: dupes, found: names.length >= 15, at: [0, 9.9, 10, 30].map(t => R.aliveCountAt(steps, t)),
          life: R.aliveAt([[0, 5, "kill"]], 2, 60) !== null}));
      });"""
    got = run_node(script, [[0.0, 5, 5], [10.0, 4, 5]])
    assert got == {"dupes": [], "found": True, "at": [[5, 5], [5, 5], [4, 5], [4, 5]], "life": True}
