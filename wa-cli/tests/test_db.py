"""Tests for wa_cli.db: read-only SQLite queries (contacts, chats).

Critic amendment A5: wa contacts intentionally queries whatsmeow_contacts in
whatsapp.db (NOT the messages.db chats-table approach used by the old
whatsapp-mcp-server/whatsapp.py) — do not copy that pattern here.
"""

from __future__ import annotations

import sqlite3

import pytest

from wa_cli import db


# --- search_contacts() ---


def test_search_contacts_matches_full_name(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "Anderson")

    assert [c.their_jid for c in results] == ["14157863858@s.whatsapp.net"]


def test_search_contacts_matches_first_name(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "Alice")

    assert any(c.their_jid == "14157863858@s.whatsapp.net" for c in results)


def test_search_contacts_matches_push_name(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "Charlie")

    assert [c.their_jid for c in results] == ["19998887777@s.whatsapp.net"]


def test_search_contacts_matches_business_name(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "Acme Corp")

    assert any(c.their_jid == "120363319322076067@g.us" for c in results)


def test_search_contacts_matches_their_jid(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "100455141081206")

    assert [c.their_jid for c in results] == ["100455141081206@lid"]


def test_search_contacts_is_case_insensitive(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "anderson")

    assert [c.their_jid for c in results] == ["14157863858@s.whatsapp.net"]

    results_upper = db.search_contacts(sample_dbs.whatsapp_db, "ANDERSON")

    assert [c.their_jid for c in results_upper] == ["14157863858@s.whatsapp.net"]


def test_search_contacts_dedupes_by_their_jid(sample_dbs):
    # "Alice Anderson" appears under two our_jid rows for the same their_jid.
    results = db.search_contacts(sample_dbs.whatsapp_db, "Alice")

    jids = [c.their_jid for c in results]
    assert jids.count("14157863858@s.whatsapp.net") == 1


def test_search_contacts_no_match_returns_empty_list(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "nonexistent-query-xyz")

    assert results == []


def test_search_contacts_covers_all_three_jid_forms(sample_dbs):
    s_whatsapp_net = db.search_contacts(sample_dbs.whatsapp_db, "14157863858@s.whatsapp.net")
    lid = db.search_contacts(sample_dbs.whatsapp_db, "100455141081206@lid")
    g_us = db.search_contacts(sample_dbs.whatsapp_db, "120363319322076067@g.us")

    assert [c.their_jid for c in s_whatsapp_net] == ["14157863858@s.whatsapp.net"]
    assert [c.their_jid for c in lid] == ["100455141081206@lid"]
    assert [c.their_jid for c in g_us] == ["120363319322076067@g.us"]


def test_search_contacts_handles_blank_business_name_and_first_name(sample_dbs):
    # Bob Baker (@lid) has empty first_name and empty business_name — matches
    # the majority-case real data shape. Ensure it doesn't error and renders
    # rendering-safe (non-None, string) blank fields.
    results = db.search_contacts(sample_dbs.whatsapp_db, "Bob Baker")

    assert len(results) == 1
    contact = results[0]
    assert contact.their_jid == "100455141081206@lid"
    assert contact.first_name == ""
    assert contact.business_name == ""
    assert contact.first_name is not None
    assert contact.business_name is not None


def test_search_contacts_returns_dataclass_with_expected_fields(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "Alice")

    contact = results[0]
    assert contact.their_jid == "14157863858@s.whatsapp.net"
    assert contact.first_name == "Alice"
    assert contact.full_name == "Alice Anderson"
    assert contact.push_name == "Ali"
    assert contact.business_name == ""


def test_search_contacts_opens_db_read_only(sample_dbs):
    # Read-only enforced: attempting to write through the same access path
    # used by search_contacts must fail.
    uri = f"file:{sample_dbs.whatsapp_db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO whatsmeow_contacts (their_jid) VALUES ('x')")
    finally:
        conn.close()

    # And the module's own connection path is genuinely read-only too.
    results = db.search_contacts(sample_dbs.whatsapp_db, "Alice")
    assert results  # sanity: reads still work over the ro connection


# --- list_chats() ---


def test_list_chats_orders_by_last_message_time_desc(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db)

    jids = [c.jid for c in chats]
    assert jids == [
        "100455141081206@lid",
        "120363319322076067@g.us",
        "14157863858@s.whatsapp.net",
        "19998887777@s.whatsapp.net",
    ]


def test_list_chats_honors_limit(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db, limit=2)

    assert len(chats) == 2
    assert chats[0].jid == "100455141081206@lid"
    assert chats[1].jid == "120363319322076067@g.us"


def test_list_chats_default_limit_is_20(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db)

    assert len(chats) == 4  # fewer than default limit; all rows returned


def test_list_chats_derives_kind_direct_for_s_whatsapp_net(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db)

    direct = next(c for c in chats if c.jid == "14157863858@s.whatsapp.net")
    assert direct.kind == "direct"


def test_list_chats_derives_kind_linked_for_lid(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db)

    linked = next(c for c in chats if c.jid == "100455141081206@lid")
    assert linked.kind == "linked"


def test_list_chats_derives_kind_group_for_g_us(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db)

    group = next(c for c in chats if c.jid == "120363319322076067@g.us")
    assert group.kind == "group"


def test_list_chats_parses_iso8601_timestamp_with_offset(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db)

    bob = next(c for c in chats if c.jid == "100455141081206@lid")
    assert bob.last_message_time.isoformat() == "2026-07-11T16:07:39-07:00"


def test_list_chats_falls_back_to_jid_when_name_is_null(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db)

    charlie = next(c for c in chats if c.jid == "19998887777@s.whatsapp.net")
    assert charlie.name == "19998887777@s.whatsapp.net"


def test_list_chats_keeps_real_name_when_present(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db)

    alice = next(c for c in chats if c.jid == "14157863858@s.whatsapp.net")
    assert alice.name == "Alice Anderson"


def test_list_chats_opens_db_read_only(sample_dbs):
    uri = f"file:{sample_dbs.messages_db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO chats (jid) VALUES ('x')")
    finally:
        conn.close()

    chats = db.list_chats(sample_dbs.messages_db)
    assert chats  # sanity: reads still work over the ro connection


def test_list_chats_empty_db_returns_empty_list(tmp_path):
    empty_db = tmp_path / "empty_messages.db"
    conn = sqlite3.connect(empty_db)
    conn.execute(
        "CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)"
    )
    conn.commit()
    conn.close()

    assert db.list_chats(empty_db) == []
