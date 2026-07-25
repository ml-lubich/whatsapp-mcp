"""Shared pytest fixtures for the wa-cli test suite.

This file is intentionally kept minimal at scaffold time. Section markers
below delimit ownership to avoid merge conflicts between work packages:

- WP4 (db.py) owns the "db schema fixtures" block.
- Fake-process fixtures for daemon tests live in tests/test_daemon.py
  instead of here, to keep this file single-writer (WP4) per the plan.
"""

# --- db schema fixtures ---
# (filled by WP4: sample_dbs(tmp_path) fixture building whatsapp.db / messages.db
#  from the exact CREATE TABLE statements in spec section 2.3)

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

# Verbatim from spec section 2.3 (whatsapp-bridge/store/whatsapp.db).
_WHATSMEOW_CONTACTS_SCHEMA = """
CREATE TABLE whatsmeow_contacts (
    our_jid TEXT, their_jid TEXT, first_name TEXT, full_name TEXT,
    push_name TEXT, business_name TEXT, redacted_phone TEXT,
    PRIMARY KEY (our_jid, their_jid)
)
"""

# Verbatim from spec section 2.3 (whatsapp-bridge/store/messages.db).
_CHATS_SCHEMA = """
CREATE TABLE chats (
    jid TEXT PRIMARY KEY,
    name TEXT,
    last_message_time TIMESTAMP
)
"""

_MESSAGES_SCHEMA = """
CREATE TABLE messages (
    id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
    is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT,
    media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER,
    PRIMARY KEY (id, chat_jid),
    FOREIGN KEY (chat_jid) REFERENCES chats(jid)
)
"""

_OUR_JID = "15551234567:1@s.whatsapp.net"


@dataclass
class SampleDbs:
    """Paths to the tmp_path-built whatsapp.db / messages.db fixtures."""

    whatsapp_db: Path
    messages_db: Path


def _build_whatsapp_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_WHATSMEOW_CONTACTS_SCHEMA)
        conn.executemany(
            "INSERT INTO whatsmeow_contacts "
            "(our_jid, their_jid, first_name, full_name, push_name, "
            "business_name, redacted_phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                # @s.whatsapp.net contact, fully populated.
                (
                    _OUR_JID,
                    "14157863858@s.whatsapp.net",
                    "Alice",
                    "Alice Anderson",
                    "Ali",
                    "",
                    "+1 415 786 3858",
                ),
                # @lid contact — empty first_name, matches real majority-case data.
                (
                    _OUR_JID,
                    "100455141081206@lid",
                    "",
                    "Bob Baker",
                    "Bobby",
                    "",
                    "",
                ),
                # @g.us "contact" row (group), with a business_name populated.
                (
                    _OUR_JID,
                    "120363319322076067@g.us",
                    "",
                    "Acme Corp Group",
                    "Acme",
                    "Acme Corp",
                    "",
                ),
                # Duplicate their_jid under a different our_jid — dedupe target.
                (
                    "15559998888:1@s.whatsapp.net",
                    "14157863858@s.whatsapp.net",
                    "Alice",
                    "Alice Anderson",
                    "Ali",
                    "",
                    "+1 415 786 3858",
                ),
                # Contact matching only on push_name, otherwise blank.
                (
                    _OUR_JID,
                    "19998887777@s.whatsapp.net",
                    "",
                    "",
                    "Charlie",
                    "",
                    "",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _build_messages_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_CHATS_SCHEMA)
        conn.execute(_MESSAGES_SCHEMA)
        conn.executemany(
            "INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)",
            [
                (
                    "14157863858@s.whatsapp.net",
                    "Alice Anderson",
                    "2026-07-10 09:00:00-07:00",
                ),
                (
                    "100455141081206@lid",
                    "Bob Baker",
                    "2026-07-11 16:07:39-07:00",
                ),
                (
                    "120363319322076067@g.us",
                    "Acme Corp Group",
                    "2026-07-11 08:30:00-07:00",
                ),
                # Null name — display must fall back to the JID.
                (
                    "19998887777@s.whatsapp.net",
                    None,
                    "2026-01-01 00:00:00-08:00",
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                # Alice thread: one message from us, one reply from her, oldest first.
                (
                    "msg-1",
                    "14157863858@s.whatsapp.net",
                    _OUR_JID,
                    "Hey are we still on for Friday?",
                    "2026-07-20 10:00:00-07:00",
                    1,
                ),
                (
                    "msg-2",
                    "14157863858@s.whatsapp.net",
                    "14157863858@s.whatsapp.net",
                    "Yes, see you then!",
                    "2026-07-20 10:05:00-07:00",
                    0,
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def sample_dbs(tmp_path: Path) -> SampleDbs:
    """Build whatsapp.db and messages.db in tmp_path using the exact real
    schemas from spec section 2.3, seeded with rows covering all three JID
    forms (@s.whatsapp.net, @lid, @g.us) and ISO-8601 offset timestamps.
    """
    whatsapp_db = tmp_path / "whatsapp.db"
    messages_db = tmp_path / "messages.db"
    _build_whatsapp_db(whatsapp_db)
    _build_messages_db(messages_db)
    return SampleDbs(whatsapp_db=whatsapp_db, messages_db=messages_db)
