"""Viewer catalog exports and offline, anonymous utility sample harvesting."""

import importlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text

from app.replays import format as fmt

WEBAPP = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_viewer_exports_live_style_tables():
    script = r"""
      const assert = require('node:assert/strict');
      const R = require(process.argv[1]);
      const row = {k: 'ability', kind: 'Zone', code: 'Wraith', name: '4_Smoke'};
      assert.ok(Array.isArray(R.ABILITY_STYLES));
      assert.ok(R.ABILITY_STYLES.every(([re, style]) =>
        re instanceof RegExp && style && typeof style === 'object'));
      const entry = R.ABILITY_STYLES.find(([re]) => re.test('Wraith_4_Smoke'));
      assert.equal(entry[1].r, 410);
      assert.equal(R.abilityStyle(row).r, 410);
      entry[1].r = 999;
      assert.equal(R.abilityStyle(row).r, 999);
      R.UTIL_ABILITY_COLORS['Dark Cover'] = '#123456';
      assert.equal(R.utilColorOf(row, R.abilityStyle(row)), '#123456');
      assert.equal(R.CONTROL_PALETTES['green-red'][0], '#5ccf8f');
      assert.equal(R.UV, 10000);
      assert.ok(R.PROJECTILE_STYLES.some(([re]) => re.test('Grenadier_Q_SemtexBasic')));
    """
    result = subprocess.run(
        [NODE, "-e", script, str(WEBAPP / "app/static/js/replay.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def gallery():
    return importlib.import_module("scripts.harvest_util_samples")


def ability(code="Wraith", name="4_Smoke", kind="Zone", by=0, t=3.0, **extra):
    return {"k": "ability", "t": t, "t1": t + 15, "by": by, "kind": kind,
            "code": code, "name": name, "agent": "Omen", "u": 5120, "v": 4410, **extra}


def round_blob(rows, *, side="A", t_end=100):
    return fmt.encode_blob({
        "v": 1, "t_end": t_end,
        "players": [{"slot": 0, "agent": "Omen", "side": side},
                    {"slot": 1, "agent": "Harbor", "side": "B"}],
        "util": rows,
    })


@pytest.fixture
def sample_db(tmp_path):
    path = tmp_path / "samples.sqlite"
    engine = create_engine("sqlite:///" + path.as_posix())
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE replays (id INTEGER PRIMARY KEY, map_name TEXT, created_at TEXT)"))
        conn.execute(text("CREATE TABLE replay_rounds (replay_id INTEGER, round_number INTEGER, data BLOB)"))
        conn.execute(text("INSERT INTO replays VALUES (:id, :map, :made)"), [
            {"id": 10, "map": "Haven", "made": "2026-11-19"},
            {"id": 20, "map": "Ascent", "made": "2026-11-21"},
            {"id": 30, "map": "Lotus", "made": "2026-11-20"},
        ])
        conn.execute(text("INSERT INTO replay_rounds VALUES (:id, :round, :data)"), [
            {"id": 20, "round": 2, "data": round_blob([ability(t=5)])},
            {"id": 10, "round": 1, "data": round_blob([ability(t=21)])},
            {"id": 30, "round": 1, "data": round_blob([ability(t=11)], side="B")},
            {"id": 20, "round": 1, "data": round_blob([
                ability(), {"k": "shot", "by": 0, "t": 4},
                ability("Mage", "Q_WallManager", "Zone", by=1, t=10),
                ability("Mage", "Q_Wall", "Projectile", by=1, t=10.2,
                        path=[[10.2, 5000, 4000], [10.3, 5100, 4200]]),
                ability("Mage", "Q_Wall", "Projectile", by=1, t=10.8),
                ability("Mage", "Q_Wall", "Projectile", by=0, t=10.1),
                ability("Mage", "Q_Wall", "Projectile", by=1, t=9.5),
                ability("Mage", "Q_Wall", "Projectile", by=1, t=10.5),
                ability("Bomb", "Spike", "Bomb", by=99),
            ])},
        ])
    yield engine, path
    engine.dispose()


def test_harvest_counts_all_rows_but_samples_distinct_replays_first(gallery, sample_db):
    engine, _ = sample_db
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, cur, sql, p, ctx, many: statements.append(sql))
    with engine.connect() as conn:
        result = gallery.harvest(conn, per_key=2)
    assert result["replays"] == 3
    assert datetime.fromisoformat(result["made"]).tzinfo is not None
    key = result["keys"]["Zone|Wraith_4_Smoke"]
    assert (key["count"], key["replays"]) == (4, 3)
    assert [(s["map"], s["row"]["t"], s["side"]) for s in key["samples"]] == [
        ("Ascent", 3, "A"), ("Lotus", 11, "B"),
    ]
    for sample in key["samples"]:
        assert set(sample) == {"map", "t_end", "side", "row", "companions"}
        assert sample["t_end"] == 100
        assert sample["companions"] == []
    assert len(result["keys"]) == 4  # Smoke, manager, projectile and Bomb; no shots.
    assert result["keys"]["Bomb|Bomb_Spike"]["samples"][0]["side"] is None
    assert json.loads(json.dumps(result)) == result
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


def test_harvest_companions_match_owner_and_half_second_window(gallery, sample_db):
    with sample_db[0].connect() as conn:
        result = gallery.harvest(conn)
    sample = result["keys"]["Zone|Mage_Q_WallManager"]["samples"][0]
    assert sample["side"] == "B"
    assert [row["t"] for row in sample["companions"]] == [10.2, 9.5, 10.5]
    assert sample["companions"][0]["path"] == [[10.2, 5000, 4000], [10.3, 5100, 4200]]
    projectiles = result["keys"]["Projectile|Mage_Q_Wall"]["samples"]
    assert all(sample["row"] not in sample["companions"] for sample in projectiles)


@pytest.mark.parametrize("per_key, maps", [
    (3, ["Ascent", "Lotus", "Haven"]),
    (4, ["Ascent", "Lotus", "Haven", "Ascent"]),
])
def test_harvest_fills_remaining_slots_after_distinct_replays(gallery, sample_db, per_key, maps):
    with sample_db[0].connect() as conn:
        result = gallery.harvest(conn, per_key=per_key)
    assert [s["map"] for s in result["keys"]["Zone|Wraith_4_Smoke"]["samples"]] == maps


def test_harvest_limits_newest_replays_and_breaks_date_ties_by_id(gallery, sample_db):
    engine, _ = sample_db
    with engine.connect() as conn:
        result = gallery.harvest(conn, replays=1)
    key = result["keys"]["Zone|Wraith_4_Smoke"]
    assert result["replays"] == 1
    assert (key["count"], key["replays"]) == (2, 1)
    assert [s["row"]["t"] for s in key["samples"]] == [3, 5]
    with engine.begin() as conn:
        conn.execute(text("UPDATE replays SET created_at = '2026-11-21' WHERE id = 30"))
        result = gallery.harvest(conn, replays=1)
    assert result["keys"]["Zone|Wraith_4_Smoke"]["samples"][0]["map"] == "Lotus"


def test_harvest_omits_identity_metadata_from_rows_and_companions(gallery, sample_db):
    engine, _ = sample_db
    private = {"player_name": "omit-me", "match_id": "omit-me", "Subject": "omit-me",
               "account_id": "omit-me", "display_name": "omit-me"}
    with engine.begin() as conn:
        conn.execute(text("UPDATE replay_rounds SET data = :data WHERE replay_id = 20 AND round_number = 1"),
                     {"data": round_blob([
                         ability("Mage", "Q_WallManager", t=10, **private),
                         ability("Mage", "Q_Wall", "Projectile", t=10.2, **private),
                     ])})
        result = gallery.harvest(conn, replays=1)
    sample = result["keys"]["Zone|Mage_Q_WallManager"]["samples"][0]
    assert sample["row"]["name"] == "Q_WallManager"
    assert sample["companions"][0]["name"] == "Q_Wall"
    assert "omit-me" not in json.dumps(result)


@pytest.mark.parametrize("relative", ["samples.json", "new-gallery/samples.json", "../webapp/samples.json", "."])
def test_main_refuses_repo_output_before_opening_database(gallery, monkeypatch, capsys, relative):
    output = WEBAPP / relative
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        gallery.main(["--out", str(output)])
    assert exc.value.code == 2
    assert "repo" in capsys.readouterr().err.lower()
    if output.suffix == ".json":
        assert not output.exists()


def test_main_writes_json_using_database_url(gallery, sample_db, tmp_path, monkeypatch, capsys):
    _, db_path = sample_db
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + db_path.as_posix())
    output = tmp_path / "gallery" / "samples.json"
    assert gallery.main(["--replays", "1", "--per-key", "1", "--out", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["replays"] == 1
    assert len(result["keys"]["Zone|Wraith_4_Smoke"]["samples"]) == 1
    assert capsys.readouterr().out.strip() == f"1 replays, 4 keys -> {output.resolve()}"


GALLERY_SETUP = r"""
const assert = require('node:assert/strict');
const R = require(process.argv[1]);
const G = require(process.argv[2]);
const sample = (code, name, kind = 'Zone', extra = {}) => ({
  map: 'Ascent', t_end: 40, side: 'A', companions: [],
  row: {k: 'ability', by: 3, t: 10, t1: 25, u: 5000, v: 5000,
        code, name, kind, ...extra}
});
const grouped = (...items) => Object.fromEntries(items.map(s => [
  s.row.kind + '|' + s.row.code + '_' + s.row.name, {count: 1, replays: 1, samples: [s]}
]));
const entries = G.galleryEntries(R, {}, {});
const entry = (ability, list = 'ability') => entries.find(e => e.ability === ability && e.list === list);
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-8, `${a} != ${b}`);
"""


def run_node(script: str, payload=None):
    completed = subprocess.run(
        [NODE, "-e", GALLERY_SETUP + script, str(WEBAPP / "app/static/js/replay.js"),
         str(WEBAPP / "scripts/util_gallery/gallery.js")],
        input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_entries_and_first_match_sample_selection():
    run_node(r"""
const smoke = sample('Wraith', '4_Smoke', 'Zone', {agent: 'Omen'});
const hidden = sample('Gumshoe', '4_TripWire_SecondWire');
const fault = sample('Breach', 'E_SweetSpotFissure', 'Zone', {charge: 0.6});
const semtex = sample('Grenadier', 'Q_SemtexBasic', 'Projectile', {
  flight: [[10, 5000, 5000, 0], [11, 5500, 5500, 0]]
});
const unknown = sample('Unknown', 'Ball', 'Projectile');
const samples = grouped(smoke, hidden, fault, semtex, unknown);
const es = G.galleryEntries(R, samples, {Clove: {Ruse: '/icon.png'}});
assert.equal(es.length, R.ABILITY_STYLES.filter(x => x[1].shape !== 'hidden').length
  + R.PROJECTILE_STYLES.length + 1);
assert.equal(new Set(es.map(e => e.id)).size, es.length);
assert.ok(es.every(e => e.shape !== 'hidden'));
assert.equal(es.find(e => e.ability === 'Dark Cover').agent, 'Omen');
assert.equal(es.find(e => e.ability === 'Ruse').agent, 'Clove');
assert.equal(es.find(e => e.ability === 'Aftershock').agent, 'Other');
for (const e of es) {
  const selected = G.entrySamples(R, e, samples);
  if (e.list !== 'unstyled') {
    const table = e.list === 'ability' ? R.ABILITY_STYLES : R.PROJECTILE_STYLES;
    assert.equal(e.style, table[e.index][1]);
    assert.equal(e.pattern, table[e.index][0].source);
    assert.equal(e.id, e.list + ':' + e.pattern);
  }
  assert.ok(!selected.includes(hidden));
  assert.equal(selected.includes(smoke), e.list === 'ability' && e.ability === 'Dark Cover');
  assert.equal(selected.includes(fault), e.list === 'ability' && e.ability === 'Fault Line');
  assert.equal(selected.includes(semtex), e.ability === 'FRAG/ment');
}
const unstyled = es.find(e => e.list === 'unstyled');
assert.equal(unstyled.key, 'Projectile|Unknown_Ball');
assert.equal(unstyled.style, null);
assert.deepEqual(G.entrySamples(R, unstyled, samples), [unknown]);
assert.deepEqual(G.entrySamples(R, entry('Dark Cover'), {keys: samples}), [smoke]);
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_sample_round_uses_companions_for_geometry_only():
    run_node(r"""
const s = sample('Grenadier', 'Q_SemtexBasic', 'Projectile', {
  flight: [[13.57, 4200, 4500, 0], [13.77, 4400, 4600, 0], [13.87, 4700, 4800, 0]]
});
const original = JSON.stringify(s);
const round = G.sampleRound(R, s, 'B');
assert.equal(round.extras.abilities.length, 2);
const [p, zone] = round.extras.abilities;
assert.equal(zone.name, 'Q_SemtexBasic_Zone');
assert.deepEqual([zone.u, zone.v, zone.t0], [4700, 4800, 13.87]);
assert.equal(G.focusRow(R, round, entry('FRAG/ment')), zone);
assert.equal(G.focusRow(R, round, entry('FRAG/ment', 'projectile')), p);
assert.equal(round.blob.players[0].side, 'B');
assert.deepEqual(round.blob.alive[3], [[0, 40]]);
assert.deepEqual(round.blob.util, []);
for (const t of [0, 7, 40]) {
  const at = R.trackAt(round.tracks[3], t);
  assert.ok(at.live); assert.ok(Number.isFinite(at.yaw));
  assert.deepEqual([at.u, at.v], [5000, 5000]);
}
assert.equal(JSON.stringify(s), original);
const wall = sample('Mage', 'Q_WallManager');
wall.companions = [sample('Mage', 'Q_Wall', 'Projectile', {
  t: 10.2, flight: [[10.2, 5000, 5000], [11, 5500, 6000]]
}).row];
const w = G.sampleRound(R, wall);
assert.equal(w.extras.abilities.length, 1);
assert.deepEqual(w.traces[0], wall.companions[0].flight);
const omen = sample('Wraith', '4_Smoke', 'Projectile');
omen.companions = [sample('Wraith', '4_Smoke', 'Projectile', {t: 10.2}).row];
assert.equal(G.sampleRound(R, omen).extras.abilities.length, 1);
// A companion Semtex cannot add an unrelated zone to the focus.
omen.companions.push(s.row);
assert.equal(G.sampleRound(R, omen).extras.abilities.length, 1);
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_phases_follow_viewer_timing_and_visibility():
    run_node(r"""
function check(s, expected, end = 40) {
  const row = R.extrasFromUtil([s.row]).abilities[0], style = R.abilityStyle(row);
  const phases = G.galleryPhases(R, row, style, end);
  assert.deepEqual(phases.map(p => p.name), expected.map(p => p[0]));
  phases.forEach((p, i) => {
    close(p.t, expected[i][1]); assert.ok(Number.isFinite(p.t));
    assert.ok(R.abilitiesAt([row], p.t, end).includes(row));
  });
  return phases;
}
check(sample('Breach', '4_FusionBlast', 'Zone', {t1: 20}),
  [['windup', 11.1], ['blast', 12.35], ['between', 12.5]]);
for (const [t1, t] of [[25, 12], [11, 10.5], [null, 12]])
  check(sample('Wraith', '4_Smoke', 'Zone', {t1}), [['active', t]]);
check(sample('BountyHunter', 'X_WaveForm'), [['windup', 11.775], ['active', 14.05]]);
check(sample('Hunter', '4_ExplosiveBolt_Explosion'), [['blast', 10.15]]);
check(sample('Sequoia', '4_MovingCover', 'Zone', {t1: 15.4}),
  [['windup', 12.7], ['active', 15.35]]);
check(sample('Wraith', '4_Smoke', 'Zone', {gone: 11}), [['active', 10.95]]);
check(sample('Wraith', '4_Smoke'), [['active', 10.45]], 10.5);
check(sample('Grenadier', 'Q_SemtexBasic', 'Projectile', {
  flight: [[13.57, 4200, 4500, 0], [13.77, 4400, 4600, 0], [13.87, 4700, 4800, 0]]
}), [['in flight', 13.77]]);
// The final scheduled pulse at exactly close is retained by the live viewer.
check(sample('Breach', '4_FusionBlast', 'Zone', {t1: 12.8}),
  [['windup', 11.1], ['blast', 12.35], ['between', 12.5]]);
check(sample('Breach', '4_FusionBlast', 'Zone', {t1: 10.5}), [['active', 10.25]]);
const row = R.extrasFromUtil([sample('Wraith', '4_Smoke').row]).abilities[0];
assert.ok(G.galleryPhases(R, {...row, t0: NaN, t1: NaN}, {}, NaN).every(p => Number.isFinite(p.t)));
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_frames_cover_geometry_and_views_stay_on_map():
    run_node(r"""
const row = {u: 5000, v: 5000, yaw: 0};
let f = G.frameOf(R, row, {shape: 'smoke', r: 410}, {}, 1);
assert.deepEqual([f.cx, f.cy], [5000, 5000]); assert.ok(f.half >= 600);
const v = G.viewOf(f, 240);
close(2 * f.half / R.UV * v.k, 0.6);
f = G.frameOf(R, {...row, end: [7000, 6000]}, {shape: 'wire'}, {}, 1);
assert.deepEqual([f.cx, f.cy], [6000, 5500]);
const boxStyle = entry('Nightfall').style;
f = G.frameOf(R, row, boxStyle, {}, 1);
assert.equal(f.cx, 7000); assert.equal(f.cy, 5000);
const capsule = entry('Aftershock').style;
const angled = {...row, yaw: 37};
f = G.frameOf(R, angled, capsule, {}, 1);
for (const [x, y] of R.capsuleEnds(angled, capsule, 1)) {
  assert.ok(Math.abs(x - f.cx) + capsule.r <= f.half + 1e-8);
  assert.ok(Math.abs(y - f.cy) + capsule.r <= f.half + 1e-8);
}
// All harvested point formats, including time-first paths and trace flights.
for (const [field, value] of Object.entries({points: [[8000, 7000]], arms: [[8000, 7000, 10, null]],
  segments: [[8000, 7000, 10, null]], line: [[8000, 7000]],
  path: [[10, 8000, 7000, 0]], flight: [[10, 8000, 7000, 0]]})) {
  f = G.frameOf(R, {...row, [field]: value}, {}, {}, 1);
  assert.deepEqual([f.cx, f.cy], [6500, 6000], field);
}
f = G.frameOf(R, row, {}, {0: [[10, 8000, 7000, 0]]}, 1);
assert.deepEqual([f.cx, f.cy], [6500, 6000]);
f = G.frameOf(R, row, {shape: 'badge', coneLen: 2000, cone: 50}, {}, 1);
assert.ok(f.cx > 5000);
function viewport(frame) {
  const view = G.viewOf(frame, 240), span = R.UV / view.k;
  assert.ok(view.k >= 1);
  const x = view.ox / 240 * R.UV, y = view.oy / 240 * R.UV;
  assert.ok(x >= 0 && y >= 0 && x + span <= R.UV + 1e-8 && y + span <= R.UV + 1e-8);
  return {x, y, span};
}
const cosmic = entry('Astral Form / Cosmic Divide').style;
const whole = viewport(G.frameOf(R, row, cosmic, {}, 1));
close(whole.x, 0); close(whole.span, R.UV);
viewport(G.frameOf(R, {u: 100, v: 100}, {r: 410}, {}, 1));
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_made_up_samples_first_match_representative_keys():
    run_node(r"""
for (const name of ['Ruse', 'GATECRASH', 'Sky Smoke']) {
  const e = entry(name), s = G.madeUpSample(R, e);
  assert.ok(s); assert.equal(s.map, null); assert.equal(s.t_end, 40); assert.equal(s.side, 'A');
  assert.deepEqual(s.companions, []);
  assert.deepEqual([s.row.u, s.row.v, s.row.t, s.row.t1, s.row.kind, s.row.agent],
    [5000, 5000, 5, 25, 'Zone', e.agent]);
  const key = G.representativeKey(R, e);
  assert.equal(s.row.code + '_' + s.row.name, key);
  assert.equal(R.ABILITY_STYLES.findIndex(([re]) => re.test(key)), e.index);
  assert.equal(R.abilityStyle(s.row).ability, name);
}
assert.equal(G.madeUpSample(R, entry('High Tide')), null);
assert.equal(G.madeUpSample(R, entry('FRAG/ment', 'projectile')), null);
// A duplicate later table row must not manufacture an earlier entry's sample.
const first = entry('Poison Cloud');
R.ABILITY_STYLES.push([new RegExp(first.pattern), {...first.style}]);
const later = {...first, index: R.ABILITY_STYLES.length - 1};
assert.equal(G.representativeKey(R, later), null);
assert.equal(G.madeUpSample(R, later), null);
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_edits_keep_raw_style_and_restore_missing_keys():
    run_node(r"""
const e = entry('Dark Cover'), before = G.snapshotStyle(R, e);
assert.equal(G.effectiveBadge(e), false);
assert.ok(Object.hasOwn(before, 'badge')); assert.equal(before.badge, undefined);
G.applyEdit(R, e, {color: '#123456', r: 999, badge: true, look: {kind: 'grain', flicker: 2}});
const current = G.snapshotStyle(R, e);
assert.equal(current.color, '#123456');
const row = G.sampleRound(R, sample('Wraith', '4_Smoke')).extras.abilities[0];
assert.deepEqual(R.abilityStyle(row).look, {kind: 'grain', settings: {flicker: 2}});
current.look.flicker = 8; assert.equal(e.style.look.flicker, 2);
G.resetEntry(R, e, before);
assert.deepEqual(G.snapshotStyle(R, e), before);
assert.ok(!Object.hasOwn(e.style, 'badge'));
const p = entry('FRAG/ment', 'projectile');
G.applyEdit(R, p, {color: '#abcdef', r: 999, badge: false, look: 'grain'});
assert.equal(G.snapshotStyle(R, p).color, '#abcdef'); assert.ok(!Object.hasOwn(p.style, 'r'));
const u = {list: 'unstyled', key: 'Projectile|Unknown_Ball', style: null};
const unchanged = JSON.stringify(u); G.applyEdit(R, u, {color: '#aaaaaa', r: 999});
assert.equal(JSON.stringify(u), unchanged);
assert.deepEqual(G.parseSettings('{"flicker": 2}'), {ok: true, value: {flicker: 2}});
for (const text of ['[1]', '{', 'null', '2', '"text"']) assert.equal(G.parseSettings(text).ok, false);
assert.equal(G.lookFrom('grain', {}), 'grain');
assert.deepEqual(G.lookFrom('grain', {flicker: 2}), {kind: 'grain', flicker: 2});
assert.deepEqual(G.lookFrom('grain', {kind: 'other', flicker: 2}), {kind: 'grain', flicker: 2});
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_storage_shares_colors_and_survives_denied_access():
    run_node(r"""
function storage() {
  const data = new Map();
  return {get length() {return data.size}, key: i => [...data.keys()][i] ?? null,
    getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k)};
}
const cages = entries.filter(e => e.ability === 'Cyber Cage');
for (const order of [cages, [...cages].reverse()]) {
  const s = storage();
  G.saveEdit(s, order[0], {r: 777, look: {kind: 'grain', flicker: 2}, color: '#111111'});
  G.saveNote(s, order[0], 'soft edge');
  G.saveNote(s, order[1], 'second');
  G.saveEdit(s, order[1], {badge: false});
  G.saveColor(s, order[0].ability, '#111111'); G.saveColor(s, order[1].ability, '#222222');
  const loaded = G.loadEdits(s, entries);
  assert.deepEqual(loaded.edits[order[0].id], {r: 777, look: {kind: 'grain', flicker: 2}});
  assert.equal(loaded.notes[order[0].id], 'soft edge');
  assert.deepEqual(loaded.edits[order[1].id], {badge: false});
  for (const e of cages) assert.equal(loaded.colors[e.ability], '#222222');
  assert.equal(s.getItem(G.colorKey('Cyber Cage')), '#222222');
  assert.equal(G.entryKey(order[0]), 'util-gallery:v1:ability:' + order[0].pattern);
  G.clearColor(s, 'Cyber Cage'); assert.deepEqual(G.loadEdits(s, cages).colors, {});
  s.setItem('unrelated', 'keep'); G.clearAll(s); assert.equal(s.length, 1);
}
for (const s of [null, {getItem() {throw Error('denied')}, setItem() {throw Error('denied')},
  removeItem() {throw Error('denied')}, get length() {throw Error('denied')}}]) {
  assert.deepEqual(G.loadEdits(s, entries), {edits: {}, notes: {}, colors: {}});
  G.saveEdit(s, cages[0], {r: 7}); G.saveNote(s, cages[0], 'note'); G.saveColor(s, 'Cyber Cage', '#123456');
  G.clearColor(s, 'Cyber Cage'); G.clearAll(s);
}
const corrupt = storage(); corrupt.setItem(G.entryKey(cages[0]), '{');
corrupt.setItem(G.entryKey(cages[1]), 'null');
assert.deepEqual(G.loadEdits(corrupt, entries), {edits: {}, notes: {}, colors: {}});
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_export_contains_only_raw_changes_and_notes():
    run_node(r"""
const u = {id: 'unstyled:Projectile|Unknown_Ball', list: 'unstyled', key: 'Projectile|Unknown_Ball',
  style: null, ability: 'Ball', agent: 'Other', shape: 'projectile'};
const es = [entry('Dark Cover'), entry('Ruse'), entry('Aftershock'), entry('Sky Smoke'), u];
const originals = Object.fromEntries(es.map(e => [e.id, G.snapshotStyle(R, e)]));
const current = Object.fromEntries(es.map(e => [e.id, G.snapshotStyle(R, e)]));
current[es[1].id].color = '#123456'; current[es[2].id].look = {kind: 'grain', flicker: 2};
const notes = {[es[0].id]: 'soft edge', [u.id]: 'add a style'};
const meta = {made: '2026-11-22T00:00:00.000Z', replay_js_sha256: 'fixture-sha', palette: 'green-red'};
const result = G.exportCritique(R, es, originals, current, notes, meta);
assert.equal(result.entries.length, 4); assert.equal(result.made, meta.made);
assert.equal(result.replay_js_sha256, meta.replay_js_sha256); assert.equal(result.palette, meta.palette);
assert.deepEqual(result.entries[0].after, {});
assert.deepEqual(result.entries[1].after, {color: '#123456'});
assert.deepEqual(result.entries[2].after, {look: {kind: 'grain', flicker: 2}});
assert.deepEqual(result.entries[0].before, originals[es[0].id]);
assert.equal(result.entries[3].list, 'unstyled'); assert.equal(result.entries[3].pattern, u.key);
const prompt = G.critiquePrompt(result);
assert.ok(prompt.startsWith('Apply each after'));
for (const word of ['ABILITY_STYLES', 'PROJECTILE_STYLES', 'UTIL_ABILITY_COLORS', 'UTIL_LOOKS',
  'unstyled', 'test rules', es[0].pattern]) assert.ok(prompt.includes(word), word);
assert.ok(prompt.endsWith('\n\n' + JSON.stringify(result, null, 1)));
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_frame_includes_fast_lane_box_corners():
    run_node(r"""
const e = entries.find(e => e.pattern === '^Sprinter_4_Tunnel$');
assert.equal(e.shape, 'lanes');
for (const [u, v] of [[5000, 5000], [9000, 9000]]) {
  const row = {u, v, yaw: 30};
  const frame = G.frameOf(R, row, e.style, {}, 0.75);
  const corners = R.boxCorners(row, e.style, 0.75);
  assert.equal(corners.length, 4);
  for (const [x, y] of corners) {
    const cx = Math.max(0, Math.min(R.UV, x)), cy = Math.max(0, Math.min(R.UV, y));
    assert.ok(Math.abs(cx - frame.cx) <= frame.half + 1e-8, `missed lane x ${cx}`);
    assert.ok(Math.abs(cy - frame.cy) <= frame.half + 1e-8, `missed lane y ${cy}`);
  }
}
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_frame_includes_turret_tracked_facings():
    run_node(r"""
const style = entries.find(e => e.pattern === '^Killjoy_E_Turret$').style;
for (const extra of [
  {yaws: [[10, 0], [12, 90], [14, 180], [16, 270]]},
  {yaw: 30}, {yaw: 30, yaws: []}, {yaw: 180, yaws: [[10, 0]]}, {},
]) {
  const row = {u: 5000, v: 5000, ...extra};
  const frame = G.frameOf(R, row, style, {}, 0.75);
  const facings = row.yaws && row.yaws.length ? row.yaws.map(p => p[1]) :
    Number.isFinite(row.yaw) ? [row.yaw] : [];
  for (const yaw of facings) {
    const angle = yaw * Math.PI / 180;
    const x = row.u + Math.cos(angle) * style.coneLen * 0.75;
    const y = row.v + Math.sin(angle) * style.coneLen * 0.75;
    assert.ok(Math.abs(x - frame.cx) <= frame.half + 1e-8, `missed cone x for ${yaw}`);
    assert.ok(Math.abs(y - frame.cy) <= frame.half + 1e-8, `missed cone y for ${yaw}`);
  }
  if (!facings.length) assert.deepEqual(frame, {cx: 5000, cy: 5000, half: 600});
  if (row.yaw === 180) assert.ok(frame.cx > 5000, 'track must take precedence over placed yaw');
}
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_fragment_zone_samples_require_recorded_flight():
    run_node(r"""
const noFlight = sample('Grenadier', 'Q_SemtexBasic', 'Projectile');
const emptyFlight = sample('Grenadier', 'Q_SemtexBasic', 'Projectile', {flight: []});
const flying = sample('Grenadier', 'Q_SemtexBasic', 'Projectile', {
  flight: [[13.57, 4200, 4500, 0], [13.77, 4400, 4600, 0], [13.87, 4700, 4800, 0]]
});
const samples = {'Projectile|Grenadier_Q_SemtexBasic': {
  count: 3, replays: 3, samples: [noFlight, emptyFlight, flying]
}};
const zone = entry('FRAG/ment'), projectile = entry('FRAG/ment', 'projectile');
assert.deepEqual(G.entrySamples(R, zone, samples), [flying]);
assert.deepEqual(G.entrySamples(R, projectile, samples), [noFlight, emptyFlight, flying]);
for (const s of G.entrySamples(R, zone, samples))
  assert.ok(G.focusRow(R, G.sampleRound(R, s), zone));
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_unnamed_projectile_uses_key_as_ability():
    run_node(r"""
const unnamed = sample('Unknown', 'Unused', 'Projectile');
delete unnamed.row.name;
const key = 'Projectile|Unknown_Unnamed';
const samples = {[key]: {count: 1, replays: 1, samples: [unnamed]}};
const unstyled = G.galleryEntries(R, samples, {}).filter(e => e.list === 'unstyled');
assert.equal(unstyled.length, 1);
assert.equal(unstyled[0].key, key);
assert.equal(unstyled[0].ability, 'Unknown_Unnamed');
assert.deepEqual(G.entrySamples(R, unstyled[0], samples), [unnamed]);
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_sample_round_side_defaults_to_a_without_owner_side():
    run_node(r"""
for (const side of [null, undefined, 'A', 'B']) {
  const s = {...sample('Wraith', '4_Smoke'), side};
  for (const override of [undefined, null, 'A', 'B']) {
    const actual = G.sampleRound(R, s, override).blob.players[0].side;
    assert.equal(actual, override || side || 'A');
    assert.ok(['A', 'B'].includes(actual));
  }
}
""")
