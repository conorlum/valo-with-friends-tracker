"""A disk cache for the ex-ante observation replay.

`load_all_observations` replays every match through `impact.py` to re-derive
the ex-ante components, which costs several minutes over 1,151 matches. That
cost is why the Stage A targets were frozen early and swept narrowly: each
configuration change meant another full replay, so the sensitivity grid stayed
small and no match-primary target was ever tried.

The replay is deterministic given (match set, scorer version), so it only has
to happen once. Everything downstream -- target definitions, weight grids,
bootstrap counts -- is arithmetic over the cached rows and runs in seconds.

Local dev tooling only. Nothing here is imported by `app/main.py`, any router,
or any template, and nothing here writes to the database.

    from app.services.impact_eval_cache import load_observations

    observations = load_observations(db)      # replays and caches, or reads
    observations = load_observations(None)    # read-only; raises if absent

The cache is keyed on the eligible match set AND the scorer's calculation
version, and a stale entry is refused rather than silently used -- the whole
point is to make iteration cheap, and a cache that can quietly serve rows from
a different scorer would make it cheap and wrong.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import time
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import text

CACHE_VERSION = 3  # 3: identity gained source_revision + database_identity


def cache_path() -> Path:
    """Override with IMPACT_EVAL_CACHE. Defaults beside the repo, not inside
    it -- these files are tens of MB and are pure derived data."""
    override = os.environ.get("IMPACT_EVAL_CACHE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / ".impact_eval_cache" / "observations.pkl"


def _live_identity(db) -> dict:
    """What the cache must match to be usable. No replay -- a handful of
    counts, sums and a digest.

    Match ids and counts alone cannot see an EDIT, so source_revision and
    database_identity are part of the identity: without them, correcting a
    kill time or a round winner left a stale cache looking valid, and two
    databases sharing surrogate ids compared as the same snapshot."""
    from sqlalchemy import func, select

    from app.models import Round
    from app.scoring.impact import IMPACT_CALCULATION_VERSION
    from app.services.impact_eval import dataset_fingerprint
    from app.services.surrender_rounds import NOT_A_SURRENDER_ROUND

    match_ids = [
        int(row[0])
        for row in db.execute(text("SELECT id FROM matches ORDER BY id")).all()
    ]
    # NOT_A_SURRENDER_ROUND is a SQLAlchemy expression carrying a bind
    # parameter, so it has to go through select() -- interpolating it into
    # text() loses the binding and fails at execute time.
    rounds = db.execute(
        select(func.count()).select_from(Round).where(NOT_A_SURRENDER_ROUND)
    ).scalar()
    return {
        "cache_version": CACHE_VERSION,
        "calculation_version": IMPACT_CALCULATION_VERSION,
        "dataset_fingerprint": dataset_fingerprint(match_ids),
        "n_rounds": int(rounds),
        "source_revision": _source_revision(db),
        "database_identity": _database_identity(db),
    }


def _database_identity(db) -> str:
    """Which database this is, so two snapshots carrying the same surrogate
    ids cannot be mistaken for each other."""
    try:
        url = db.get_bind().url
        return f"{url.get_backend_name()}:{url.host or 'local'}:{url.database or ''}"
    except Exception:
        return "unknown"


def _source_revision(db) -> str:
    """A content digest of the rows the replay actually reads.

    Match ids and row counts cannot see an EDIT. Correcting a kill time,
    a loadout or a round winner leaves the id list and both counts identical,
    so a stale cache stayed 'valid' and a comparison could claim two
    different snapshots were the same data. These aggregates move whenever
    any value the scorer consumes changes, and they cost four cheap scans
    rather than a replay.
    """
    aggregates = [
        "SELECT COUNT(*), COALESCE(SUM(event_time_seconds), 0) FROM kill_events",
        "SELECT COUNT(*), COALESCE(SUM(loadout), 0) + COALESCE(SUM(score), 0) "
        "FROM round_player_stats",
        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN planted THEN 1 ELSE 0 END), 0) FROM rounds",
    ]
    parts = []
    for sql in aggregates:
        try:
            row = db.execute(text(sql)).one()
            parts.append(":".join(f"{v}" for v in row))
        except Exception:
            parts.append("NA")
    # Round outcomes decide every label, and are pure text.
    try:
        outcomes = db.execute(text(
            "SELECT id, outcome FROM rounds ORDER BY id"
        )).all()
        digest = hashlib.sha256(
            ";".join(f"{r[0]}={r[1] or ''}" for r in outcomes).encode()
        ).hexdigest()[:16]
        parts.append(digest)
    except Exception:
        parts.append("NA")
    return "|".join(parts)


def _mismatches(cached: dict, live: dict) -> list[str]:
    return [
        f"{key}: cached {cached.get(key)!r} != live {live[key]!r}"
        for key in live
        if cached.get(key) != live[key]
    ]


def write_cache(observations, identity: dict, path: Path | None = None) -> Path:
    path = path or cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "identity": identity,
        "written_at": time.time(),
        "n_observations": len(observations),
        "observations": observations,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)
    return path


def read_cache(path: Path | None = None) -> dict:
    path = path or cache_path()
    with open(path, "rb") as fh:
        return pickle.load(fh)


def load_observations(db=None, path: Path | None = None, refresh: bool = False,
                      report: dict | None = None):
    """Cached `load_all_observations(use_realized_swing=False)`.

    `db` None means read-only: the cache must exist and is used unverified
    (there is no session to check it against). With a `db`, the cached
    identity is verified against the live database and a mismatch triggers a
    replay rather than a stale read.
    """
    from app.services.impact_eval import load_all_observations

    path = path or cache_path()
    live = _live_identity(db) if db is not None else None
    notes: list[str] = []

    if not refresh and path.exists():
        try:
            payload = read_cache(path)
        except Exception as exc:  # noqa: BLE001 -- a corrupt cache must never be fatal
            notes.append(f"cache unreadable ({exc.__class__.__name__}), replaying")
        else:
            drift = _mismatches(payload.get("identity", {}), live) if live else []
            if drift:
                notes.append("cache stale, replaying -- " + "; ".join(drift))
            else:
                if report is not None:
                    report.update({
                        "source": "cache", "path": str(path),
                        "n_observations": payload["n_observations"],
                        "identity": payload["identity"], "notes": notes,
                    })
                return payload["observations"]

    if db is None:
        raise FileNotFoundError(
            f"no usable observation cache at {path} and no db session to build one. "
            f"Run: .\\.venv\\Scripts\\python.exe scripts\\cache_observations.py"
        )

    load_report: dict = {}
    observations = load_all_observations(db, use_realized_swing=False, report=load_report)
    write_cache(observations, live, path)
    if report is not None:
        report.update({
            "source": "replay", "path": str(path),
            "n_observations": len(observations),
            "identity": live, "notes": notes, "load": load_report,
        })
    return observations


def observations_to_columns(observations, feature_names):
    """(n, p) float array in `feature_names` order, for ad-hoc analysis that
    does not want to go through a target builder."""
    import numpy as np

    return np.array(
        [[float(getattr(o, name)) for name in feature_names] for o in observations],
        dtype=float,
    )


def describe(path: Path | None = None) -> dict:
    """Metadata only -- does not unpickle the rows."""
    path = path or cache_path()
    if not path.exists():
        return {"exists": False, "path": str(path)}
    payload = read_cache(path)
    return {
        "exists": True,
        "path": str(path),
        "size_mb": round(path.stat().st_size / 1e6, 1),
        "written_at": payload.get("written_at"),
        "n_observations": payload.get("n_observations"),
        "identity": payload.get("identity"),
        "fields": sorted(asdict(payload["observations"][0])) if payload.get("observations") else [],
    }
