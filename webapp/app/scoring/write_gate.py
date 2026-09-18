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

import weakref
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


#: The identity each engine presents, keyed weakly so a disposed engine is not
#: kept alive by it.
_ENGINE_IDENTITIES: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_LISTENING: "weakref.WeakSet" = weakref.WeakSet()


def install_write_identity(db, identity: str) -> None:
    """Present `identity` on every connection this session's engine hands out.

    On CHECKOUT, not on connect, and every time rather than once. Two ways a
    writer otherwise ends up with no identity halfway through an ingest --
    committed match, refused scores, the exact stranding shape the gate exists
    to prevent:

    - the pool can already hold connections opened before this call, and a
      "connect" listener never fires for those;
    - `set_config` made inside a transaction that later rolls back is reverted
      with it, and the pool hands that connection out again without opening it
      afresh, so no connect-time hook runs either.

    Re-registering is harmless: the listener is installed once per engine and
    reads the current identity when it fires.
    """
    engine = db.get_bind()
    _ENGINE_IDENTITIES[engine] = identity
    if engine not in _LISTENING:
        @event.listens_for(engine, "checkout")
        def _claim_on_checkout(dbapi_connection, _record, _proxy):  # pragma: no cover - pool callback
            claimed = _ENGINE_IDENTITIES.get(engine)
            if claimed is None:
                return
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SELECT set_config(%s, %s, false)",
                               (WRITE_IDENTITY_SETTING, claimed))
            # Hand the connection over IDLE, not mid-transaction. psycopg2 opens
            # a transaction implicitly on that first statement, so without this
            # commit every checkout arrives with a query already in it, and the
            # caller can no longer ask for a snapshot: SET TRANSACTION ISOLATION
            # LEVEL must be a transaction's first statement, and psycopg2's
            # set_session() refuses inside one at all. That broke verify-build
            # and verify-live, the only commands that claim an identity and then
            # take a REPEATABLE READ READ ONLY snapshot on the same connection.
            # Committing is also what makes the identity stick: set_config's
            # third argument is false (session scope, not transaction scope), so
            # a later rollback would otherwise revert it -- the very stranding
            # this listener exists to prevent.
            dbapi_connection.commit()

        _LISTENING.add(engine)
    claim_write_identity(db, identity)  # the connection already checked out


def current_write_identity(db) -> str | None:
    return db.execute(text("SELECT current_setting(:name, true)"),
                      {"name": WRITE_IDENTITY_SETTING}).scalar()
