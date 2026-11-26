"""create_readonly_role.py grants reading only, and builds the read-only login's URL from the owner's."""

import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import create_readonly_role  # noqa: E402


def test_role_url_swaps_only_the_login():
    url = create_readonly_role.role_url("postgresql://owner:secret@db.example:5432/valowithfriendsdb?sslmode=require",
                                        "p@ss/word")
    parts = urlsplit(url)
    assert parts.username == "probe_ro"
    assert unquote(parts.password) == "p@ss/word"
    assert parts.hostname == "db.example" and parts.port == 5432
    assert parts.path == "/valowithfriendsdb"
    assert parse_qs(parts.query) == {"sslmode": ["require"]}
    assert "owner" not in url and "secret" not in url


def test_setup_grants_select_and_nothing_that_writes():
    sql = "\n".join(create_readonly_role.setup_sql("valowithfriendsdb")).upper()
    for word in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALL PRIVILEGES", "SUPERUSER ", "CREATEROLE "):
        assert word not in sql.replace("NOSUPERUSER", "").replace("NOCREATEROLE", "")
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA PUBLIC" in sql
    assert "DEFAULT_TRANSACTION_READ_ONLY = ON" in sql
    assert "STATEMENT_TIMEOUT = '60S'" in sql


def test_setup_quotes_the_database_name():
    sql = create_readonly_role.setup_sql('we"ird')
    assert 'GRANT CONNECT ON DATABASE "we""ird" TO probe_ro' in sql
