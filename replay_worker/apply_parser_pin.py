"""Clone the pinned parser and apply the pin's patches (the Docker image's first stage).

    python apply_parser_pin.py <replay_parser.json> <destination>

The same recipe as scripts/build_replay_parser.ps1: clone the upstream, check out the pinned
commit, apply each find/replace patch exactly once. replay_parser.json is the single source.
"""

import json
import hashlib
import subprocess
import sys
from pathlib import Path


def main(pin_path: str, destination: str) -> None:
    pin = json.loads(Path(pin_path).read_text(encoding="utf-8"))
    subprocess.run(["git", "clone", pin["upstream"], destination], check=True)
    subprocess.run(["git", "-C", destination, "checkout", "--detach", pin["commit"]], check=True)
    for patch in pin["patches"]:
        path = Path(destination) / patch["file"]
        text = path.read_text(encoding="utf-8")
        if text.count(patch["find"]) != 1:
            raise SystemExit(f"patch target not found exactly once in {patch['file']}")
        path.write_text(text.replace(patch["find"], patch["replace"]), encoding="utf-8")
    for patch in pin.get('source_patches', []):
        source = Path(pin_path).parent / patch['file']
        raw = source.read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(raw).hexdigest() != patch['sha256']:
            raise SystemExit('source patch digest mismatch')
        subprocess.run(['git', '-C', destination, 'apply', '--check', str(source.resolve())], check=True)
        subprocess.run(['git', '-C', destination, 'apply', str(source.resolve())], check=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
