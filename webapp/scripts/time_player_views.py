"""
Timing harness for the account-groups pages. Runs ONLY against the local
corpus copy (host localhost/127.0.0.1, database valo_groups_corpus).

Usage (DATABASE_URL must point at the corpus copy):
    python scripts/time_player_views.py --link --main "A#1" --sub "B#2" --sub "C#3"
    python scripts/time_player_views.py --main "A#1" --sub "B#2" --sub "C#3" --out timings.json [--repeat 3] [--names]
    python scripts/time_player_views.py --unlink --main "A#1" --sub "B#2"
"""

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
ALLOWED_DB = "valo_groups_corpus"


def check_database_url(url: str) -> bool:
    """True only for a local host and the corpus-copy database."""
    try:
        parsed = urlparse(url)
        return parsed.hostname in ALLOWED_HOSTS and parsed.path.lstrip("/") == ALLOWED_DB
    except ValueError:
        return False


def _request(display_name):
    from starlette.requests import Request
    return Request({
        "type": "http", "method": "GET", "path": f"/players/{display_name}/career", "query_string": b"",
        "headers": [], "scheme": "http", "server": ("testserver", 80), "session": {},
    })


def _find(db, display_name):
    from app.models import Player
    return db.query(Player).filter(Player.display_name == display_name).one()


def _unlink(db, ids):
    from sqlalchemy import delete
    from app.models import AccountGroup, AccountGroupMember, AccountGroupViewCache
    group_ids = {g for (g,) in db.query(AccountGroupMember.group_id).filter(AccountGroupMember.player_id.in_(ids))}
    group_ids |= {g for (g,) in db.query(AccountGroup.id).filter(AccountGroup.main_player_id.in_(ids))}
    if group_ids:
        db.execute(delete(AccountGroupViewCache).where(AccountGroupViewCache.group_id.in_(group_ids)))
        db.execute(delete(AccountGroupMember).where(AccountGroupMember.group_id.in_(group_ids)))
        db.execute(delete(AccountGroup).where(AccountGroup.id.in_(group_ids)))
    db.commit()


def _median_time(repeat, setup, fn):
    runs = []
    for _ in range(repeat):
        setup()
        t0 = time.perf_counter()
        fn()
        runs.append(time.perf_counter() - t0)
    return statistics.median(runs)


def _blob_sizes(db, ids, group_id):
    from sqlalchemy import text
    pv = db.execute(text(
        "SELECT count(*), coalesce(sum(octet_length(data::text)), 0) FROM player_view_cache WHERE player_id = ANY(:ids)"
    ), {"ids": list(ids)}).one()
    gv = db.execute(text(
        "SELECT count(*), coalesce(sum(octet_length(data::text)), 0) FROM account_group_view_cache WHERE group_id = :g"
    ), {"g": group_id}).one()
    return {"player_view_cache_rows": pv[0], "player_view_cache_bytes": int(pv[1]),
            "account_group_view_cache_rows": gv[0], "account_group_view_cache_bytes": int(gv[1])}


def measure(db, main, subs, repeat):
    from app.models import AccountGroupMember
    from app.routers import players as pr
    from app.services.account_groups import group_fingerprint, prewarm_group_caches
    from app.services.player_view_cache import invalidate_player_cache, recompute_player_views

    group_id = db.query(AccountGroupMember.group_id).filter_by(player_id=main.id).scalar()
    if group_id is None:
        sys.exit("main is not in a group: run --link first")
    ids = {m for (m,) in db.query(AccountGroupMember.player_id).filter_by(group_id=group_id)}

    def cold(player_ids):
        def _go():
            invalidate_player_cache(db, set(player_ids))
            db.commit()
        return _go

    def career(accounts):
        return lambda: pr.player_career_fragment(_request(main.display_name), main.display_name, accounts, db)

    out = {}
    out["prewarm_one_account_s"] = _median_time(repeat, cold({main.id}), lambda: recompute_player_views(db, main.id))
    out["prewarm_group_s"] = _median_time(repeat, cold(ids), lambda: prewarm_group_caches(db, {main.id}))
    for key, accounts, cold_ids in (("single", None, {main.id}), ("combined", "all", ids)):  # combined last: blobs are read after it
        colds, hots = [], []
        for _ in range(repeat):
            cold(cold_ids)()
            t0 = time.perf_counter()
            career(accounts)()
            colds.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            career(accounts)()
            hots.append(time.perf_counter() - t0)
        out[f"career_{key}_cold_s"] = statistics.median(colds)
        out[f"career_{key}_cached_s"] = statistics.median(hots)
    out["account_view_s"] = _median_time(repeat, lambda: None, lambda: pr._account_view(db, main, "all"))
    out["group_fingerprint_s"] = _median_time(
        repeat, lambda: None, lambda: group_fingerprint(db, frozenset(ids)))
    out["blob_bytes"] = _blob_sizes(db, ids, group_id)
    out["group_id"] = group_id
    out["player_ids"] = sorted(ids)
    return out


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", required=True)
    ap.add_argument("--sub", action="append", default=[])
    ap.add_argument("--out")
    ap.add_argument("--names", action="store_true")
    ap.add_argument("--link", action="store_true")
    ap.add_argument("--unlink", action="store_true")
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    from app.config import settings
    if not check_database_url(settings.database_url):
        print("refusing: DATABASE_URL must be localhost/127.0.0.1 and database " + ALLOWED_DB, file=sys.stderr)
        sys.exit(2)

    from app.db import SessionLocal
    from app.services.account_groups import link_account
    db = SessionLocal()
    try:
        main = _find(db, args.main)
        subs = [_find(db, s) for s in args.sub]
        ids = [main.id] + [s.id for s in subs]
        if args.unlink or args.link:
            _unlink(db, ids)
            if args.link:
                for s in subs:
                    err = link_account(db, main.id, s.id)
                    if err:
                        db.rollback()
                        sys.exit(f"link failed: {err}")
                db.commit()
            print("linked" if args.link else "unlinked", ids)
            return
        result = measure(db, main, subs, args.repeat)
        result["repeat"] = args.repeat
        result["at"] = datetime.now(timezone.utc).isoformat()
        if args.names:
            result["names"] = {str(p.id): p.display_name for p in [main, *subs]}
        if args.out:
            Path(args.out).write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main_cli()
