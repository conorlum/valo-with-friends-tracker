"""Check reviewed area files without opening a database.

Run from webapp: python scripts/areas_files.py check [--dir DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.coach import areas  # noqa: E402

DATA_DIR = WEBAPP_ROOT / "app/static/data"


def file_status(path: Path, map_name: str) -> str:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing"
    except json.JSONDecodeError as exc:
        return f"invalid JSON: {exc.msg}"
    except UnicodeError:
        return "invalid UTF-8"
    except OSError as exc:
        return f"cannot read file: {exc.strerror}"
    errors = areas.validate(obj)
    if errors:
        return errors[0]
    if obj["reviewed"] is not True:
        return "not reviewed"
    if obj["map"] != map_name:
        return "wrong map name"
    return "ok"


def check(directory: Path) -> int:
    maps = json.loads((DATA_DIR / "layouts.json").read_text(encoding="utf-8"))["maps"]
    ok = 0
    for map_name in maps:
        status = file_status(directory / f"{map_name}.areas.json", map_name)
        print(f"{map_name}: {status}")
        ok += status == "ok"
    print(f"{ok}/{len(maps)} maps ok")
    return 0 if ok == len(maps) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    checker = commands.add_parser("check", help="check every map's reviewed area file")
    checker.add_argument("--dir", type=Path, default=DATA_DIR / "control")
    args = parser.parse_args()
    return check(args.dir)


if __name__ == "__main__":
    raise SystemExit(main())
