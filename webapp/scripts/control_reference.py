"""The control engine's output as digests, to prove a change left flat maps alone
(docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4: a map without a height asset is
byte-identical to the reference engine, after only the revision number in the header is normalised).

    .\\.venv\\Scripts\\python.exe scripts\\control_reference.py --toys --write         # the committed fixture
    .\\.venv\\Scripts\\python.exe scripts\\control_reference.py --toys                 # compare with it
    .\\.venv\\Scripts\\python.exe scripts\\control_reference.py --blobs <dir> --write <out.json>
    .\\.venv\\Scripts\\python.exe scripts\\control_reference.py --blobs <dir> --compare <out.json>

`--toys` are tests/replays/control_toys.py's `reference_rounds()`; their digests live in
tests/fixtures/control/reference_flat.json (tests/replays/test_control_reference.py compares them on every
run). `--blobs` is a folder written by scripts/preview_control_live.py (`<n>.json.gz` and `context.json`):
real rounds, so their digests are written wherever `--write` says and never committed.

A digest is the sha256 of a round's stored bytes, decompressed, with the header's and the summary's
`revision` set to 0. Rewrite the fixture only for a change that is meant to move flat maps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

FIXTURE = WEBAPP_ROOT / "tests" / "fixtures" / "control" / "reference_flat.json"


def digest_round(rc, blob: dict) -> dict:
    """{"data", "summary"}: 16 hex each, over the decompressed bytes with the revision normalised."""
    from app.control.encode import encode_data, encode_summary
    from app.replays import control_format as cf

    header, streams = cf.unpack_data(encode_data(rc, blob))
    header["revision"] = 0
    data = hashlib.sha256(json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for name in sorted(streams):
        data.update(name.encode("ascii") + b"\0" + streams[name])
    summary = cf.unpack_summary(encode_summary(rc, blob))
    summary["revision"] = 0
    text = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"data": data.hexdigest()[:16], "summary": hashlib.sha256(text).hexdigest()[:16]}


def toy_digests() -> dict:
    sys.path.insert(0, str(WEBAPP_ROOT / "tests" / "replays"))
    from control_toys import reference_rounds

    from app.control import engine

    return {name: digest_round(engine.compute_round(blob, geo, link), blob)
            for name, (geo, blob, link) in reference_rounds().items()}


def blob_digests(directory: Path) -> dict:
    """The rounds of a preview folder, computed with the committed geometry of the folder's map."""
    import preview_control_live as preview

    from app.control import engine, geometry
    from app.replays import format as fmt

    ctx = json.loads((directory / "context.json").read_text(encoding="utf-8"))
    geo = geometry.visibility(geometry.load_geometry(ctx["match"]["map"]))
    out = {}
    for path in sorted(directory.glob("*.json.gz"), key=lambda p: int(p.name.split(".")[0])):
        n = int(path.name.split(".")[0])
        blob = fmt.decode_blob(path.read_bytes())
        link = preview.link_for(ctx, n)
        control_link = engine.ControlLink(sides={int(s): side for s, side in link["sides"].items()},
                                          db_deaths=tuple((int(s), float(t)) for s, t in link["db_deaths"]))
        out[str(n)] = digest_round(engine.compute_round(blob, geo, control_link), blob)
        print(f"  round {n}: {out[str(n)]}", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--toys", action="store_true", help="the toy rounds of tests/replays/control_toys.py")
    source.add_argument("--blobs", type=Path, help="a preview folder of real rounds (never committed)")
    parser.add_argument("--write", nargs="?", const=str(FIXTURE), help="write the digests (default: the fixture)")
    parser.add_argument("--compare", help="compare with this file (default with --toys: the fixture)")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(WEBAPP_ROOT / "scripts"))
    if args.write and args.blobs and Path(args.write).resolve().is_relative_to(WEBAPP_ROOT.parent):
        raise SystemExit("REFUSED: real rounds' digests are not written inside the repo")
    got = toy_digests() if args.toys else blob_digests(args.blobs)
    if args.write:
        Path(args.write).write_text(json.dumps({"rounds": got}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {len(got)} rounds to {args.write}")
        return 0
    want = json.loads(Path(args.compare or FIXTURE).read_text(encoding="utf-8"))["rounds"]
    differing = sorted(name for name in set(got) | set(want) if got.get(name) != want.get(name))
    if differing:
        for name in differing:
            print(f"DIFFERS {name}: got {got.get(name)}, reference {want.get(name)}")
        return 1
    print(f"identical ({len(got)} rounds)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
