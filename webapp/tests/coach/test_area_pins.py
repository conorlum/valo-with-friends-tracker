import json
from contextlib import nullcontext
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.exc import ProgrammingError

from app import db as database
from app.models.coach import CoachFeedback
from app.models.replay import ReplayRound
from app.replays import format as fmt
from app.services import coach
from scripts import area_pins
from tests.coach.test_coach_store import payload


def test_pins_use_the_nearest_height_sample_and_omit_player_details():
    source = {"area_name": "Market", "match": "example-match", "map": "Sunset", "round": 5, "t": 20, "slot": 1,
              "player_name": "private fixture player", "area_player_id": 42}
    blob = {"hz": 4, "tracks": {"1": [fmt.encode_segment(20, [300, 310], [400, 410], [0, 0], [50, 60])]}}
    blobs = {("example-match", 5): blob}
    pins = area_pins.pins_from(
        [source, {**source, "t": 20.125}, {**source, "t": 20.75}, {**source, "t": 20.751},
         {**source, "slot": None}, {**source, "slot": 9}, {**source, "round": 6}],
        lambda match, round_number: blobs.get((match, round_number)),
    )
    base = {"map": "Sunset", "area": "Market", "match": "example-match"}
    assert pins == [{**base, "u": 300, "v": 400, "z": 50}, {**base, "u": 300, "v": 400, "z": 50},
                    {**base, "u": 310, "v": 410, "z": 60}]


def test_main_keeps_the_previous_file_if_migration_0032_is_missing(tmp_path, monkeypatch, capsys):
    class Unmigrated:
        def query(self, *columns):
            raise RuntimeError("column area_name does not exist")

    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(Unmigrated()))
    out = tmp_path / "pins.json"
    out.write_text("[]", encoding="utf-8")
    assert area_pins.main(["--out", str(out)]) == 0
    assert "migration 0033 isn't live yet" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == "[]"


def test_main_does_not_hide_an_unrelated_database_error(tmp_path, monkeypatch):
    class Failed:
        def query(self, *columns):
            raise ProgrammingError("SELECT coach_feedback.area_name", {}, RuntimeError("column unrelated does not exist"))

    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(Failed()))
    out = tmp_path / "pins.json"
    with pytest.raises(ProgrammingError, match="unrelated"):
        area_pins.main(["--out", str(out)])
    assert not out.exists()


def test_main_exports_a_linked_match_and_counts_feedback_with_no_replay_or_player(db, world, tmp_path, monkeypatch, capsys):
    replay, ids = world
    report = coach.store_report(db, replay.match_id, ids[0], payload())
    point = coach.points(db, report.id)[0]
    # The chosen player is slot 5. The author and report player each have different tracks.
    blob = {"v": 1, "hz": 4, "tracks": {
        str(slot): [fmt.encode_segment(41.5, [u], [4000], [0], [z])]
        for slot, u, z in ((0, 1000, 10), (1, 2000, 20), (5, 5000, 70))
    }}
    db.get(ReplayRound, (replay.id, 2)).data = fmt.encode_blob(blob)
    missing_report = coach.store_report(db, None, ids[0], payload())
    missing_point = coach.points(db, missing_report.id)[0]
    for point_id, player_id, area in ((point.id, ids[5], "Market"), (point.id, None, "Player removed"),
                                      (missing_point.id, ids[5], "Replay missing")):
        db.add(CoachFeedback(point_id=point_id, author_player_id=ids[1], instance_index=0, area_name=area,
                             area_player_id=player_id, handled_at=datetime.now(timezone.utc)))
    db.commit()
    expected = {"map": replay.map_name, "u": 5000, "v": 4000, "z": 70, "area": "Market", "match": str(replay.match_uuid)}

    statements = []

    def record(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(db))
    out = tmp_path / "new" / "pins.json"
    try:
        assert area_pins.main(["--out", str(out)]) == 0
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert json.loads(out.read_text(encoding="utf-8")) == [expected]
    assert "1 pins, 2 skipped" in capsys.readouterr().out
