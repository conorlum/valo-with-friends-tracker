"""constraints-scoring.txt must be the active manifest's frozen packages, and requirements.txt must fit inside it.

Ingestion refuses unless the scoring venv's `pip freeze` equals the manifest's list. Twice (2026-10-31 and
2026-11-06) .venv313 was rebuilt from requirements.txt, which then pinned pytest-xdist and left transitive
dependencies to float, and `matches` was refused. The scoring venv is now built with
`pip install -r requirements.txt -c constraints-scoring.txt`; these checks keep that command reproducing the list.
"""
import re

from scripts import scoring_constraints


def _pins(path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name, version = line.split("==", 1)
        pins[re.sub(r"[-_.]+", "-", re.sub(r"\[.*\]", "", name)).lower()] = version
    return pins


def test_the_committed_constraints_match_the_active_manifest():
    assert scoring_constraints.main(["check"]) == 0


def test_every_requirement_is_a_frozen_package_at_its_frozen_version():
    _, packages = scoring_constraints.frozen_packages()
    if not packages:
        return
    frozen = _pins(scoring_constraints.CONSTRAINTS)
    required = _pins(scoring_constraints.WEBAPP / "requirements.txt")
    unfrozen = sorted(name for name in required if name not in frozen)
    assert not unfrozen, (
        f"{unfrozen} are in requirements.txt but not in the frozen scoring environment, so ingestion would refuse "
        "a venv built from it. Test-only tools go in requirements-dev.txt; an app dependency needs a re-freeze.")
    moved = {name: (version, frozen[name]) for name, version in required.items() if frozen[name] != version}
    assert not moved, f"requirements.txt pins differ from the frozen versions (required, frozen): {moved}"
