"""What a checkout must prove before it writes anything a match depends on.

Ingestion commits a match, then invalidates caches, then scores. When scoring
failed on a schema mismatch in September 2026 the match stayed committed and
unscored, the caches stayed deleted, and nobody noticed until the corpus was
counted: that is match 3133. The fix is to refuse BEFORE the first commit.

This is the checkout's own check, so it can give a precise reason and stop
early. It is not the enforcement: a checkout too old to contain this file will
never run it. The release write gate (scripts/sql/release_write_gate.sql) is
what actually refuses those, from inside the database.

Order matters. Each check answers a different question:
  1. schema      -- does this checkout's ORM match the database's shape?
  2. readable    -- can it actually read the table it is about to write?
  3. scoring     -- is an activated, verified manifest in force here?
  4. environment -- is this the interpreter and dependency set that was frozen?
  5. gate        -- does the database agree this release may write?
Only then does it claim the write identity the gate checks.
"""

from __future__ import annotations

import subprocess
import sys

from sqlalchemy import text

from app.scoring.write_gate import install_write_identity, read_gate


class IngestRefused(RuntimeError):
    """This checkout must not write. The message says which check failed."""


def _migration_head() -> str | None:
    """The head revision of THIS checkout's migration scripts."""
    try:
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        webapp_root = Path(__file__).resolve().parents[2]
        config = Config(str(webapp_root / "alembic.ini"))
        config.set_main_option("script_location", str(webapp_root / "alembic"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:  # pragma: no cover - alembic layout problems surface elsewhere
        return None


def _installed_packages() -> list[str]:
    frozen = subprocess.run([sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"],
                            capture_output=True, text=True, timeout=120)
    return sorted(line.strip() for line in frozen.stdout.splitlines() if line.strip())


def check_schema(db) -> None:
    applied = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    head = _migration_head()
    if head is not None and applied != head:
        raise IngestRefused(
            f"the database is at migration {applied!r} but this checkout's head is {head!r}. "
            "Ingesting now would commit a match this code cannot score.")


def check_readable(db) -> None:
    """The exact query that failed for match 3133, run before anything commits."""
    from app.models import ImpactScore

    try:
        db.query(ImpactScore).limit(1).all()
    except Exception as exc:
        raise IngestRefused(
            f"this checkout cannot read impact_scores against this database ({exc.__class__.__name__}: "
            f"{str(exc)[:200]}). Scoring would fail after the match had already committed.") from exc


def check_scoring_configuration():
    """The active manifest, verified. Returns it, or refuses."""
    from app.scoring.impact_runtime import active_manifest, active_scoring_config

    try:
        manifest = active_manifest()
        config = active_scoring_config()
    except Exception as exc:
        raise IngestRefused(f"the active scoring manifest does not verify here: {exc}") from exc
    if manifest is None or config is None:
        raise IngestRefused(
            "no scoring manifest is active in this checkout (ACTIVE_MANIFEST is None), so ingestion "
            "would write legacy scores into an activated database.")
    return manifest


def _running_python() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def check_environment(manifest) -> None:
    environment = manifest.get("environment") or {}
    # Compared at major.minor. Scores have depended on the interpreter before --
    # 3.12 made sum() compensated, which moved trade credits -- and that was a
    # minor-version change; a patch release does not change float arithmetic.
    frozen_python = (environment.get("python") or "").split(" ", 1)[0]
    if frozen_python and ".".join(frozen_python.split(".")[:2]) != _running_python():
        raise IngestRefused(
            f"this is Python {_running_python()}, but this candidate was frozen under Python "
            f"{frozen_python}. Ingest from the frozen environment (.venv313), or refreeze.")
    recorded = environment.get("packages")
    if not recorded:
        return
    current = _installed_packages()
    if current == list(recorded):
        return
    missing = sorted(set(recorded) - set(current))
    extra = sorted(set(current) - set(recorded))
    raise IngestRefused(
        "the installed packages differ from the ones this candidate was frozen with "
        f"(missing {missing[:5]}, unexpected {extra[:5]}). Rebuild the environment, or refreeze.")


def check_gate_and_claim(db, manifest) -> str:
    gate = read_gate(db)
    if gate is None:
        raise IngestRefused(
            "the release write gate is not installed on this database. Install it before ingesting.")
    release = manifest["candidate_id"]
    if not gate.is_open:
        raise IngestRefused(
            f"the release write gate is {gate.state} (note: {gate.note!r}). Ingestion is frozen.")
    if gate.release_id != release:
        raise IngestRefused(
            f"the gate is open for {gate.release_id!r}, but this checkout is {release!r}.")
    # Every connection this engine opens from now on, because an ingest
    # commits several times and a commit returns the connection to the pool.
    install_write_identity(db, release)
    return release


def verify_ingest_preflight(db) -> str:
    """Run every check and claim the write identity. Returns the release id.

    Callers run this before their first write, not after their first commit.
    """
    check_schema(db)
    check_readable(db)
    manifest = check_scoring_configuration()
    check_environment(manifest)
    return check_gate_and_claim(db, manifest)
