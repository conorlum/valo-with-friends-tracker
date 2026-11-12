import pytest
from sqlalchemy import create_engine, inspect, text

from tests.support.postgres import command

pytestmark = pytest.mark.postgres


def test_area_feedback_migration_preserves_rows_on_upgrade_downgrade_and_reupgrade(scratch_database):
    command(scratch_database, "-m", "alembic", "upgrade", "0031")
    engine = create_engine(scratch_database)

    def existing_rows(conn):
        return conn.execute(text(
            "SELECT id, point_id, author_player_id, instance_index, verdict, note FROM coach_feedback ORDER BY id"
        )).all()

    def assert_empty_area_columns(conn):
        columns = {column["name"]: column for column in inspect(conn).get_columns("coach_feedback")}
        assert columns["area_name"]["type"].length == 64
        assert columns["area_name"]["nullable"] and columns["area_player_id"]["nullable"]
        assert conn.execute(text("SELECT area_name, area_player_id FROM coach_feedback ORDER BY id")).all() == [
            (None, None), (None, None),
        ]

    try:
        with engine.begin() as conn:
            subject, author, target = conn.execute(text(
                "INSERT INTO players (display_name, created_at) VALUES "
                "('Migration subject', now()), ('Migration author', now()), ('Migration target', now()) RETURNING id"
            )).scalars().all()
            match = conn.execute(text(
                "INSERT INTO matches (external_id, source, map_name, team1_rounds_won, team2_rounds_won, created_at) "
                "VALUES ('migration-0032', 'SCRAPED', 'Sunset', 13, 8, now()) RETURNING id"
            )).scalar_one()
            report = conn.execute(text(
                "INSERT INTO coach_reports (match_id, player_id, kind, version, playbook_version, model) "
                "VALUES (:match, :subject, 'match', 1, '1', 'test') RETURNING id"
            ), {"match": match, "subject": subject}).scalar_one()
            point = conn.execute(text(
                "INSERT INTO coach_points (report_id, polarity, ordinal, theme, title, body, instances) "
                "VALUES (:report, 'good', 1, 'rotation', 'Rotate', 'Kept the lane', '[{\"round\": 1, \"t\": 10.0}]') "
                "RETURNING id"
            ), {"report": report}).scalar_one()
            conn.execute(text(
                "INSERT INTO coach_feedback (point_id, author_player_id, instance_index, verdict, note) VALUES "
                "(:point, :author, NULL, 'right', 'overall'), (:point, :author, 0, NULL, 'moment')"
            ), {"point": point, "author": author})
            expected = existing_rows(conn)

        command(scratch_database, "-m", "alembic", "upgrade", "0032")
        with engine.begin() as conn:
            assert existing_rows(conn) == expected
            assert_empty_area_columns(conn)
            conn.execute(text(
                "UPDATE coach_feedback SET area_name = 'Market', area_player_id = :target WHERE instance_index = 0"
            ), {"target": target})
            conn.execute(text("DELETE FROM players WHERE id = :target"), {"target": target})
            assert conn.execute(text(
                "SELECT area_name, area_player_id FROM coach_feedback WHERE instance_index = 0"
            )).one() == ("Market", None)

        command(scratch_database, "-m", "alembic", "downgrade", "0031")
        with engine.connect() as conn:
            assert existing_rows(conn) == expected
            columns = {column["name"] for column in inspect(conn).get_columns("coach_feedback")}
            assert not {"area_name", "area_player_id"} & columns

        command(scratch_database, "-m", "alembic", "upgrade", "0032")
        with engine.connect() as conn:
            assert existing_rows(conn) == expected
            assert_empty_area_columns(conn)
    finally:
        engine.dispose()
