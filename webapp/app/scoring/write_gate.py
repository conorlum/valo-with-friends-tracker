"""Claiming the right to write scores, and reading the gate that grants it.

The gate itself is scripts/sql/release_write_gate.sql: statement-level triggers
on impact_scores and the ingestion tables that refuse any write whose connection
does not carry the open release's identity (or the runbook's admin identity).

Nothing here decides anything. It sets the identity a caller has already earned
by verifying the active manifest, and reads back the gate's state so a preflight
can refuse BEFORE ingestion commits a match rather than after -- the ordering
that left match 3133 stranded when scoring failed on a schema mismatch.

The identity is a session setting, not a transaction-local one, because a single
ingest spans several transactions (load_match commits, cache invalidation
commits, then scoring commits) and all of them must carry it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import event, text

#: Custom GUC the gate's trigger reads. Dotted, as Postgres requires.
WRITE_IDENTITY_SETTING = "valo.write_release"


class WriteGateError(RuntimeError):
    """The gate is missing, closed, or open for a different release."""


@dataclass(frozen=True)
class GateState:
    state: str
    release_id: str
    admin_id: str
    note: str | None

    @property
    def is_open(self) -> bool:
        return self.state == "open"


def read_gate(db) -> GateState | None:
    """The gate row, or None when the gate is not installed (local sqlite and
    the test fixtures, which have no triggers to satisfy)."""
    installed = db.execute(text("SELECT to_regclass('public.scoring_gate')")).scalar()
    if not installed:
        return None
    row = db.execute(text(
        "SELECT state, release_id, admin_id, note FROM scoring_gate WHERE id")).one_or_none()
    if row is None:
        raise WriteGateError("the scoring gate table exists but holds no row")
    return GateState(*row)


def claim_write_identity(db, identity: str) -> None:
    """Present `identity` to the gate for this connection.

    Callers must have verified the active configuration first: the identity is
    the claim "this process is the release the gate named", and the gate takes
    it at face value.
    """
    db.execute(text("SELECT set_config(:name, :value, false)"),
               {"name": WRITE_IDENTITY_SETTING, "value": identity})


def install_write_identity(db, identity: str) -> None:
    """Present `identity` on every connection this session's engine opens.

    A per-connection claim is not enough. An ingest commits several times (the
    match, then the cache invalidation, then the scores), and a commit returns
    the connection to the pool: the next statement can arrive on a different
    connection with no identity at all, and the gate would refuse it halfway
    through -- committed match, no scores, which is the exact shape of the
    stranding this gate exists to prevent.
    """
    engine = db.get_bind()

    @event.listens_for(engine, "connect")
    def _claim_on_connect(dbapi_connection, _record):  # pragma: no cover - driver callback
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SELECT set_config(%s, %s, false)",
                           (WRITE_IDENTITY_SETTING, identity))

    claim_write_identity(db, identity)  # the connection already checked out


def current_write_identity(db) -> str | None:
    return db.execute(text("SELECT current_setting(:name, true)"),
                      {"name": WRITE_IDENTITY_SETTING}).scalar()
