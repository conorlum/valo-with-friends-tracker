r"""Declaration 13's equivalence: the v4 implementation against the INDEPENDENT
reference rows (plan 2026-09-21-impact-v4, section 2.6 steps 2 and 3).

Run from webapp/, read-only, on the measurement cohort (valo_v4):

    DATABASE_URL=... .venv313/Scripts/python.exe scripts/compare_v4_reference.py \
        --reference DIR --out DIR [--pairs P0:exante,N:realized,...]

The reference rows were written by scripts/postplant_v4_decl12.py --dump-rows at
c470670, whose app/ is production rc3 (f96aee9). They are NEVER regenerated
here and never adjusted: a difference is a finding about this checkout.

Each arm is scored by THIS checkout through a declared comparator, not a
wrapper:

    P0   impact_rc3    (both v4 flags off)
    N    impact_v4_n   (enable_decided_only_time)
    N+A  impact_v4     (both flags)

each under ex-ante and realized scoring. Rows are compared BY KEY
(round_id, match_player_id) over every CalculatedImpact field except
scoring_version, which is reported separately. The implementation's rows are
also written in the reference's exact CSV form, so byte equality is reported
too; the row-by-key comparison is what the declaration scores.
"""

import argparse
import csv
import dataclasses
import hashlib
import io
import json
import sys
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.scoring.impact import CalculatedImpact, build_impact_rows_for_match
from app.scoring.impact_manifest import COMPARATORS, RC3, V4, V4_N

