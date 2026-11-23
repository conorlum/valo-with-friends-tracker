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
