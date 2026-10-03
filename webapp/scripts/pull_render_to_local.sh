#!/bin/sh
# Helper for pull_render_to_local.ps1. Runs inside a postgres:18 container.
#
#   sh pull_render_to_local.sh dump <dump-file> <snapshot-file>    # pg_dump $RURL, then snapshot it
#   sh pull_render_to_local.sh snapshot <snapshot-file>            # snapshot $RURL only
#
# Read-only against $RURL: pg_dump and SELECTs. The snapshot (alembic head, latest
# match, every table's row count, every sequence position) is what the local copy
# is diffed against after the restore.
set -e

snapshot() {
  out="$1"
  : > "$out"
  psql "$RURL" -Atc "select 'alembic ' || version_num from alembic_version" >> "$out"
  psql "$RURL" -Atc "select 'max_played_at ' || max(played_at) from matches" >> "$out"
  for t in $(psql "$RURL" -Atc "select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1"); do
    echo "table $t $(psql "$RURL" -Atc "select count(*) from \"$t\"")" >> "$out"
  done
  psql "$RURL" -Atc "select 'seq ' || sequencename || ' ' || coalesce(last_value::text, 'null') from pg_sequences where schemaname='public' order by 1" >> "$out"
}

case "$1" in
  dump)
    pg_dump --format=custom --no-owner --no-privileges -d "$RURL" -f "$2"
    snapshot "$3"
    ;;
  snapshot)
    snapshot "$2"
    ;;
  *)
    echo "usage: $0 dump <dump-file> <snapshot-file> | snapshot <snapshot-file>" >&2
    exit 2
    ;;
esac
