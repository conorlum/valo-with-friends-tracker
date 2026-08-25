from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.orm import strategies as orm_strategies

from app.config import settings

# SQLAlchemy's `selectin` eager loader batches parent primary keys into groups
# of 500 per query (SelectInLoader._chunksize), to keep any single IN clause
# from getting huge. On this DB, a heavily-tracked player can have 3000+
# rounds, so loading Round.kill_events/Round.player_stats for their full
# career turns into ~8 serial round trips instead of 1 -- invisible on
# low-latency local Postgres, but each round trip costs real time against a
# remote DB (e.g. Neon). Raising it comfortably covers this dataset's round
# counts while staying far under Postgres's ~65535 bind-parameter limit.
orm_strategies.SelectInLoader._chunksize = 10_000

# pool_pre_ping: validates a pooled connection with a cheap SELECT before
# handing it to a caller, transparently reconnecting if it was silently
# closed (e.g. Neon closing a connection that sat idle through a long
# non-DB phase like a multi-minute tracker.gg page crawl).
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
