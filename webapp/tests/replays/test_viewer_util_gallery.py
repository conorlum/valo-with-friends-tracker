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


def test_render_inlines_site_map_scales(tiny_samples):
    import re
    from app.services.replays import _uv_per_unit

    renderer = importlib.import_module("scripts.render_util_gallery")
    html = renderer.render(tiny_samples)
    data = json.loads(re.search(r'id="gallery-payload">(.*?)</script>', html, re.S).group(1))
    assert data["mapScales"] == _uv_per_unit()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_sample_scale_matches_linked_drawing_and_framing():
    from app.services.replays import _uv_per_unit

    run_node(GALLERY_DRAW + r"""
payload.mapScales = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const cases = [
  ['Wraith', '4_Smoke'], ['Guide', '4_Heal_AOE'], ['Breach', '4_FusionBlast'],
  ['Sequoia', '4_MovingCover'], ['Thorne', 'E_Wall_Fortifying'],
  ['Deadeye', 'E_Trap'], ['Killjoy', 'E_Turret'], ['Wraith', 'Q_NearsightMissile', 'Projectile']
];
assert.ok(R.projectileStyle(sample('Wraith', 'Q_NearsightMissile', 'Projectile').row).width);
for (const [code, name, kind = 'Zone'] of cases) {
  const s = sample(code, name, kind, {yaw: 55, flight: [[10, 5000, 5000], [14, 6500, 5100]]});
  const e = G.galleryEntries(R, grouped(s), {}).find(e => G.entrySamples(R, e, grouped(s)).length);
  const d = drawing(e, s), viewer = d.viewer;
  const original = {name: viewer.nameOf(3), side: viewer.sideColor(3), owner: viewer.ownerColor(3),
                    slot: viewer.slotColor(3, 'A')};
  // Reuse the same tile, including a switch with no map-image URL change.
  for (const map of ['Bind', 'Ascent', 'Haven', 'Lotus', null, 'Bind']) {
    G.setTileSample(R, viewer, {...s, map});
    const unit = map ? payload.mapScales[map] : 0.75;
    assert.deepEqual(viewer.linked.players, {});
    close(viewer.uvRadius(410), 410 * unit);
    assert.deepEqual({name: viewer.nameOf(3), side: viewer.sideColor(3), owner: viewer.ownerColor(3),
                      slot: viewer.slotColor(3, 'A')}, original);
    const row = G.focusRow(R, viewer.current, e);
    const style = kind === 'Projectile' ? R.projectileStyle(row) : R.abilityStyle(row);
    const phase = G.galleryPhases(R, row, style, s.t_end).slice(-1)[0];
    d.log.length = 0;
    G.drawTile(R, viewer, d.canvas, phase);
    const actual = JSON.parse(JSON.stringify(d.log));
    assert.deepEqual(viewer.view, G.viewOf(G.frameOf(R, row, style, viewer.current.traces, unit), 1024));
    // Run the site's drawing methods on a linked viewer with the same viewport.
    const site = Object.create(R.ReplayViewer.prototype);
    Object.assign(site, viewer, {linked: {players: {}, uvPerUnit: unit}});
    const log = [], ctx = mockCtx(log), scale = 1024 / R.UV;
    const radius = 1024 / 48 / Math.sqrt(site.view.k);
    ctx.setTransform(...R.viewMatrix(site.view, 0, 1024).map(v => v * 480 / 1024));
    ctx.fillStyle = '#1b1d24';   // the tile's background fill leaves this as its fill state
    site.drawAbilities(ctx, scale, radius, []);
    if (e.list !== 'ability') site.drawProjectiles(ctx, scale, radius, []);
    assert.deepEqual(actual.filter(x => !['drawImage', 'fillRect'].includes(x.op)),
      JSON.parse(JSON.stringify(log)).filter(x => !['drawImage', 'fillRect'].includes(x.op)), code + '/' + map);
  }
}
close(410 * payload.mapScales.Bind, 241.9);
""", _uv_per_unit())


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_faint_phases_choose_peak_drawn_alpha():
    run_node(r"""
for (const [code, name] of [['Guide', '4_Heal_AOE'], ['Wraith', '4_Smoke'],
                           ['Deadeye', 'E_Trap'], ['Sequoia', '4_MovingCover']]) {
  for (const life of [0, 0.001, 0.01, 0.049, 0.05, 0.051]) {
    for (const limits of [{}, {gone: 10}, {end: 10.02}, {gone: 10.01, end: 10.1}]) {
      const row = R.extrasFromUtil([sample(code, name, 'Zone', {t1: 10 + life, ...limits}).row]).abilities[0];
      const end = limits.end || 40, phases = G.galleryPhases(R, row, R.abilityStyle(row), end);
      let best = 0;
      for (let i = 0; i <= 4000; i++) {
        const t = 10 + i / 4000 * Math.min(2, end - 10);
        if (R.abilitiesAt([row], t, end).includes(row)) best = Math.max(best, R.abilityAlpha(row, t));
      }
      for (const p of phases) {
        assert.ok(Number.isFinite(p.t) && p.t <= end);
        assert.ok(R.abilitiesAt([row], p.t, end).includes(row));
        assert.ok(R.abilityAlpha(row, p.t) >= best - 0.005, `${code} ${life} ${JSON.stringify(limits)}: ${p.t}`);
      }
    }
  }
}
const s = sample('Guide', '4_Heal_AOE', 'Zone', {t1: 10.01});
const row = R.extrasFromUtil([s.row]).abilities[0];
assert.ok(R.abilityAlpha(row, G.galleryPhases(R, row, R.abilityStyle(row), 40)[0].t) > 0.75);
// A round with no positive-alpha instant keeps the original finite time.
assert.deepEqual(G.galleryPhases(R, row, R.abilityStyle(row), 10), [{name: 'active', t: 10}]);
// Projectiles keep their original timing even when their abilityAlpha is zero.
const projectile = R.extrasFromUtil([sample('Grenadier', 'Q_SemtexBasic', 'Projectile',
  {t1: 10, flight: [[10, 5000, 5000]]}).row]).abilities[0];
assert.equal(G.galleryPhases(R, projectile, R.projectileStyle(projectile), 40)[0].t, 10);
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_frames_include_intercepts_and_complete_cone_arcs():
    run_node(GALLERY_DRAW + r"""
function fits(frame, x, y) {
  const view = G.viewOf(frame, 1024), span = R.UV / view.k;
  const left = view.ox / 1024 * R.UV, top = view.oy / 1024 * R.UV;
  assert.ok(x >= left - 1e-8 && x <= left + span + 1e-8, `clipped x=${x}`);
  assert.ok(y >= top - 1e-8 && y <= top + span + 1e-8, `clipped y=${y}`);
}
const s = sample('Pine', 'E_RadEater', 'GameObject', {active: 11, intercepts: [[11.8, 6300, 5000, 0]]});
const d = drawing(entry('Interceptor'), s);
G.drawTile(R, d.viewer, d.canvas, {t: 12});
assert.ok(d.log.some(x => x.op === 'stroke' && x.path.some(p => p[0] === 'L' && p[1] === 6300 * 1024 / R.UV)));
for (const intercepts of [s.row.intercepts, [[11.8, 3700, 5000]], [[11.8, 5000, 6300]], [[11.8, 5000, 3700]]]) {
  const row = {...s.row, intercepts};
  const frame = G.frameOf(R, row, entry('Interceptor').style, {}, 0.75);
  intercepts.forEach(hit => fits(frame, hit[1], hit[2]));
}
for (const unit of [0.59, 0.7, 0.75, 1]) {
  for (const yaw of [55, -55, 145, 235, 325, 360, 415]) {
    for (const track of [false, true]) {
      const row = {u: 5000, v: 5000, yaw: track ? 180 : yaw, ...(track ? {yaws: [[10, yaw], [12, yaw + 25]]} : {})};
      for (const style of [entry('TURRET').style, {shape: 'cone', angle: 160, r: 2000}]) {
        const frame = G.frameOf(R, row, style, {}, unit);
        const facings = style.coneLen && track ? row.yaws.map(p => p[1]) : [row.yaw];
        for (const facing of facings) {
          const half = style.coneLen ? style.cone : style.angle / 2;
          const length = (style.coneLen || style.r) * unit;
          for (let a = facing - half; a <= facing + half; a += 0.5) {
            fits(frame, row.u + Math.cos(a * Math.PI / 180) * length,
                        row.v + Math.sin(a * Math.PI / 180) * length);
          }
        }
      }
    }
  }
}
""")


@pytest.mark.parametrize("git_mode", ["listed", "missing", "failed", "timeout"])
def test_harvest_refuses_primary_and_linked_checkout_outputs_before_db(
        gallery, monkeypatch, tmp_path, capsys, git_mode):
    primary = tmp_path / "primary checkout"
    primary.mkdir()
    (primary / ".git").mkdir()
    root = primary / ".claude/worktrees/gallery"
    root.mkdir(parents=True)
    (root / ".git").write_text("gitdir: unused", encoding="utf-8")
    sibling = tmp_path / "linked sibling"
    sibling.mkdir()
    monkeypatch.setattr(gallery, "REPO_ROOT", root)
    monkeypatch.chdir(primary)

    def worktrees(command, **kwargs):
        assert command == ["git", "-C", str(root), "worktree", "list", "--porcelain"]
        if git_mode == "missing":
            raise FileNotFoundError("git")
        if git_mode == "timeout":
            raise subprocess.TimeoutExpired(command, 5)
        return subprocess.CompletedProcess(command, 1 if git_mode == "failed" else 0,
                                           f"worktree {primary.as_posix()}\n\nworktree {root.as_posix()}\n\n"
                                           f"worktree {sibling.as_posix()}\n", "")

    monkeypatch.setattr(subprocess, "run", worktrees)
    monkeypatch.setattr(gallery, "create_engine", lambda *_: pytest.fail("database opened"))
    monkeypatch.setenv("DATABASE_URL", "unused")
    outputs = [Path("webapp/samples.json"), primary / "other.json", root / "samples.json"]
    if git_mode == "listed":
        outputs += [sibling / "samples.json", sibling / "nested/samples.json"]
    for output in outputs:
        with pytest.raises(SystemExit) as exc:
            gallery.main(["--out", str(output)])
        assert exc.value.code == 2
        assert "repo" in capsys.readouterr().err.lower()
        assert not output.exists()


def test_harvest_allows_output_in_an_unrelated_git_repository(gallery, sample_db, tmp_path, monkeypatch):
    _, db_path = sample_db
    other = tmp_path / "unrelated"
    other.mkdir()
    (other / ".git").mkdir()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + db_path.as_posix())
    output = other / "samples.json"
    assert gallery.main(["--replays", "0", "--out", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["replays"] == 0


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


@pytest.fixture
def tiny_samples():
    return {"replays": 1, "keys": {
        "Zone|Wraith_4_Smoke": {"count": 1, "replays": 1, "samples": [
            {"map": "Ascent", "t_end": 40, "side": "A", "companions": [], "row": ability()}]},
        "Projectile|Grenadier_Q_SemtexBasic": {"count": 1, "replays": 1, "samples": [
            {"map": "Ascent", "t_end": 40, "side": "B", "companions": [], "row": ability(
                code="Grenadier", name="Q_SemtexBasic", kind="Projectile", agent="KAY/O",
                flight=[[3, 5000, 4000, 0], [4, 5100, 4100, 0]])}]},
    }}


def test_render_util_gallery_is_self_contained(tiny_samples):
    import hashlib
    import re

    renderer = importlib.import_module("scripts.render_util_gallery")
    html = renderer.render(tiny_samples)
    for path in ("app/static/css/style.css", "app/static/js/replay.js", "scripts/util_gallery/gallery.js"):
        assert (WEBAPP / path).read_text(encoding="utf-8").replace("</", "<\\/") in html
    data = json.loads(re.search(r'<script type="application/json" id="gallery-payload">(.*?)</script>',
                                html, re.S).group(1))
    assert data["samples"] == tiny_samples
    assert data["maps"]["Ascent"].startswith("data:image/png;base64,")
    assert data["icons"]["Omen"].startswith("data:image/png;base64,")
    assert data["abilityIcons"] == json.loads((WEBAPP / "app/static/data/abilities.json").read_text())
    assert all(value.startswith("data:image/png;base64,") for value in data["assets"].values())
    assert data["replay_js_sha256"] == hashlib.sha256(
        (WEBAPP / "app/static/js/replay.js").read_text(encoding="utf-8").encode()).hexdigest()
    assert not re.search(r'(?:src|href)=[\"\']https?://', html)
    for control in ("palette", "side", "agent", "filter", "edited", "export", "copy", "clear"):
        assert f'data-header="{control}"' in html


def test_render_util_gallery_preserves_literal_text_without_script_breakout(tiny_samples, monkeypatch):
    renderer = importlib.import_module("scripts.render_util_gallery")
    tiny_samples["keys"]["</script><script>bad()</script>{{literal}}"] = {"samples": []}
    read_text = Path.read_text

    def read(path, *args, **kwargs):
        value = read_text(path, *args, **kwargs)
        return value + '\n// </script>{{literal}}\n' if path.name in ("replay.js", "gallery.js") else value

    monkeypatch.setattr(Path, "read_text", read)
    html = renderer.render(tiny_samples)
    assert "</script><script>bad()" not in html
    assert '<\\/script><script>bad()<\\/script>{{literal}}' in html
    assert html.count('// <\\/script>{{literal}}') == 2


def test_render_util_gallery_main_writes_file_and_reports_missing_samples(tiny_samples, tmp_path, capsys):
    renderer = importlib.import_module("scripts.render_util_gallery")
    source, output = tmp_path / "samples.json", tmp_path / "nested/g.html"
    source.write_text(json.dumps(tiny_samples), encoding="utf-8")
    assert renderer.main(["--samples", str(source), "--out", str(output)]) == 0
    assert output.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert capsys.readouterr().out.strip() == str(output.resolve())
    with pytest.raises(SystemExit) as exc:
        renderer.main(["--samples", str(tmp_path / "missing.json")])
    assert exc.value.code != 0
    assert "missing.json" in capsys.readouterr().err


# Recording mockCtx copied from test_viewer_util_style.py; no DOM, browser or server.
GALLERY_DRAW = r"""
global.getComputedStyle = () => ({getPropertyValue: () => ""});
global.Image = class { constructor() { this.complete = false; } };
function mockCtx(log) {
  function gradient(kind, args) {
    const g = {kind, args, stops: [], addColorStop(at, color) { this.stops.push([at, color]); }};
    log.push({op: "gradient", gradient: g}); return g;
  }
  const keys = ["fillStyle", "strokeStyle", "globalAlpha", "font", "lineWidth", "textAlign", "textBaseline",
                "lineDashOffset", "globalCompositeOperation", "lineCap", "lineJoin", "dash"];
  let path = [], stack = [], tf = [1, 0, 0, 1, 0, 0];
  const c = {fillStyle: "#000", strokeStyle: "#000", globalAlpha: 1, font: "10px sans-serif", lineWidth: 1,
             textAlign: "start", textBaseline: "alphabetic", lineDashOffset: 0, dash: []};
  const snap = () => ({fill: c.fillStyle, stroke: c.strokeStyle, alpha: c.globalAlpha, font: c.font,
                      tf: tf.slice(), dash: c.dash.slice(), offset: c.lineDashOffset, lineWidth: c.lineWidth});
  const rec = (op) => { log.push(Object.assign({op, path: path.slice()}, snap())); };
  Object.assign(c, {
    save() { const o = {tf: tf.slice()}; keys.forEach(k => { o[k] = c[k]; }); stack.push(o); },
    restore() { const o = stack.pop(); if (!o) return; tf = o.tf; keys.forEach(k => { c[k] = o[k]; }); },
    setTransform(a, b, cc, d, e, f) { tf = [a, b, cc, d, e, f]; },
    beginPath() { path = []; }, closePath() { path.push(["Z"]); },
    moveTo(x, y) { path.push(["M", x, y]); }, lineTo(x, y) { path.push(["L", x, y]); },
    arc(x, y, r, a0, a1) { path.push(["A", x, y, r, a0, a1]); }, rect(x, y, w, h) { path.push(["R", x, y, w, h]); },
    fill() { rec("fill"); }, stroke() { rec("stroke"); },
    fillRect(x, y, w, h) { log.push(Object.assign({op: "fillRect", x, y, w, h}, snap())); },
    strokeRect(x, y, w, h) { log.push(Object.assign({op: "strokeRect", x, y, w, h}, snap())); },
    fillText(text, x, y) { log.push(Object.assign({op: "fillText", text: String(text), x, y}, snap())); },
    measureText(text) {
      const m = /(\d+(?:\.\d+)?)px/.exec(c.font); const px = m ? parseFloat(m[1]) : 10;
      return {width: String(text).length * px * 0.6};
    },
    setLineDash(dash) { c.dash = dash.slice(); }, getLineDash() { return c.dash.slice(); },
    clip() { rec("clip"); }, clearRect() {},
    createRadialGradient(...args) { return gradient("radial", args); },
    createLinearGradient(...args) { return gradient("linear", args); },
    createPattern() { log.push({op: "createPattern"}); return "noise-pattern"; },
    drawImage(img, x, y, w, h) { log.push(Object.assign({op: "drawImage", src: img.src, x, y, w, h}, snap())); }
  });
  return new Proxy(c, {get: (o, k) => (k in o ? o[k] : () => {})});
}
const payload = {maps: {Ascent: 'data:image/png;base64,bWFw'}, assets: {'/smoke.png': 'data:image/png;base64,aWNvbg'},
  abilityIcons: {Omen: {'Dark Cover': '/smoke.png'}}};
function drawing(e, s) {
  const log = [], ctx = mockCtx(log), canvas = {width: 480, height: 480, getContext: () => ctx};
  const tile = {entry: e, sample: s, canvases: []};
  const viewer = G.tileViewer(R, tile, payload);
  if (viewer.map) Object.assign(viewer.map, {complete: true, naturalWidth: 100});
  const row = G.focusRow(R, viewer.current, e);
  const phase = G.galleryPhases(R, row, row.kind === 'Projectile' ? R.projectileStyle(row) : R.abilityStyle(row), s.t_end)[0];
  tile.canvases.push({canvas, phase});
  viewer.draw();
  return {viewer, tile, canvas, phase, log};
}
"""

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_tile_draws_at_the_viewers_1024_scale_on_a_smaller_canvas():
    run_node(GALLERY_DRAW + r"""
const d = drawing(entry('Dark Cover'), sample('Wraith', '4_Smoke', 'Zone', {agent: 'Omen'}));
const k = d.viewer.view.k, r = 1024 / 48 / Math.sqrt(k);
// The map covers the logical 1024 square, under the transform scaled to the 480 px backing canvas.
const first = d.log[0];
assert.equal(first.op, 'drawImage'); assert.deepEqual([first.x, first.y, first.w, first.h], [0, 0, 1024, 1024]);
const expected = R.viewMatrix(d.viewer.view, 0, 1024).map(v => v * 480 / 1024);
first.tf.forEach((v, i) => close(v, expected[i]));
// The smoke is 410 * 0.75 world units at the viewer's 1024 px scale, its timer ring as thin as the viewer's.
const arcs = d.log.flatMap(x => (x.path || []).filter(p => p[0] === 'A').map(p => p[3]));
assert.ok(arcs.some(a => Math.abs(a - 410 * 0.75 * (1024 / 10000)) < 1e-6), arcs.join());
assert.ok(d.log.some(x => x.op === 'stroke' && Math.abs(x.lineWidth - Math.max(2.5, r / 4.5)) < 1e-8));
// viewBounds and friends see the viewer's size during a draw.
let seen = null;
d.viewer.drawAbilities = function () { seen = [this.canvas.width, this.canvas.height]; };
G.drawTile(R, d.viewer, d.canvas, d.phase);
assert.deepEqual(seen, [1024, 1024]);
""")

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_tile_draws_glass_and_owns_icon_redraw():
    run_node(GALLERY_DRAW + r"""
const d = drawing(entry('Dark Cover'), sample('Wraith', '4_Smoke', 'Zone', {agent: 'Omen'}));
assert.equal(Object.getPrototypeOf(d.viewer), R.ReplayViewer.prototype);
assert.notEqual(d.viewer.draw, R.ReplayViewer.prototype.draw);
assert.equal(d.log[0].op, 'drawImage');
assert.ok(d.log.some(x => x.op === 'fillRect' && x.fill.kind === 'radial'));
assert.equal(d.viewer.view.k, G.viewOf(G.frameOf(R, G.focusRow(R, d.viewer.current, d.tile.entry),
  R.abilityStyle(G.focusRow(R, d.viewer.current, d.tile.entry)), d.viewer.current.traces, 0.75), 240).k);
const icons = d.viewer.icons;
d.viewer.abilityIcon('Omen', 'Dark Cover');
const icon = icons['/smoke.png'];
assert.equal(icon.src, payload.assets['/smoke.png']);
Object.assign(icon, {complete: true, naturalWidth: 32});
d.tile.entry.style.badge = true;
delete d.viewer.canvas;
d.log.length = 0;
icon.onload();
assert.equal(d.viewer.icons, icons);
assert.ok(d.log.some(x => x.op === 'drawImage' && x.src === icon.src));
d.viewer.layers.abilities = d.viewer.layers.projectiles = false;
d.log.length = 0; d.viewer.draw();
assert.deepEqual(d.log.map(x => x.op), ['drawImage']);
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("code,name,ability_name", [
    ("Grenadier", "X_UltPulseManager", "NULL/cmd"), ("Phoenix", "X_ResTarget", "Run it Back"),
])
def test_gallery_tile_draws_carried_timer(code, name, ability_name):
    run_node(GALLERY_DRAW + r"""
const p = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const e = entries.find(e => e.pattern === R.ABILITY_STYLES.find(([re]) => re.test(p.code + '_' + p.name))[0].source);
assert.equal(e.ability, p.ability);
const d = drawing(e, sample(p.code, p.name));
const r = 1024 / 48 / Math.sqrt(d.viewer.view.k), timerRadius = r * 1.25 + Math.max(2.5, r / 4) / 2;
assert.ok(d.log.some(x => x.op === 'stroke' && x.path.some(p => p[0] === 'A' && Math.abs(p[3] - timerRadius) < 1e-8)));
d.viewer.layers.abilities = false; d.log.length = 0; d.viewer.draw();
assert.deepEqual(d.log.map(x => x.op), ['drawImage']);
""", {"code": code, "name": name, "ability": ability_name})


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_tile_draws_high_tide_companion_trace():
    run_node(GALLERY_DRAW + r"""
const s = sample('Mage', 'Q_WallManager');
s.companions = [sample('Mage', 'Q_Wall', 'Projectile', {t: 10.2,
  flight: [[10.2, 5000, 5000], [11, 5500, 6000]]}).row];
const d = drawing(entry('High Tide'), s);
assert.ok(d.log.some(x => x.op === 'stroke' && x.path.some(p => p[0] === 'M' && Math.abs(p[1] - 512) < 1e-6 && Math.abs(p[2] - 512) < 1e-6) &&
  x.path.some(p => p[0] === 'L' && Math.abs(p[1] - 563.2) < 1e-6 && Math.abs(p[2] - 614.4) < 1e-6)));
assert.equal(d.viewer.current.extras.abilities.length, 1);
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_tile_projectiles_background_and_zoom_radius():
    run_node(GALLERY_DRAW + r"""
const s = sample('Grenadier', 'Q_SemtexBasic', 'Projectile', {flight: [[10, 5000, 5000], [11, 5500, 6000]]});
const d = drawing(entry('FRAG/ment', 'projectile'), s);
assert.ok(d.log.some(x => x.op === 'fill' && x.path.some(p => p[0] === 'A')));
const calls = [];
d.viewer.drawAbilities = (ctx, s, r, hits) => { calls.push(['abilities', r, hits]); hits.push('used'); };
d.viewer.drawProjectiles = (ctx, s, r, hits) => { calls.push(['projectiles', r, hits]); hits.push('used'); };
// A frame of half 750 yields span 2500 and k=4 with the actual frame/view helpers.
d.viewer.current.extras.abilities[0].flight = [[10, 5000, 5000], [11, 6500, 5000]];
G.drawTile(R, d.viewer, d.canvas, d.phase);
close(d.viewer.view.k, 4);
close(calls.find(x => x[0] === 'projectiles')[1], 1024 / 48 / 2);
assert.notEqual(calls[0][2], calls[1][2]);
calls.length = 0; d.viewer.layers.projectiles = false; d.log.length = 0; d.viewer.draw();
assert.equal(calls.length, 1); // abilities still enabled
assert.deepEqual(d.log.map(x => x.op), ['drawImage']);
d.viewer.layers.abilities = false; d.viewer.map = null;
d.log.length = 0; d.viewer.draw();
assert.deepEqual(d.log.map(x => [x.op, x.fill]), [['fillRect', '#1b1d24']]);
""")


def test_gallery_renderer_is_guarded_by_replay_web():
    from scripts import test_catalog as tc

    assert "scripts/render_util_gallery.py" in tc.load_catalog()["replay-web"]["guards"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_tile_guards_missing_focus_and_draws_unstyled_projectile():
    run_node(GALLERY_DRAW + r"""
const s = sample('Grenadier', 'Q_SemtexBasic', 'Projectile');
const zone = entries.find(e => e.pattern === '^Grenadier_Q_SemtexBasic_Zone$');
const log = [], canvas = {width: 480, height: 480, getContext: () => mockCtx(log)};
const tile = {entry: zone, sample: s, canvases: [{canvas, phase: {name: 'active', t: 12}}]};
const v = G.tileViewer(R, tile, payload);
assert.equal(G.focusRow(R, v.current, zone), undefined);
Object.assign(v.map, {complete: true, naturalWidth: 100}); v.draw();
assert.deepEqual(log.map(x => x.op), ['drawImage']);
const unknown = sample('Unknown', 'Unstyled', 'Projectile', {agent: 'Omen'});
const e = G.galleryEntries(R, grouped(unknown), {}).find(e => e.list === 'unstyled');
const d = drawing(e, unknown);
assert.ok(d.log.some(x => x.op === 'fill' && x.path.some(p => p[0] === 'A')));
d.viewer.layers.projectiles = false; d.log.length = 0; d.viewer.draw();
assert.deepEqual(d.log.map(x => x.op), ['drawImage']);
""")


GALLERY_DOM = r"""
// Only the DOM operations used by mount; canvas recording remains offline.
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attrs = {}; this.listeners = {};
    this.value = ''; this.checked = false; this.style = {}; this.hidden = false; this._text = ''; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) { this.children.push(c); c.parentNode = this; return c; }
  replaceChildren(...items) { this.children = []; items.forEach(c => this.appendChild(c)); }
  remove() { this.parentNode.children = this.parentNode.children.filter(c => c !== this); }
  addEventListener(k, fn) { this.listeners[k] = fn; }
  dispatch(k) { return this.listeners[k]({target: this}); }
  click() { return this.listeners.click && this.dispatch('click'); }
  focus() {} select() {}
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  querySelectorAll(selector) {
    const m = /^\[([^=\]]+)(?:="(.*)")?\]$/.exec(selector);
    const matches = c => m ? Object.hasOwn(c.attrs, m[1]) && (m[2] === undefined || c.attrs[m[1]] === m[2]) : c.tagName === selector;
    return this.children.flatMap(c => (matches(c) ? [c] : []).concat(c.querySelectorAll(selector)));
  }
  querySelector(s) { return this.querySelectorAll(s)[0] || null; }
  getContext() { return mockCtx([]); }
}
function page() {
  const body = new Element('body'), root = new Element('main');
  body.appendChild(root); root.setAttribute('data-gallery', '');
  const doc = {body, createElement: tag => new Element(tag), querySelector: s => body.querySelector(s),
    execCommand: op => { doc.copied = op; return true; }};
  ['palette', 'side', 'agent', 'filter', 'edited', 'export', 'copy', 'clear', 'status'].forEach(k => {
    const el = doc.createElement('input'); el.setAttribute('data-header', k); body.appendChild(el);
    if (k === 'side') el.value = 'Sample';
  });
  const map = new Map(), storage = {get length() {return map.size;}, key: i => [...map.keys()][i],
    getItem: k => map.get(k) || null, setItem: (k, v) => map.set(k, v), removeItem: k => map.delete(k)};
  return {doc, root, storage, header: k => doc.querySelector('[data-header="' + k + '"]')};
}
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_mount_controls_preserve_edits_share_color_reset_and_filter():
    run_node(GALLERY_DRAW + GALLERY_DOM + r"""
const p = page(), samples = grouped(sample('Wraith', '4_Smoke', 'Zone', {agent: 'Omen'}));
samples['Zone|Wraith_4_Smoke'].samples.push(sample('Wraith', '4_Smoke', 'Zone', {u: 6000}));
const e = entry('Dark Cover');
G.saveEdit(p.storage, e, {r: 555}); G.saveNote(p.storage, e, 'review');
G.saveColor(p.storage, e.ability, '#123456');
const state = G.mount(R, p.doc, {...payload, samples}, p.storage);
const cages = state.tiles.filter(t => t.entry.ability === 'Cyber Cage');
assert.ok(cages.length >= 2);
const redraws = new Map();
cages.forEach(t => { t.viewer.draw = () => redraws.set(t.entry.id, (redraws.get(t.entry.id) || 0) + 1); });
cages[0].controls.color.value = '#654321'; cages[0].controls.color.dispatch('input');
cages.forEach(t => { assert.equal(t.controls.color.value, '#654321'); assert.equal(redraws.get(t.entry.id), 1); });
assert.equal(p.storage.getItem(G.colorKey('Cyber Cage')), '#654321');
assert.ok(state.tiles.filter(t => t.entry.list === 'projectile').every(t =>
  !t.controls.r && !t.controls.badge && !t.controls.look && t.controls.color && t.controls.notes));
const tile = p.root.querySelectorAll('[data-entry-id]').find(t => t.attrs['data-entry-id'] === e.id);
const control = k => tile.querySelector('[data-control="' + k + '"]');
assert.equal(e.style.r, 555); assert.equal(state.originals[e.id].r, 410);
assert.equal(control('notes').value, 'review');
assert.equal(control('color').value, '#123456');
control('badge').checked = true; control('badge').dispatch('change');
assert.deepEqual(JSON.parse(p.storage.getItem(G.entryKey(e))), {r: 555, badge: true, notes: 'review'});
control('look').value = 'grain'; control('look').dispatch('change');
control('settings').value = '{"strength":0.25}'; control('settings').dispatch('change');
assert.deepEqual(e.style.look, {kind: 'grain', strength: 0.25});
control('settings').value = '[]'; control('settings').dispatch('change');
assert.ok(tile.querySelector('[data-settings-error]').textContent);
assert.deepEqual(e.style.look, {kind: 'grain', strength: 0.25});
assert.ok(tile.textContent.includes('1 of 2'));
tile.querySelector('[data-sample="next"]').click(); assert.ok(tile.textContent.includes('2 of 2'));
const json = state.critique();
assert.equal(json.entries.find(x => x.pattern === e.pattern).after.r, 555);
p.header('side').value = 'B'; p.header('side').dispatch('change');
assert.ok(state.tiles.filter(t => t.viewer.current).every(t => t.viewer.current.blob.players[0].side === 'B'));
p.header('palette').value = 'blue-orange'; p.header('palette').dispatch('change');
assert.ok(state.tiles.every(t => t.viewer.controlPalette === 'blue-orange'));
control('reset').click();
assert.equal(e.style.r, 410); assert.equal(e.style.badge, undefined);
assert.equal(p.storage.getItem(G.colorKey(e.ability)), null);
assert.deepEqual(JSON.parse(p.storage.getItem(G.entryKey(e))), {notes: 'review'});
assert.equal(control('notes').value, 'review');
p.header('edited').checked = true; p.header('edited').dispatch('change'); assert.equal(tile.hidden, false);
control('notes').value = ''; control('notes').dispatch('input'); assert.equal(tile.hidden, true);
p.header('edited').checked = false; p.header('edited').dispatch('change');
p.header('filter').value = 'dark cover'; p.header('filter').dispatch('input'); assert.equal(tile.hidden, false);
p.header('agent').value = 'Harbor'; p.header('agent').dispatch('change'); assert.equal(tile.hidden, true);
G.saveColor(p.storage, 'Cyber Cage', '#111111');
p.storage.setItem('other-page', 'keep'); p.header('clear').click();
assert.equal(p.storage.length, 1); assert.equal(p.storage.getItem('other-page'), 'keep');
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_gallery_mount_export_and_copy_fallback():
    run_node(GALLERY_DRAW + GALLERY_DOM + r"""
const p = page();
const state = G.mount(R, p.doc, {...payload, samples: {}, replay_js_sha256: 'digest'}, p.storage);
const tile = p.root.querySelectorAll('[data-entry-id]')[0];
const notes = tile.querySelector('[data-control="notes"]');
notes.value = 'Please review'; notes.dispatch('input');
let blob, download;
global.URL = {createObjectURL(b) {blob = b; return 'blob:gallery';}, revokeObjectURL() {}};
const create = p.doc.createElement;
p.doc.createElement = tag => {const el = create(tag); if (tag === 'a') el.click = () => {download = el.download;}; return el;};
p.header('export').click();
assert.match(download, /^util-critique-\d{4}-\d{2}-\d{2}\.json$/);
assert.equal(blob.type, 'application/json');
Object.defineProperty(global, 'navigator', {value: {clipboard: {writeText: () => Promise.reject(Error('denied'))}}, configurable: true});
const textareas = p.doc.body.querySelectorAll('textarea').length;
Promise.resolve(p.header('copy').click()).then(() => {
  assert.equal(p.doc.copied, 'copy'); assert.match(p.header('status').textContent, /copied/i);
  assert.equal(p.doc.body.querySelectorAll('textarea').length, textareas);
}).catch(e => {console.error(e); process.exitCode = 1;});
""")
