"""Tests for the read-only query functions in whatsapp.py.

Root cause under test: every read function wrapped its SQLite query in
`except sqlite3.Error: return []` (or `return None`), so a real failure to
open the database (e.g. a wrong/relocated MESSAGES_DB_PATH) was silently
turned into an empty result instead of surfacing. That's how `list_chats`
could return `{"result": []}` with a healthy database sitting right there.

Fixture: a temp SQLite DB built from the real `chats`/`messages` schema
(no real message content), so queries run against the actual table shape.
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
    """A DB path whose parent directory doesn't exist, so sqlite3.connect
    fails the way it does when a package is loaded from a relocated install
    (the actual bug: MESSAGES_DB_PATH resolved relative to a copy of
    whatsapp.py that isn't sitting next to whatsapp-bridge/)."""
    bad_path = tmp_path / "nowhere" / "messages.db"
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(bad_path))
    return bad_path


# --- positive cases: real schema, real query paths --------------------------

def test_list_chats_no_query_returns_seeded_chats(seeded_db):
    chats = whatsapp.list_chats(limit=15)
    names = [c.name for c in chats]
    assert "Test Group :)" in names
    assert "Alice Example" in names


def test_list_chats_query_matches_by_name_substring(seeded_db):
    chats = whatsapp.list_chats(query="group", limit=15)
    assert [c.jid for c in chats] == [GROUP_JID]


def test_list_chats_query_no_match_returns_empty_not_error(seeded_db):
    # "eria" matches nothing in this fixture; empty is correct, not a bug.
    assert whatsapp.list_chats(query="eria", limit=15) == []


def test_list_messages_query_matches_content(seeded_db):
    output = whatsapp.list_messages(query="hello group", limit=5, include_context=False)
    assert "hello group" in output


def test_search_contacts_finds_direct_not_group(seeded_db):
    contacts = whatsapp.search_contacts("Alice")
    assert [c.jid for c in contacts] == [DIRECT_JID]


def test_get_chat_by_jid(seeded_db):
    chat = whatsapp.get_chat(GROUP_JID)
    assert chat.name == "Test Group :)"
    assert chat.last_message == "Thanks"


def test_get_direct_chat_by_contact(seeded_db):
    chat = whatsapp.get_direct_chat_by_contact("222")
    assert chat.jid == DIRECT_JID


def test_get_contact_chats(seeded_db):
    chats = whatsapp.get_contact_chats(GROUP_JID, limit=5)
    assert any(c.jid == GROUP_JID for c in chats)


def test_get_last_interaction(seeded_db):
    result = whatsapp.get_last_interaction(GROUP_JID)
    assert "Thanks" in result


def test_get_message_context(seeded_db):
    context = whatsapp.get_message_context("m1", before=2, after=2)
    assert context.message.content == "Thanks"
    assert [m.content for m in context.before] == ["hello group"]


# --- negative cases: a real DB failure must surface, never come back as [] --

def test_list_chats_surfaces_db_open_error(missing_db):
    with pytest.raises(sqlite3.Error):
        whatsapp.list_chats(limit=15)


def test_list_messages_surfaces_db_open_error(missing_db):
    with pytest.raises(sqlite3.Error):
        whatsapp.list_messages(limit=15, include_context=False)


def test_search_contacts_surfaces_db_open_error(missing_db):
    with pytest.raises(sqlite3.Error):
        whatsapp.search_contacts("anything")


def test_get_chat_surfaces_db_open_error(missing_db):
    with pytest.raises(sqlite3.Error):
        whatsapp.get_chat(GROUP_JID)


def test_get_direct_chat_by_contact_surfaces_db_open_error(missing_db):
    with pytest.raises(sqlite3.Error):
        whatsapp.get_direct_chat_by_contact("222")


def test_get_contact_chats_surfaces_db_open_error(missing_db):
    with pytest.raises(sqlite3.Error):
        whatsapp.get_contact_chats(GROUP_JID)


def test_get_last_interaction_surfaces_db_open_error(missing_db):
    with pytest.raises(sqlite3.Error):
        whatsapp.get_last_interaction(GROUP_JID)


def test_get_message_context_surfaces_db_open_error(missing_db):
    # Already correct before this fix (re-raises); kept as a regression guard.
    with pytest.raises(sqlite3.Error):
        whatsapp.get_message_context("m1")
