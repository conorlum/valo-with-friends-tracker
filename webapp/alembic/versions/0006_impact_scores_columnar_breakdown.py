"""unpack impact_scores.breakdown into columns, narrow floats, drop surrogate id

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-26

`impact_scores` was 271 MB of a 432 MB database -- 63% of the whole thing --
almost entirely because `breakdown` was a JSON (not JSONB) dict storing ~14
long, descriptive string keys verbatim on all 652,730 rows. Measured
avg_width was 313 bytes/row, of which ~85% was repeated key names and
punctuation rather than data.

Three changes, all of which require rewriting the same table, so they happen
in one pass rather than three:

  1. The 12 scalar breakdown keys become int2 columns; only the two
     per-teammate trade maps stay JSON (as JSONB `trade_detail`), NULL when
     both are empty -- which is 64.1% of rows.
  2. kill_impact/death_impact/impact float8 -> int4. The scorer round()s all
     three and all 652,730 rows were verified integral (range -652..3053).
  3. The surrogate `id` is dropped in favour of the natural
     (round_id, match_player_id) PK. `id` was referenced nowhere in app/ or
     scripts/, and impact_scores_pkey had 0 index scans against 446,891 on
     uq_impact_round_match_player -- so two 14 MB indexes become one.

Built as CREATE TABLE AS + swap rather than ALTER + DROP COLUMN, because
dropping a column does NOT reclaim its bytes: Postgres just marks it dropped
and leaves the data in every tuple until a rewrite. VACUUM FULL would work but
temporarily doubles the table. This is one pass, and the resulting table is
small.

Expected: 271 MB -> ~70 MB.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# int4 columns are selected before the int2 block so Postgres does not insert
# alignment padding -- interleaving them costs ~12 bytes/row for the same data.
_SCALARS = (
    "damage",
    "econ_impact",
    "time_impact",
    "swing_impact",
    "econ_kill",
    "econ_death",
    "clutch_kill",
    "clutch_death",
    "post_plant_kill",
    "post_plant_death",
    "traded_teammate",
    "traded_by_teammate",
)

_NOT_NULL_COLUMNS = (
    "round_id",
    "match_player_id",
    "kill_impact",
    "death_impact",
    "impact",
    *_SCALARS,
)


def upgrade() -> None:
    scalar_select = ",\n            ".join(
        f"COALESCE((breakdown->>'{key}')::numeric, 0)::smallint AS {key}"
        for key in _SCALARS
    )

    op.execute(
        f"""
        CREATE TABLE impact_scores_new AS
        SELECT
            round_id,
            match_player_id,
            kill_impact::integer  AS kill_impact,
            death_impact::integer AS death_impact,
            impact::integer       AS impact,
            {scalar_select},
            CASE
                WHEN COALESCE(breakdown->>'traded_teammate_targets', '{{}}') = '{{}}'
                 AND COALESCE(breakdown->>'traded_by_teammate_sources', '{{}}') = '{{}}'
                THEN NULL
                ELSE jsonb_build_object(
                    't', COALESCE((breakdown->>'traded_teammate_targets')::jsonb, '{{}}'::jsonb),
                    's', COALESCE((breakdown->>'traded_by_teammate_sources')::jsonb, '{{}}'::jsonb)
                )
            END AS trade_detail
        FROM impact_scores
        """
    )

    # CREATE TABLE AS carries no constraints, so every one is restored by hand.
    # Missing an FK here would silently lose referential integrity.
    #
    # SET NOT NULL scans the table and takes ACCESS EXCLUSIVE, so it happens
    # here -- while impact_scores_new is still private to this transaction and
    # nothing else can be waiting on it -- rather than after the swap.
    for column in _NOT_NULL_COLUMNS:
        op.execute(f"ALTER TABLE impact_scores_new ALTER COLUMN {column} SET NOT NULL")

    # Constraint names are schema-scoped, so these cannot take their final
    # names while the old table still holds them -- build under temporary
    # names and rename after the swap.
    op.execute(
        "ALTER TABLE impact_scores_new "
        "ADD CONSTRAINT impact_scores_new_pkey PRIMARY KEY (round_id, match_player_id)"
    )
    op.execute(
        "ALTER TABLE impact_scores_new "
        "ADD CONSTRAINT impact_scores_new_round_id_fkey "
        "FOREIGN KEY (round_id) REFERENCES rounds(id)"
    )
    op.execute(
        "ALTER TABLE impact_scores_new "
        "ADD CONSTRAINT impact_scores_new_match_player_id_fkey "
        "FOREIGN KEY (match_player_id) REFERENCES match_players(id)"
    )

    # The swap. DROP takes ACCESS EXCLUSIVE, so keep it as the last, shortest
    # step -- the CREATE TABLE AS above only holds ACCESS SHARE on the source,
    # which does not block reads or writes.
    op.execute("DROP TABLE impact_scores")
    op.execute("ALTER TABLE impact_scores_new RENAME TO impact_scores")

    # Renaming a PK/unique constraint renames its underlying index too.
    op.execute(
        "ALTER TABLE impact_scores RENAME CONSTRAINT impact_scores_new_pkey TO impact_scores_pkey"
    )
    op.execute(
        "ALTER TABLE impact_scores RENAME CONSTRAINT "
        "impact_scores_new_round_id_fkey TO impact_scores_round_id_fkey"
    )
    op.execute(
        "ALTER TABLE impact_scores RENAME CONSTRAINT "
        "impact_scores_new_match_player_id_fkey TO impact_scores_match_player_id_fkey"
    )

    # Postgres 17+ catalogues NOT NULL as named constraints, so these were also
    # created carrying the temporary table name. Renaming is catalog-only --
    # no scan, no meaningful lock -- unlike SET NOT NULL itself.
    for column in _NOT_NULL_COLUMNS:
        op.execute(
            f"ALTER TABLE impact_scores RENAME CONSTRAINT "
            f"impact_scores_new_{column}_not_null TO impact_scores_{column}_not_null"
        )


def downgrade() -> None:
    scalar_json = ",\n                ".join(f"'{key}', {key}" for key in _SCALARS)

    op.execute(
        f"""
        CREATE TABLE impact_scores_old AS
        SELECT
            row_number() OVER (ORDER BY round_id, match_player_id)::integer AS id,
            round_id,
            match_player_id,
            kill_impact::double precision  AS kill_impact,
            death_impact::double precision AS death_impact,
            impact::double precision       AS impact,
            (jsonb_build_object(
                {scalar_json},
                'traded_teammate_targets',
                    COALESCE(trade_detail->'t', '{{}}'::jsonb),
                'traded_by_teammate_sources',
                    COALESCE(trade_detail->'s', '{{}}'::jsonb)
            ))::json AS breakdown
        FROM impact_scores
        """
    )

    _OLD_NOT_NULL = ("id", "round_id", "match_player_id", "kill_impact", "death_impact", "impact")
    for column in _OLD_NOT_NULL:
        op.execute(f"ALTER TABLE impact_scores_old ALTER COLUMN {column} SET NOT NULL")

    # Temporary names for the same reason as upgrade(): the live table still
    # owns impact_scores_pkey and the FK names until it is dropped.
    op.execute("ALTER TABLE impact_scores_old ADD CONSTRAINT impact_scores_old_pkey PRIMARY KEY (id)")
    op.execute(
        "ALTER TABLE impact_scores_old "
        "ADD CONSTRAINT uq_impact_round_match_player UNIQUE (round_id, match_player_id)"
    )
    op.execute(
        "ALTER TABLE impact_scores_old "
        "ADD CONSTRAINT impact_scores_old_round_id_fkey FOREIGN KEY (round_id) REFERENCES rounds(id)"
    )
    op.execute(
        "ALTER TABLE impact_scores_old "
        "ADD CONSTRAINT impact_scores_old_match_player_id_fkey "
        "FOREIGN KEY (match_player_id) REFERENCES match_players(id)"
    )

    op.execute("DROP TABLE impact_scores")
    op.execute("ALTER TABLE impact_scores_old RENAME TO impact_scores")

    op.execute(
        "ALTER TABLE impact_scores RENAME CONSTRAINT impact_scores_old_pkey TO impact_scores_pkey"
    )
    op.execute(
        "ALTER TABLE impact_scores RENAME CONSTRAINT "
        "impact_scores_old_round_id_fkey TO impact_scores_round_id_fkey"
    )
    op.execute(
        "ALTER TABLE impact_scores RENAME CONSTRAINT "
        "impact_scores_old_match_player_id_fkey TO impact_scores_match_player_id_fkey"
    )
    for column in _OLD_NOT_NULL:
        op.execute(
            f"ALTER TABLE impact_scores RENAME CONSTRAINT "
            f"impact_scores_old_{column}_not_null TO impact_scores_{column}_not_null"
        )

    # Restore the identity sequence the original table's `id` column had.
    op.execute("CREATE SEQUENCE impact_scores_id_seq OWNED BY impact_scores.id")
    op.execute(
        "ALTER TABLE impact_scores "
        "ALTER COLUMN id SET DEFAULT nextval('impact_scores_id_seq')"
    )
    op.execute(
        "SELECT setval('impact_scores_id_seq', COALESCE((SELECT MAX(id) FROM impact_scores), 1))"
    )
