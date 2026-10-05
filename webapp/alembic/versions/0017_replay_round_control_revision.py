"""replay round control revision

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-05

`replay_round_control.control_revision`: the CONTROL_REVISION a row was computed under
(docs/superpowers/plans/2026-10-05-control-idle-queue.md, D9), so SQL can count rows per revision. Written with
every row from here on (app/services/replay_control_store.py). Existing `ok` rows are backfilled from the JSON
header their data already starts with (app/replays/control_format.py, `pack_data`); a failed row, or a header that
can't be read, stays NULL.

Additive, and the ValoMaths demo's table is empty.
"""
import gzip
import io
import json
import struct
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def header_revision(data) -> int | None:
    """The `revision` in a stored row's data header: gzip(b"VCTL", version byte, <I header length, JSON header, ...).
    Read here rather than through `app`, so a later format change can't break this migration."""
    try:
        # Only the header is read: the migration holds the ALTER's lock while it backfills.
        with gzip.GzipFile(fileobj=io.BytesIO(bytes(data))) as stream:
            head = stream.read(9)
            if head[:4] != b"VCTL":
                return None
            (length,) = struct.unpack("<I", head[5:9])
            found = json.loads(stream.read(length).decode("utf-8")).get("revision")
    except Exception:  # noqa: BLE001 - a row that can't be read stays NULL; the migration never fails on data
        return None
    return found if type(found) is int else None


def backfill(connection) -> int:
    """One row's bytes at a time: a round's data is large, and the build machine is small."""
    keys = connection.execute(sa.text(
        "SELECT replay_id, round_number FROM replay_round_control "
        "WHERE status = 'ok' AND control_revision IS NULL ORDER BY replay_id, round_number")).fetchall()
    filled = 0
    for replay_id, round_number in keys:
        where = {"r": replay_id, "n": round_number}
        data = connection.execute(sa.text(
            "SELECT data FROM replay_round_control WHERE replay_id = :r AND round_number = :n"), where).scalar()
        found = header_revision(data)
        if found is None:
            continue
        connection.execute(sa.text(
            "UPDATE replay_round_control SET control_revision = :v WHERE replay_id = :r AND round_number = :n"),
            {**where, "v": found})
        filled += 1
    return filled


# PROVISIONAL(D4): not yet run on PostgreSQL; run the local upgrade/downgrade check before merging.
def upgrade() -> None:
    op.add_column("replay_round_control", sa.Column("control_revision", sa.SmallInteger(), nullable=True))
    backfill(op.get_bind())


def downgrade() -> None:
    op.drop_column("replay_round_control", "control_revision")
