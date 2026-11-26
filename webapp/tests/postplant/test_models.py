"""Offline schema contract for post-plant evidence and its derived cache."""
from sqlalchemy import LargeBinary, SmallInteger, create_engine, inspect

from app.db import Base


def test_postplant_tables_columns_and_constraints():
    from app.models.replay_postplant import PostplantStatsCache, ReplayRoundPostplant
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine, tables=[ReplayRoundPostplant.__table__, PostplantStatsCache.__table__])
        schema = inspect(engine)
        columns = {c["name"]: c for c in schema.get_columns("replay_round_postplant")}
        assert set(columns) == {"replay_id", "round_number", "status", "fingerprint", "gaps_revision",
                                "data", "error", "computed_at"}
        assert schema.get_pk_constraint("replay_round_postplant")["constrained_columns"] == ["replay_id", "round_number"]
        assert columns["status"]["type"].length == 8
        assert columns["fingerprint"]["type"].length == 16
        assert isinstance(columns["gaps_revision"]["type"], SmallInteger)
        assert isinstance(columns["round_number"]["type"], SmallInteger)
        assert columns["data"]["nullable"] and columns["error"]["nullable"]
        assert columns["computed_at"]["default"] is not None
        [fk] = schema.get_foreign_keys("replay_round_postplant")
        assert fk["name"] == "fk_replay_round_postplant_round"
        assert fk["constrained_columns"] == ["replay_id", "round_number"]
        assert fk["referred_table"] == "replay_rounds"
        assert fk["referred_columns"] == ["replay_id", "round_number"]
        assert fk["options"]["ondelete"] == "CASCADE"
        [check] = schema.get_check_constraints("replay_round_postplant")
        assert check["name"] == "ck_replay_round_postplant_status"
        assert check["sqltext"] == "status IN ('ok', 'failed')"
        cache = {c["name"]: c for c in schema.get_columns("postplant_stats_cache")}
        assert set(cache) == {"key", "fingerprint", "data", "computed_at"}
        assert schema.get_pk_constraint("postplant_stats_cache")["constrained_columns"] == ["key"]
        assert cache["key"]["type"].length == 200 and cache["fingerprint"]["type"].length == 64
        assert isinstance(cache["data"]["type"], LargeBinary)
        assert all(not c["nullable"] for c in cache.values())
        assert cache["computed_at"]["default"] is not None
    finally:
        engine.dispose()
