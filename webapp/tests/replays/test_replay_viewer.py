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


def test_feature_panel_is_ascent_only(condensed):
    import copy
    assert 'data-replay-panel="features"' in standalone.render(condensed.rounds)
    other = copy.deepcopy(condensed.rounds)
    for data in other.values():
        data['map'] = 'Haven'
    assert 'data-replay-panel="features"' not in standalone.render(other)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_feature_panel_seek_rows_escape_labels_and_old_replays_explain_reparse():
    feature = {'key':'ascent_market','name':'<img src=x>', 'initial':'open', 'closing_s':5,
               'events':[{'t':1,'state':'closing'}]}
    script = """
    const R = require(process.argv[1]);
    let text=''; process.stdin.on('data',d=>text+=d).on('end',()=>{
      const viewer=Object.create(R.ReplayViewer.prototype);
      viewer.ui={featuresEvents:{},featuresState:{},featuresStatus:{}};
      viewer.current={blob:{map_features:{v:1,status:'decoded',features:[JSON.parse(text)]}}};
      viewer.layers={features:false}; viewer.t=3.5; viewer.number=1;
      viewer.controlCache={ready:()=>({status:'ok',parsed:{header:{map_features:{keys:['ascent_market']}}}})};
      viewer.renderMapFeatureEvents(); viewer.drawMapFeatures({},1,1,[]);
      const out={events:viewer.ui.featuresEvents.innerHTML,state:viewer.ui.featuresState.innerHTML,
                 status:viewer.ui.featuresStatus.textContent};
      viewer.current={blob:{}}; viewer.renderMapFeatureEvents(); viewer.drawMapFeatures({},1,1,[]);
      out.old=viewer.ui.featuresEvents.innerHTML; out.unavailable=viewer.ui.featuresStatus.textContent;
      process.stdout.write(JSON.stringify(out));
    });"""
    got = run_node(script, feature)
    assert 'data-seek-t="1"' in got['events'] and '1.000s' in got['events']
    assert '&lt;img src=x&gt;' in got['events'] and '<img' not in got['events']
    assert '50% closed (model)' in got['state'] and '<img' not in got['state']
    assert 'Control: includes <img src=x>' in got['status']  # textContent, never HTML
    assert 'parsed again' in got['old'] and 'unavailable' in got['unavailable']


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
    # a projectile is drawn on its own layer since the 2026-10-05 review (item 23), never hidden outright
    assert got["styles"] == ["smoke", "wire", "hidden", "hidden", "projectile", "spike", "badge"]
    assert got["label"] == "Q Thing"
    assert got["at10"] == 4 and got["at95"] == 0 and got["open"] == 4
    assert got["pairs"] == [[1, 3]], "a wire pairs with its own owner's second anchor"
    assert got["signed"] == ["+12", "\u22124", "0", ""]
    assert got["tally"] == {"0": {"k": 1, "d": 0, "a": 0}, "5": {"k": 0, "d": 2, "a": 0}}


