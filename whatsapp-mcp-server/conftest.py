"""Shared fixtures for the whatsapp-mcp-server test suite.

Same schema/fixture shape as test_reads.py (kept local there for that file's
own history); new test files import these instead of redefining them.
"""

from __future__ import annotations

import sqlite3

import pytest

import whatsapp

SCHEMA = """
CREATE TABLE chats (
    jid TEXT PRIMARY KEY,
    name TEXT,
    last_message_time TIMESTAMP
);
CREATE TABLE messages (
    id TEXT,
    chat_jid TEXT,
    sender TEXT,
    content TEXT,
    timestamp TIMESTAMP,
    is_from_me BOOLEAN,
    media_type TEXT,
    filename TEXT,
    url TEXT,
    media_key BLOB,
    file_sha256 BLOB,
    file_enc_sha256 BLOB,
    file_length INTEGER,
    sender_jid TEXT,
    PRIMARY KEY (id, chat_jid),
    FOREIGN KEY (chat_jid) REFERENCES chats(jid)
);
"""

GROUP_JID = "111@g.us"
DIRECT_JID = "222@s.whatsapp.net"


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db_path = tmp_path / "messages.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)",
        [
            (GROUP_JID, "Test Group :)", "2026-09-24 15:27:22-07:00"),
            (DIRECT_JID, "Alice Example", "2026-09-20 10:00:00-07:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("m1", GROUP_JID, "333@s.whatsapp.net", "Thanks", "2026-09-24 15:27:22-07:00", 1),
            ("m2", GROUP_JID, "333@s.whatsapp.net", "hello group", "2026-09-24 15:20:00-07:00", 0),
            ("m3", DIRECT_JID, DIRECT_JID, "hi alice", "2026-09-20 10:00:00-07:00", 0),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(db_path))
    return db_path


@pytest.fixture
def missing_db(tmp_path, monkeypatch):
    """A DB path whose parent directory doesn't exist, so sqlite3.connect fails."""
    bad_path = tmp_path / "nowhere" / "messages.db"
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(bad_path))
    return bad_path