ARMS = {"P0": RC3, "N": V4_N, "N+A": V4}
MODES = ("exante", "realized")
KEY = ("round_id", "match_player_id")
SEPARATE = "scoring_version"


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _cell(value):
    """The reference's own cell rule (postplant_v4_decl12._dump_cell)."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return _canonical_json(value)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SystemExit(f"STOP: unexpected cell type {type(value).__name__}: {value!r}")
    return str(value)


def _line(row, fields):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow([_cell(getattr(row, f)) for f in fields])
    return buf.getvalue()


def _kwargs(arm, mode):
    kw = COMPARATORS[ARMS[arm]].build_kwargs()
    kw["use_realized_swing"] = mode == "realized"
    return kw


def replay(db, ids, pairs, fields):
    """{(arm, mode): [(key, csv line)]} and the assists reports, one pass per mode."""
    out = {pair: [] for pair in pairs}
    assists = {pair: Counter() for pair in pairs}
    for mode in MODES:
        arms = [arm for arm, m in pairs if m == mode]
        if not arms:
            continue
        log(f"replaying {mode}: {len(ids):,} matches x {arms}")
        for i, match_id in enumerate(ids, 1):
            for arm in arms:
                report = assists[(arm, mode)]

                def observer(round_number, kill_index, kill, context, report=report):
                    got = context.get("post_decided_assists")
                    if got is not None:
                        for what in ("removed", "clamped", "unmapped", "ambiguous"):
                            report[what] += len(got[what])
                        report["decided_kills"] += got["decided"]

                rows = build_impact_rows_for_match(db, match_id, kill_observer=observer,
                                                   **_kwargs(arm, mode))
                out[(arm, mode)].extend(((r.round_id, r.match_player_id), _line(r, fields))
                                        for r in rows)
            db.rollback()
            if i % 500 == 0:
                log(f"  {mode} {i:,}/{len(ids):,}")
    return out, assists


def _read_reference(path):
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        return header, {(int(r[0]), int(r[1])): r for r in reader}


def compare(reference_path, impl_lines, fields):
    header, ref = _read_reference(reference_path)
    if header != fields:
        raise SystemExit(f"STOP: reference header {header} is not this checkout's fields {fields}")
    impl = {key: next(csv.reader([line])) for key, line in impl_lines}
    if len(impl) != len(impl_lines):
        raise SystemExit("STOP: duplicate keys in the implementation's rows")
    compared = [i for i, f in enumerate(fields) if f != SEPARATE]
    version_at = fields.index(SEPARATE)
    only_ref = sorted(set(ref) - set(impl))
    only_impl = sorted(set(impl) - set(ref))
    differing, by_field, examples = 0, Counter(), []
    for key in sorted(set(ref) & set(impl)):
        a, b = ref[key], impl[key]
        moved = [fields[i] for i in compared if a[i] != b[i]]
        if moved:
            differing += 1
            by_field.update(moved)
            if len(examples) < 10:
                examples.append({"key": list(key), "fields": {f: [a[fields.index(f)], b[fields.index(f)]]
                                                              for f in moved}})
    return {
        "rows_reference": len(ref), "rows_implementation": len(impl),
        "keys_only_in_reference": len(only_ref), "keys_only_in_implementation": len(only_impl),
        "first_keys_only_in_reference": [list(k) for k in only_ref[:5]],
        "first_keys_only_in_implementation": [list(k) for k in only_impl[:5]],
        "rows_differing": differing, "differing_by_field": dict(by_field), "examples": examples,
        "scoring_version_reference": dict(Counter(r[version_at] for r in ref.values())),
        "scoring_version_implementation": dict(Counter(r[version_at] for r in impl.values())),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", required=True, help="directory holding ref_*.csv and its sidecar")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pairs", default="", help="e.g. P0:realized; default all six")
    ap.add_argument("--label", default="equivalence")
    ap.add_argument("--limit", type=int, default=0, help="last N matches: SMOKE TEST, NOT A RESULT")
    args = ap.parse_args()
    reference, out_dir = Path(args.reference), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs = ([tuple(p.split(":")) for p in args.pairs.split(",")] if args.pairs
             else [(arm, mode) for mode in MODES for arm in ARMS])
    for arm, mode in pairs:
        if arm not in ARMS or mode not in MODES:
            raise SystemExit(f"STOP: unknown pair {arm}:{mode}")
    sidecar = json.loads((reference / "reference_sidecar.json").read_text(encoding="utf-8"))
    fields = [f.name for f in dataclasses.fields(CalculatedImpact)]
    if sidecar["fields"] != fields:
        raise SystemExit(f"STOP: the reference's fields differ from this checkout's: "
                         f"{sidecar['fields']} vs {fields}")

    db = SessionLocal()
    db.rollback()
    db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    try:
        ids = [r[0] for r in db.execute(text("SELECT id FROM matches ORDER BY id"))]
        database = db.execute(text("SELECT current_database()")).scalar()
        db.rollback()
        if args.limit:
            log(f"*** --limit {args.limit}: SMOKE TEST, NOT A RESULT ***")
            ids = ids[-args.limit:]
        elif hashlib.sha256(_canonical_json(ids).encode()).hexdigest() != sidecar["match_id_list_sha256"]:
            raise SystemExit("STOP: this database's match ids are not the reference's cohort")
        lines, assists = replay(db, ids, pairs, fields)
    finally:
        db.rollback()
        db.close()

    report = {"label": args.label, "database": database, "matches": len(ids),
              "limit": args.limit, "reference_revision": sidecar["revision"], "pairs": {}}
    for arm, mode in pairs:
        name = f"ref_{arm.replace('+', 'plus')}_{mode}.csv"
        rows = sorted(lines[(arm, mode)])
        data = (",".join(fields) + "\n" + "".join(line for _, line in rows)).encode("utf-8")
        impl_name = f"impl_{arm.replace('+', 'plus')}_{mode}.csv"
        (out_dir / impl_name).write_bytes(data)
        result = compare(reference / name, rows, fields) if not args.limit else {}
        result.update({
            "comparator": ARMS[arm], "implementation_sha256": hashlib.sha256(data).hexdigest(),
            "reference_sha256": sidecar["artifacts"][name]["sha256"],
            "assists": dict(assists[(arm, mode)]),
        })
        result["bytes_equal"] = result["implementation_sha256"] == result["reference_sha256"]
        report["pairs"][f"{arm}:{mode}"] = result
        log(f"{arm:4s} {mode:8s} vs reference: rows differing {result.get('rows_differing')}, "
            f"keys only ref/impl {result.get('keys_only_in_reference')}/"
            f"{result.get('keys_only_in_implementation')}, scoring_version "
            f"{result.get('scoring_version_reference')} / {result.get('scoring_version_implementation')}, "
            f"bytes equal {result['bytes_equal']}, assists {result['assists']}")
    (out_dir / f"{args.label}.json").write_text(json.dumps(report, indent=2))
    log(f"written to {out_dir / (args.label + '.json')}")


if __name__ == "__main__":
    main()
