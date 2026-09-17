-- The release write gate: which build may write scores and ingested data.
--
-- Declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md.
--
-- WHY A TRIGGER AND NOT A CHECK CONSTRAINT. impact_scores.scoring_version says
-- which build wrote a row, but a row's own value cannot identify its writer: a
-- checkout whose model does not know the column can UPDATE a row that already
-- says 3, leave the value alone, and pass any CHECK on it. The gate therefore
-- asks the WRITER, through a setting only a verified preflight sets on its
-- connection, and refuses everything else -- including DELETE and TRUNCATE.
--
-- It is deliberately NOT an Alembic migration. `alembic downgrade` during a
-- rollback must not be able to lift the freeze, and the gate outlives the
-- columns it protects.
--
-- This protects against an accidental stale checkout. It is not a security
-- boundary: anyone who can edit scoring_gate can open it.
--
-- Apply with scripts/install_release_write_gate.py, which is idempotent.

CREATE TABLE IF NOT EXISTS scoring_gate (
    -- one row, enforced: `id` can only ever be true
    id          boolean     PRIMARY KEY DEFAULT true CHECK (id),
    state       text        NOT NULL CHECK (state IN ('closed', 'open')),
    release_id  text        NOT NULL,
    admin_id    text        NOT NULL,
    note        text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- The operation-state record. scripts/swap_impact_scores.py appends one entry
-- per build, verification, swap and rollback, and writes the swap's and the
-- rollback's entries INSIDE the transaction that makes the change: the entry
-- and the change commit together or not at all. After a lost connection this
-- table says whether a swap happened, so nobody repeats one blind.
CREATE TABLE IF NOT EXISTS scoring_release_log (
    id          bigserial   PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    operation   text        NOT NULL,
    outcome     text        NOT NULL,
    identity    text,
    details     jsonb       NOT NULL
);

CREATE OR REPLACE FUNCTION scoring_gate_guard() RETURNS trigger
LANGUAGE plpgsql AS $guard$
DECLARE
    gate  scoring_gate%ROWTYPE;
    who   text := current_setting('valo.write_release', true);
BEGIN
    SELECT * INTO gate FROM scoring_gate WHERE id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'the scoring gate row is missing, so % on % is refused',
            TG_OP, TG_TABLE_NAME
            USING HINT = 'run scripts/install_release_write_gate.py';
    END IF;

    -- The runbook's own identity: swaps, rollbacks and deliberate manual fixes.
    IF who IS NOT NULL AND who = gate.admin_id THEN
        RETURN NULL;
    END IF;

    -- The open release: set only by an ingestion preflight that has verified
    -- the active manifest, its version and this gate.
    IF gate.state = 'open' AND who IS NOT NULL AND who = gate.release_id THEN
        RETURN NULL;
    END IF;

    RAISE EXCEPTION 'the release write gate refused % on %', TG_OP, TG_TABLE_NAME
        USING DETAIL = format('connection identity %s; gate %s for release %s',
                              coalesce(who, '(none)'), gate.state, gate.release_id),
              HINT = 'only a checkout whose preflight verified the active manifest may write';
END;
$guard$;

-- Statement-level: the decision depends on the writer, never on the row, so a
-- 659,500-row load pays for one check rather than 659,500.
DO $install$
DECLARE
    guarded text;
    op      text;
BEGIN
    FOREACH guarded IN ARRAY ARRAY[
        'impact_scores', 'matches', 'match_players', 'rounds',
        'round_player_stats', 'round_player_spend', 'kill_events'
    ] LOOP
        IF to_regclass('public.' || guarded) IS NULL THEN
            RAISE EXCEPTION 'cannot gate %: the table does not exist', guarded;
        END IF;
        FOREACH op IN ARRAY ARRAY['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'] LOOP
            EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                           'scoring_gate_' || lower(op), guarded);
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE %s ON %I FOR EACH STATEMENT '
                'EXECUTE FUNCTION scoring_gate_guard()',
                'scoring_gate_' || lower(op), op, guarded);
        END LOOP;
    END LOOP;
END;
$install$;