def test_hidden_wins_over_a_class_display():
    """The spike panel (`.replay-hud`, display: flex) stayed on screen, showing the last round's defuse,
    after a change of round set it `hidden`: an author `display` beats the browser's own [hidden] rule."""
    import re

    css = (WEBAPP / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert re.search(r"(^|\n)\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}", css)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_sentinel_utility_goes_down_when_its_owner_dies():
    """Placed sentinel utility dies with its owner (the engine's Q71 set): the viewer greys it from the
    owner's death on. Other utility, and a live owner's, stays up."""
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const alive = {"3": [[0, 20, "kill"]], "4": [[0, null, "round_end"]]};
        const util = (code, name, slot) => ({kind: "GameObject", code: code, name: name, slot: slot});
        const down = (a, t) => R.utilDownAt(a, alive, t, 90);
        const trip = util("Gumshoe", "4_TripWire", 3);
        process.stdout.write(JSON.stringify({
          trip: [down(trip, 10), down(trip, 20.5), down(trip, 60)],
          set: [down(util("Gumshoe", "E_PossessableCamera", 3), 30), down(util("Killjoy", "E_Turret", 3), 30),
                down(util("Killjoy", "Q_StealthAlarmbot", 3), 30), down(util("Deadeye", "E_Trap", 3), 30)],
          live_owner: down(util("Gumshoe", "4_TripWire", 4), 60),
          not_sentinel: [down(util("Wraith", "4_Smoke", 3), 30), down(util("Hunter", "E_Drone", 3), 30)],
          no_owner: down(util("Gumshoe", "4_TripWire", null), 30)
        }));
      });"""
    got = run_node(script, {})
    assert got["trip"] == [False, True, True]
    assert got["set"] == [True, True, True, True]
    assert got["live_owner"] is False
    assert got["not_sentinel"] == [False, False]
    assert got["no_owner"] is False


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
Pandemic_4_SmokeZone Pandemic_X_Circular Grenadier_E_SuppressionPulse
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


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_stage5_helpers_spike_defuse_kills_impact_reveals_and_the_wall():
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const kills = [{t: 10, killer: 0, victim: 5, gain: 12, loss: 7, assists: [1, 0, 5]},
                       {t: 20, killer: 6, victim: 1, gain: 9, loss: 4, assists: []},
                       {t: 20.5, killer: 2, victim: 2, gain: 0, loss: 3}];
        // Planted at 30; a 3.8 s attempt (halves it), a tap, then the finishing 3.5 s.
        const spike = {kind: "Bomb", slot: 4, t0: 30, t1: 70,
                       defuses: [[50, 53.8, 6, false], [54, 54.2, 7, false], [60, 63.5, 7, true]]};
        const lone = {kind: "Bomb", slot: 4, t0: 30, t1: null, defuses: [[70, null, 6, false]]};
        const wall = {on: [[5, 12], [20, null]]};
        const reveals = [{t0: 4, t1: 6, slot: 2, target: 8}, {t0: 9, t1: null, slot: 2, target: 7}];
        const at = t => { const s = R.spikeAt([spike], t); return s && {left: s.left, halved: s.halved,
          defusing: s.defusing && [s.defusing.slot, +s.defusing.elapsed.toFixed(2), s.defusing.needed],
          defused: s.defused, exploded: s.exploded}; };
        process.stdout.write(JSON.stringify({
          tally: R.tallyAt(kills, 30), impact: R.impactAt(kills, 15), noSplit: R.impactAt([{t: 1, killer: 0, victim: 1}], 5),
          next: [0, 8.9, 9, 19, 19.5, 25].map(t => R.nextKillTime(kills, t)),
          prev: [0, 9, 9.5, 19, 19.5, 25].map(t => R.prevKillTime(kills, t)),
          spike: [29, 30, 51, 55, 61, 64].map(at),
          boom: [74, 75].map(t => R.spikeAt([lone], t)).map(s => [s.defusing && s.defusing.slot, s.exploded]),
          wall: [4, 5, 12.5, 25].map(t => R.wallUp(wall, t)),
          reveals: [5, 7, 10.5, 11.5].map(t => R.revealsAt(reveals, t).map(r => r.target))
        }));
      });"""
    got = run_node(script, {})
    assert got["tally"] == {"0": {"k": 1, "d": 0, "a": 0}, "1": {"k": 0, "d": 1, "a": 1}, "5": {"k": 0, "d": 1, "a": 0},
                            "6": {"k": 1, "d": 0, "a": 0}, "2": {"k": 0, "d": 1, "a": 0}}
    assert got["impact"] == {"0": 12, "5": -7} and got["noSplit"] is None
    assert got["next"] == [9, 9, 19, 19.5, None, None]
    # Back from a kill's lead-in goes to the kill before it; before the first kill there's nothing.
    assert got["prev"] == [None, None, 9, 9, 19, 19.5]
    assert got["spike"][0] is None
    assert got["spike"][1] == {"left": 45, "halved": False, "defusing": None, "defused": None, "exploded": False}
    assert got["spike"][2]["defusing"] == [6, 1, 7] and not got["spike"][2]["halved"]
    assert got["spike"][3]["halved"] and got["spike"][3]["defusing"] is None
    assert got["spike"][4]["defusing"] == [7, 1, 3.5], "after a half defuse the next needs 3.5 s"
    assert got["spike"][5]["defused"] == {"slot": 7, "t": 63.5} and got["spike"][5]["defusing"] is None
    assert got["boom"] == [[6, False], [None, True]], "a defuse still going at 45 s is cut off by the detonation"
    assert got["wall"] == [False, True, False, True]
    assert got["reveals"] == [[8], [], [7], []]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_pops_show_only_until_they_went_off_and_statuses_read_back():
    script = """
      const R = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
        const saturate = {kind: "GameObject", code: "Terra", name: "C_TimeSlowGrenade_Explosion", t0: 10, t1: 20};
        const mpulse = {kind: "GameObject", code: "Iris", name: "Concuss", t0: 30, t1: 35, fx: [30, 32, 34]};
        const fault = {kind: "GameObject", code: "Breach", name: "E_SweetSpotFissure", t0: 50, t1: 56.1, fx: [50, 51.1]};
        const smoke = {kind: "Zone", code: "Wraith", name: "4_Smoke", t0: 10, t1: 25};
        const wire = {kind: "GameObject", code: "Gumshoe", name: "4_TripWire", t0: 0, t1: 90, gone: 13.5};
        const all = [saturate, mpulse, fault, smoke];
        const util = [{k: "status", t: 5, by: 2, t1: 7, target: 8, code: "Iris", name: "Concuss", status: "concussed", from: "object"}];
        const extras = R.extrasFromUtil(util);
        process.stdout.write(JSON.stringify({
          at: [10.5, 11.5, 33, 34.7, 51.5, 52, 20].map(t => R.abilitiesAt(all, t, 90).map(a => a.name)),
          wire: [13, 14, 14.2].map(t => R.abilitiesAt([wire], t, 90).length),
          until: [R.popUntil(saturate, 1), R.popUntil(mpulse, 0.8), R.popUntil(fault, 0.8)],
          statuses: extras.statuses, on: [4.9, 6, 7.1].map(t => R.statusesAt(extras.statuses, t).length),
          styles: [R.statusStyle("concussed").label, R.statusStyle("gravnet").label]
        }));
      });"""
    got = run_node(script, {})
    assert got["at"] == [["C_TimeSlowGrenade_Explosion", "4_Smoke"], ["4_Smoke"], ["Concuss"], ["Concuss"],
                         ["E_SweetSpotFissure"], [], ["4_Smoke"]]
    assert got["until"] == [11, 34.8, 51.9]
    assert got["wire"] == [1, 1, 0], "a trip that was shot shows its burst, then goes"
    assert got["statuses"] == [{"t0": 5, "slot": 2, "t1": 7, "target": 8, "code": "Iris", "name": "Concuss",
                                "status": "concussed", "from": "object"}]
    assert got["on"] == [0, 1, 0] and got["styles"] == ["CONCUSSED", "GRAVNET"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_clicking_a_round_number_plays_that_round_from_its_start():
    # The strip's buttons, built with a stub document, on a viewer whose showRound only records the call and,
    # like the real one, leaves the round paused once loaded. Any round, the current one too, plays from 0.
    script = """
      const buttons = [];
      global.document = {createElement: () => {
        const b = {attrs: {}, setAttribute(k, v) { this.attrs[k] = v; },
                   addEventListener(type, fn) { this[type] = fn; }};
        buttons.push(b);
        return b;
      }};
      const R = require(process.argv[1]);
      const v = Object.create(R.ReplayViewer.prototype);
      Object.assign(v, {options: {}, rounds: [1, 2, 3], number: 2, t: 31.0, playing: false, shown: [],
                        ui: {strip: {innerHTML: "", appendChild() {}}, prev: {}, next: {}}});
      v.showRound = function (n, t) { this.shown.push([n, t || 0]); this.number = n; this.t = t || 0;
                                      this.playing = false; return Promise.resolve({}); };
      v.updateControls = function () {};
      v.renderStrip();
      buttons[2].click();
      setTimeout(() => {
        const third = {shown: v.shown.slice(), playing: v.playing};
        v.playing = false; v.t = 12.0;
        buttons[1].click();                      // the round already showing
        setTimeout(() => process.stdout.write(JSON.stringify(
          {third: third, current: {shown: v.shown.slice(1), playing: v.playing, t: v.t}})), 0);
      }, 0);"""
    got = run_node(script, None)
    assert got["third"] == {"shown": [[3, 0]], "playing": True}
    assert got["current"] == {"shown": [[2, 0]], "playing": True, "t": 0}


# A viewer without a page: the real prototype, the render methods stubbed, rounds loaded through
# options.loadRound (each a promise the test resolves or rejects by hand).
PLAYBACK_VIEWER = """
      const R = require(process.argv[1]);
      const pending = {};
      const v = Object.create(R.ReplayViewer.prototype);
      for (const name of ["renderStrip", "renderTicks", "renderBanner", "renderFeed", "renderAnalysis",
                          "renderUtilList", "refreshControl", "renderControlTable", "refreshGaps",
                          "updateControls", "draw"]) v[name] = function () {};
      Object.assign(v, {rounds: [1, 2, 3], cache: {}, speed: 1, t: 0, playing: false, current: null, lastFrame: null,
                        options: {loadRound: n => new Promise((ok, fail) => { pending[n] = {ok, fail}; })}});
      const blob = n => ({v: 1, round: n, hz: 16, t_end: 10, tracks: {}, players: [], kills: [], util: []});
      const land = n => { pending[n].ok(blob(n)); return new Promise(r => setTimeout(r, 0)); };
      const frame = (now) => { const raf = global.requestAnimationFrame; global.requestAnimationFrame = () => {};
                               v.tick(now); global.requestAnimationFrame = raf; };
      const state = () => ({number: v.number, playing: v.playing, t: v.t});
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_finished_round_plays_the_next_one_and_the_last_one_stops():
    script = PLAYBACK_VIEWER + """
      (async () => {
        const out = {};
        const first = v.playRound(1); await land(1); await first;
        frame(1000); frame(3000);                     // 2 s played
        out.midway = state();
        frame(20000);                                 // past the end: round 2 is asked for
        out.at_end = state();
        await land(2);
        out.next = state();
        frame(30000); out.first_frame_after_load = v.t;      // the load's wait is never played
        frame(31000); out.second_frame = v.t;
        const last = v.playRound(3); await land(3); await last;
        frame(40000); frame(60000);
        await new Promise(r => setTimeout(r, 0));
        out.final = state();
        process.stdout.write(JSON.stringify(out));
      })();"""
    got = run_node(script, None)
    assert got["midway"] == {"number": 1, "playing": True, "t": 2}
    assert got["at_end"] == {"number": 1, "playing": False, "t": 10}
    assert got["next"] == {"number": 2, "playing": True, "t": 0}
    assert got["first_frame_after_load"] == 0 and got["second_frame"] == 1
    assert got["final"] == {"number": 3, "playing": False, "t": 10}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_manual_choice_or_a_pause_beats_an_automatic_load_in_flight():
    script = PLAYBACK_VIEWER + """
      (async () => {
        const out = {};
        let p = v.playRound(1); await land(1); await p;
        frame(1000); frame(20000);                    // round 1 ends: round 2 is loading
        v.step(1 + 1);                                // the user steps to round 3 meanwhile (paused, as step does)
        await land(3);
        out.stepped = state();
        await land(2);                                // the stale automatic load lands late
        out.after_stale = state();

        p = v.playRound(1); await p;                  // cached now
        frame(50000); frame(70000);                   // round 1 ends again: round 2 (cached) is on its way
        v.toggle();                                   // paused before it shows
        await new Promise(r => setTimeout(r, 0));
        out.paused_during_load = state();
        v.toggle();
        out.then_play = state();
        process.stdout.write(JSON.stringify(out));
      })();"""
    got = run_node(script, None)
    assert got["stepped"] == {"number": 3, "playing": False, "t": 0}
    assert got["after_stale"] == {"number": 3, "playing": False, "t": 0}
    assert got["paused_during_load"] == {"number": 2, "playing": False, "t": 0}
    assert got["then_play"] == {"number": 2, "playing": True, "t": 0}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_round_that_fails_to_load_leaves_the_viewer_stopped_on_the_one_shown():
    script = PLAYBACK_VIEWER + """
      (async () => {
        const p = v.playRound(1); await land(1); await p;
        frame(1000); frame(20000);
        pending[2].fail(new Error("gone"));
        await new Promise(r => setTimeout(r, 0));
        const failed = state();
        v.toggle();                                   // Play again: the same round from its start
        process.stdout.write(JSON.stringify({failed: failed, replay: state()}));
      })();"""
    got = run_node(script, None)
    assert got["failed"] == {"number": 1, "playing": False, "t": 10}
    assert got["replay"] == {"number": 1, "playing": True, "t": 0}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_space_toggles_once_from_anywhere_but_a_field():
    script = PLAYBACK_VIEWER + """
      (async () => {
        const p = v.showRound(1); await land(1); await p;
        const el = (tagName, extra) => Object.assign({tagName, getAttribute: () => null}, extra || {});
        const press = (target, more) => {
          const e = Object.assign({key: " ", code: "Space", target, prevented: 0,
                                   preventDefault() { this.prevented++; }}, more || {});
          const before = v.playing;
          const handled = v.onSpaceDown(e);
          const up = {key: " ", code: "Space", target, prevented: 0, preventDefault() { this.prevented++; }};
          v.onSpaceUp(up);
          const out = {handled, toggled: v.playing !== before, prevented: e.prevented, up_prevented: up.prevented};
          v.playing = false;
          return out;
        };
        const out = {
          body: press(el("BODY")),
          canvas: press(el("CANVAS")),
          play_button: press(el("BUTTON")),
          tab: press(el("DIV", {getAttribute: n => n === "role" ? "tab" : null})),
          layer_checkbox: press(el("INPUT", {type: "checkbox"})),
          scrub: press(el("INPUT", {type: "range"})),
          text: press(el("INPUT", {type: "text"})),
          search: press(el("INPUT", {type: "search"})),
          no_type: press(el("INPUT")),
          textarea: press(el("TEXTAREA")),
          select: press(el("SELECT")),
          editable: press(el("DIV", {isContentEditable: true})),
          held: press(el("BODY"), {repeat: true}),
          ctrl: press(el("BODY"), {ctrlKey: true}),
          other_key: (() => { const e = {key: "a", code: "KeyA", target: el("BODY"), preventDefault() {}};
                              return v.onSpaceDown(e); })(),
        };
        v.current = null;
        out.no_round = press(el("BODY")).toggled;
        process.stdout.write(JSON.stringify(out));
      })();"""
    got = run_node(script, None)
    once = {"handled": True, "toggled": True, "prevented": 1}
    for where in ("body", "canvas"):
        assert got[where] == {**once, "up_prevented": 0}, where
    for where in ("play_button", "tab", "layer_checkbox", "scrub"):
        # the control's own Space action (on keyup) is swallowed, so one press is one toggle
        assert got[where] == {**once, "up_prevented": 1}, where
    for where in ("text", "search", "no_type", "textarea", "select", "editable", "held", "ctrl"):
        assert got[where] == {"handled": False, "toggled": False, "prevented": 0, "up_prevented": 0}, where
    assert got["other_key"] is False and got["no_round"] is False


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_projectiles_their_flights_and_the_new_wall_shapes():
    script = """
      const R = require(process.argv[1]);
      const flying = {kind: "Projectile", code: "Wraith", name: "Q_NearsightMissile", slot: 2, t0: 10, t1: 11.5, u: 100, v: 100,
                      flight: [[10, 100, 100, 30], [11, 300, 100, 30], [11.5, 400, 100, 30]]};
      const still = {kind: "Projectile", code: "Phoenix", name: "E_FlareCurve_Synced_Right", slot: 1, t0: 5, t1: 7, u: 50, v: 60};
      const net = {kind: "Patch", code: "Cable", name: "4_NetToss", slot: 3, t0: 12, t1: 15, u: 0, v: 0,
                   thrown: {t0: 11.2, t1: 12, u: 1, v: 1}};
      const netThrow = {kind: "Projectile", code: "Cable", name: "4_NetToss", slot: 3, t0: 11.2, t1: 12, u: 1, v: 1,
                        flight: [[11.2, 1, 1], [12, 5, 5]]};
      const mesh = {code: "Cable", name: "E_CableJam_Root", u: 0, v: 0, on: [[3, 30]],
                    arms: [[10, 0, 10, null], [0, 10, 10, 8], [-10, 0, 10, null], [0, -10, 10, null]]};
      const sage = {code: "Thorne", name: "E_Wall_Fortifying", segments: [[1, 1, 2, 40], [2, 1, 2, 9], [3, 1, 2, 40]]};
      const shear = {code: "Nox", name: "WallTrap", line: [[0, 0], [10, 0]], raised: [20, 26]};
      process.stdout.write(JSON.stringify({
        style: [R.abilityStyle(flying), R.abilityStyle(still).label, R.abilityStyle(mesh).shape, R.abilityStyle(shear).shape,
                R.abilityStyle({code: "Phoenix", name: "Q_FlameWallManager_Production"}).shape],
        at: [R.projectileAt(flying, 9.9), R.projectileAt(flying, 10.5), R.projectileAt(still, 6), R.projectileAt(still, 7.1)],
        dedupe: [R.throwShownAsProjectile(net, [netThrow]), R.throwShownAsProjectile(net, [])],
        mesh: [R.meshArmsAt(mesh, 2).length, R.meshArmsAt(mesh, 5).length, R.meshArmsAt(mesh, 9).length],
        sage: [R.sageSegmentsAt(sage, 1).length, R.sageSegmentsAt(sage, 5).length, R.sageSegmentsAt(sage, 10).length],
        shear: [R.shearAt(shear, 10), R.shearAt(shear, 21), R.shearAt(shear, 27), R.shearAt({}, 5)],
        layers: R.LAYERS
      }));"""
    got = run_node(script, None)
    assert got["style"][0] == {"label": "Paranoia", "ability": "Paranoia", "shape": "projectile", "width": 650}
    assert got["style"][1:] == ["Curveball", "mesh", "shear", "wall"]
    assert got["at"][0] is None and got["at"][1] == {"u": 200, "v": 100, "moving": True}
    assert got["at"][2] == {"u": 50, "v": 60, "moving": False} and got["at"][3] is None, "no flight: no invented travel"
    assert got["dedupe"] == [True, False]
    assert got["mesh"] == [0, 4, 3]
    assert got["sage"] == [0, 3, 2]
    assert got["shear"] == ["set", "raised", "set", None]
    assert "projectiles" in got["layers"]


def test_the_projectile_layer_is_on_by_default_and_has_a_toggle():
    source = REPLAY_JS.read_text(encoding="utf-8")
    assert "projectiles: true" in source
    template = (WEBAPP / "app" / "templates" / "replays" / "_player.html").read_text(encoding="utf-8")
    assert 'data-replay-layer="projectiles" checked' in template


def test_the_page_listens_for_space_on_the_document_not_only_inside_the_viewer():
    source = REPLAY_JS.read_text(encoding="utf-8")
    assert 'page.addEventListener("keydown", function (e) { self.onSpaceDown(e); });' in source
    assert 'page.addEventListener("keyup", function (e) { self.onSpaceUp(e); });' in source
    assert 'if (e.key === " ") { e.preventDefault(); self.toggle(); }' not in source      # one listener, not two
