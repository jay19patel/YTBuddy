"""Migrations preserve real data from the pre-migration app; logs never carry secrets."""

import json
import logging
import os
import sqlite3

import pytest

from ytstudio.adapters.token_vault import DbTokenVault, TokenCipher
from ytstudio.cli import migrate
from ytstudio.config import get_settings
from ytstudio.logging_setup import JsonFormatter, redact

LEGACY_SCHEMA = """
CREATE TABLE channel (id VARCHAR NOT NULL PRIMARY KEY, title VARCHAR NOT NULL, thumb VARCHAR NOT NULL,
  creds VARCHAR NOT NULL, subscribers INTEGER NOT NULL, views INTEGER NOT NULL, videos INTEGER NOT NULL,
  stats_at DATETIME);
CREATE TABLE snapshot (channel_id VARCHAR NOT NULL, day DATE NOT NULL, subscribers INTEGER NOT NULL,
  views INTEGER NOT NULL, videos INTEGER NOT NULL, PRIMARY KEY (channel_id, day));
CREATE TABLE analyticscache (channel_id VARCHAR NOT NULL, days INTEGER NOT NULL, data VARCHAR NOT NULL,
  fetched_at DATETIME NOT NULL, PRIMARY KEY (channel_id, days));
CREATE TABLE settings (id INTEGER NOT NULL PRIMARY KEY, stats_minutes INTEGER NOT NULL,
  analytics_minutes INTEGER NOT NULL,
  stats_enabled BOOLEAN NOT NULL, analytics_enabled BOOLEAN NOT NULL, currency VARCHAR NOT NULL);
"""
LEGACY_CREDS = {
    "token": "ya29.old",
    "refresh_token": "1//refresh",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_id": "cid",
    "client_secret": "LEAKED",
    "scopes": ["https://www.googleapis.com/auth/youtube"],
    "expiry": "2026-11-11T11:52:43Z",
}


def test_legacy_database_is_stamped_upgraded_and_tokens_encrypted(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(LEGACY_SCHEMA)
    con.execute("INSERT INTO channel VALUES ('UC1','One','t',?,5,50,2,NULL)", (json.dumps(LEGACY_CREDS),))
    con.execute("INSERT INTO snapshot VALUES ('UC1','2026-11-10',5,50,2)")
    con.commit()
    con.close()
    tokens = tmp_path / "tokens"
    tokens.mkdir()
    (tokens / "UC2.json").write_text(json.dumps({"id": "UC2", "title": "Two", "thumb": "t", "creds": LEGACY_CREDS}))
    (tokens / "UC1.json").write_text(json.dumps({"id": "UC1", "title": "Dup", "thumb": "t", "creds": LEGACY_CREDS}))
    monkeypatch.setenv("LEGACY_TOKENS_DIR", str(tokens))
    get_settings.cache_clear()

    migrate(f"sqlite:///{db}")

    con = sqlite3.connect(db)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0005"
    rows = dict(con.execute("SELECT id, creds FROM channel").fetchall())
    assert set(rows) == {"UC1", "UC2"}
    assert all(c.startswith("v1:") and "LEAKED" not in c and "refresh" not in c for c in rows.values())
    assert con.execute("SELECT title, subscribers FROM channel WHERE id='UC1'").fetchone() == ("One", 5)  # kept
    assert con.execute("SELECT count(*) FROM snapshot").fetchone()[0] == 1
    con.close()
    tok = DbTokenVault(f"sqlite:///{db}", TokenCipher(os.environ["TOKEN_ENCRYPTION_KEY"])).load("UC1")
    assert tok.refresh_token == "1//refresh" and tok.scopes == LEGACY_CREDS["scopes"]

    migrate(f"sqlite:///{db}")  # idempotent
    get_settings.cache_clear()


def test_migration_refuses_plaintext_tokens_without_a_key(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(LEGACY_SCHEMA)
    con.execute("INSERT INTO channel VALUES ('UC1','One','t',?,5,50,2,NULL)", (json.dumps(LEGACY_CREDS),))
    con.commit()
    con.close()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY"):
        migrate(f"sqlite:///{db}")
    get_settings.cache_clear()
    con = sqlite3.connect(db)
    assert (
        json.loads(con.execute("SELECT creds FROM channel").fetchone()[0])["refresh_token"] == "1//refresh"
    )  # untouched


def test_key_rotation(tmp_path):
    from cryptography.fernet import Fernet

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    blob = TokenCipher(old).encrypt({"refresh_token": "r"})
    both = TokenCipher(f"{new},{old}")
    rotated = both.rotate(blob)
    assert TokenCipher(new).decrypt(rotated) == {"refresh_token": "r"}


@pytest.mark.parametrize(
    "secret",
    [
        "ya29.a0AfH6SMBx",
        "1//0gAbcDEF-xyz",
        "refresh_token=abc123",
        '"client_secret": "shh"',
        "sk-ant-api03-abcdefghij",
        "code=4/0AbCd",
    ],
)
def test_redaction(secret):
    out = redact(f"failed with {secret} ok")
    assert "[REDACTED]" in out and secret.split("=")[-1].split(":")[-1].strip(' "') not in out


def test_json_formatter_redacts_messages_and_tracebacks():
    try:
        raise RuntimeError("token=supersecret")
    except RuntimeError:
        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "refresh_token=abc", (), None)
        import sys

        rec.exc_info = sys.exc_info()
    line = JsonFormatter().format(rec)
    assert "supersecret" not in line and "abc" not in json.loads(line)["msg"]
