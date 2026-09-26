"""Tests for wa_cli.db: read-only SQLite queries (contacts, chats).

Critic amendment A5: wa contacts intentionally queries whatsmeow_contacts in
whatsapp.db (NOT the messages.db chats-table approach used by the old
whatsapp-mcp-server/whatsapp.py) — do not copy that pattern here.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import conftest
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


# --- resolve_chat_jid() ---


def test_resolve_chat_jid_returns_jid_forms_as_is(sample_dbs):
    assert db.resolve_chat_jid(sample_dbs.messages_db, "14157863858@s.whatsapp.net") == (
        "14157863858@s.whatsapp.net"
    )


def test_resolve_chat_jid_resolves_plain_digits(sample_dbs):
    assert db.resolve_chat_jid(sample_dbs.messages_db, "14157863858") == "14157863858@s.whatsapp.net"


def test_resolve_chat_jid_returns_none_when_no_match(sample_dbs):
    assert db.resolve_chat_jid(sample_dbs.messages_db, "00000000000") is None


# --- recent_messages() ---


def test_recent_messages_returns_oldest_first(sample_dbs):
    messages = db.recent_messages(sample_dbs.messages_db, "14157863858@s.whatsapp.net")

    assert [m.text for m in messages] == [
        "Hey are we still on for Friday?",
        "Yes, see you then!",
    ]


def test_recent_messages_reports_is_from_me(sample_dbs):
    messages = db.recent_messages(sample_dbs.messages_db, "14157863858@s.whatsapp.net")

    assert messages[0].is_from_me is True
    assert messages[1].is_from_me is False


def test_recent_messages_honors_limit(sample_dbs):
    messages = db.recent_messages(sample_dbs.messages_db, "14157863858@s.whatsapp.net", limit=1)

    assert len(messages) == 1
    assert messages[0].text == "Yes, see you then!"


def test_recent_messages_empty_chat_returns_empty_list(sample_dbs):
    messages = db.recent_messages(sample_dbs.messages_db, "120363319322076067@g.us")

    assert messages == []


def test_list_chats_empty_db_returns_empty_list(tmp_path):
    empty_db = tmp_path / "empty_messages.db"
    conn = sqlite3.connect(empty_db)
    conn.execute(
        "CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)"
    )
    conn.commit()
    conn.close()

    assert db.list_chats(empty_db) == []


# --- search_contacts() edge cases ---


def test_search_contacts_empty_string_matches_all_distinct_contacts(sample_dbs):
    # like = "%%" — matches every row regardless of content; 5 rows dedupe
    # to 4 distinct their_jid (Alice's duplicate our_jid row collapses).
    results = db.search_contacts(sample_dbs.whatsapp_db, "")

    assert len(results) == 4


def test_search_contacts_whitespace_only_query_matches_fields_containing_a_space(sample_dbs):
    # " " is not "blank" to LIKE — it matches any field with a literal
    # space in it ("Alice Anderson", "Acme Corp Group", "Acme Corp").
    results = db.search_contacts(sample_dbs.whatsapp_db, " ")

    assert len(results) == 3


def test_search_contacts_query_with_embedded_newline_no_match(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "\n")

    assert results == []


def test_search_contacts_very_long_query_does_not_error(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "x" * 10_000)

    assert results == []


def test_search_contacts_leading_whitespace_in_query_is_not_trimmed(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, " Alice")

    assert results == []


def test_search_contacts_percent_in_query_is_a_sql_wildcard_not_literal(tmp_path):
    # The query is wrapped in `%...%` unescaped, so a literal "%" in a
    # search term is a SQL LIKE wildcard, not a literal character — real
    # LIKE semantics, not a bug: contact search is free text, not a value
    # that needs LIKE-metachar escaping per spec.
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(
        db_path,
        [
            ("our@x", "a@s.whatsapp.net", "", "50%", "", "", ""),
            ("our@x", "b@s.whatsapp.net", "", "50 discount", "", "", ""),
        ],
    )
    results = db.search_contacts(db_path, "50%")

    assert {c.their_jid for c in results} == {"a@s.whatsapp.net", "b@s.whatsapp.net"}


def test_search_contacts_underscore_in_query_is_a_sql_wildcard_not_literal(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(
        db_path,
        [
            ("our@x", "a@s.whatsapp.net", "", "a_b", "", "", ""),
            ("our@x", "b@s.whatsapp.net", "", "axb", "", "", ""),
        ],
    )
    results = db.search_contacts(db_path, "a_b")

    assert {c.their_jid for c in results} == {"a@s.whatsapp.net", "b@s.whatsapp.net"}


def test_search_contacts_bracket_characters_are_literal_not_wildcards(tmp_path):
    # SQLite LIKE has exactly two metachars (% and _) — no GLOB-style
    # "[...]" character classes — so brackets match literally.
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(
        db_path, [("our@x", "b@s.whatsapp.net", "", "[Team] Alice", "", "", "")]
    )
    results = db.search_contacts(db_path, "[Team]")

    assert [c.their_jid for c in results] == ["b@s.whatsapp.net"]


@pytest.mark.parametrize(
    "payload",
    ["' OR '1'='1", "'; DROP TABLE whatsmeow_contacts; --"],
)
def test_search_contacts_sql_injection_shaped_query_is_a_no_op(sample_dbs, payload):
    # `?` placeholders mean the payload is just literal text to LIKE-match
    # against — it must not error and must not return unrelated rows.
    results = db.search_contacts(sample_dbs.whatsapp_db, payload)

    assert results == []


def test_search_contacts_sql_injection_payload_does_not_drop_table(sample_dbs):
    db.search_contacts(sample_dbs.whatsapp_db, "'; DROP TABLE whatsmeow_contacts; --")

    # The table must still exist and be queryable afterwards.
    survivors = db.search_contacts(sample_dbs.whatsapp_db, "Alice")
    assert survivors


def test_search_contacts_unicode_emoji_name_matches(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(
        db_path, [("our@x", "emoji@s.whatsapp.net", "", "\U0001f600 Party", "", "", "")]
    )
    results = db.search_contacts(db_path, "\U0001f600")

    assert [c.their_jid for c in results] == ["emoji@s.whatsapp.net"]


def test_search_contacts_rtl_arabic_name_matches(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    name = "مرحبا"  # "مرحبا"
    conftest.build_contacts_db(db_path, [("our@x", "rtl@s.whatsapp.net", "", name, "", "", "")])

    results = db.search_contacts(db_path, name)

    assert [c.their_jid for c in results] == ["rtl@s.whatsapp.net"]


def test_search_contacts_nocase_folds_ascii_case(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(db_path, [("our@x", "ascii@s.whatsapp.net", "", "HELLO", "", "", "")])

    results = db.search_contacts(db_path, "hello")

    assert [c.their_jid for c in results] == ["ascii@s.whatsapp.net"]


def test_search_contacts_nocase_does_not_fold_non_ascii_case(tmp_path):
    # SQLite's builtin NOCASE collation only folds ASCII A-Z; it leaves
    # accented letters untouched, so "café" does not match a "CAFÉ" row —
    # real, documented SQLite behavior, not a bug in db.py.
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(db_path, [("our@x", "upper@s.whatsapp.net", "", "CAFÉ", "", "", "")])

    results = db.search_contacts(db_path, "café")

    assert results == []


def test_search_contacts_nocase_matches_same_accented_case(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(db_path, [("our@x", "upper@s.whatsapp.net", "", "CAFÉ", "", "", "")])

    results = db.search_contacts(db_path, "CAFÉ")

    assert [c.their_jid for c in results] == ["upper@s.whatsapp.net"]


@pytest.mark.parametrize("field", ["first_name", "full_name", "push_name", "business_name"])
def test_search_contacts_matches_each_field_independently(tmp_path, field):
    db_path = tmp_path / "whatsapp.db"
    values = {"first_name": "", "full_name": "", "push_name": "", "business_name": ""}
    values[field] = "UniqueMarker"
    conftest.build_contacts_db(
        db_path,
        [
            (
                "our@x",
                "row@s.whatsapp.net",
                values["first_name"],
                values["full_name"],
                values["push_name"],
                values["business_name"],
                "",
            )
        ],
    )
    results = db.search_contacts(db_path, "UniqueMarker")

    assert [c.their_jid for c in results] == ["row@s.whatsapp.net"]


def test_search_contacts_null_full_name_becomes_empty_string(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(db_path, [("our@x", "n@s.whatsapp.net", "First", None, "Push", "", "")])

    results = db.search_contacts(db_path, "n@s.whatsapp.net")

    assert results[0].full_name == ""


def test_search_contacts_null_push_name_becomes_empty_string(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(db_path, [("our@x", "n2@s.whatsapp.net", "First", "Full", None, "", "")])

    results = db.search_contacts(db_path, "n2@s.whatsapp.net")

    assert results[0].push_name == ""


def test_search_contacts_empty_database_returns_empty_list(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(db_path, [])

    assert db.search_contacts(db_path, "anything") == []


def test_search_contacts_orders_results_by_their_jid(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(
        db_path,
        [
            ("our@x", "zzz@s.whatsapp.net", "Match", "", "", "", ""),
            ("our@x", "aaa@s.whatsapp.net", "Match", "", "", "", ""),
            ("our@x", "mmm@s.whatsapp.net", "Match", "", "", "", ""),
        ],
    )
    results = db.search_contacts(db_path, "Match")

    assert [c.their_jid for c in results] == [
        "aaa@s.whatsapp.net",
        "mmm@s.whatsapp.net",
        "zzz@s.whatsapp.net",
    ]


def test_search_contacts_missing_db_file_raises_operational_error(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        db.search_contacts(tmp_path / "nope.db", "x")


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(name=st.text(min_size=1, max_size=100).filter(lambda s: "\x00" not in s))
def test_search_contacts_property_exact_name_always_finds_itself(tmp_path, name):
    # Whatever wildcard chars the name happens to contain, self-matching
    # a wildcard-widened LIKE pattern against the same string can only
    # become more permissive, never less — so this must always hold.
    db_path = tmp_path / f"{uuid.uuid4().hex}.db"
    conftest.build_contacts_db(db_path, [("our@x", "hyp@s.whatsapp.net", "", name, "", "", "")])

    results = db.search_contacts(db_path, name)

    assert any(c.their_jid == "hyp@s.whatsapp.net" for c in results)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(query=st.text(max_size=200).filter(lambda s: "\x00" not in s))
def test_search_contacts_property_never_raises_and_returns_list(sample_dbs, query):
    results = db.search_contacts(sample_dbs.whatsapp_db, query)

    assert isinstance(results, list)


# --- list_chats() edge cases ---


def test_list_chats_limit_zero_returns_empty(sample_dbs):
    assert db.list_chats(sample_dbs.messages_db, limit=0) == []


def test_list_chats_negative_limit_is_clamped_to_zero(sample_dbs):
    # Real bug found and fixed: SQLite treats ANY negative LIMIT as "no
    # limit at all", so a caller typo like `wa chats --limit -5` would
    # otherwise silently dump the entire chats table. db.list_chats now
    # clamps a negative limit to 0 before it reaches SQLite.
    assert db.list_chats(sample_dbs.messages_db, limit=-1) == []
    assert db.list_chats(sample_dbs.messages_db, limit=-100) == []


def test_list_chats_limit_larger_than_row_count_returns_all_rows(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db, limit=1000)

    assert len(chats) == 4


def test_list_chats_absurdly_large_limit_does_not_error_or_hang(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db, limit=10**9)

    assert len(chats) == 4


def test_list_chats_many_chats_ordering_at_scale(tmp_path):
    rows = [
        (f"{i:03d}0000000000@s.whatsapp.net", f"Contact {i}", f"2026-01-01 00:{i % 60:02d}:00-08:00")
        for i in range(80)
    ]
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(db_path, rows)

    chats = db.list_chats(db_path, limit=1000)

    assert len(chats) == 80
    times = [c.last_message_time for c in chats]
    assert times == sorted(times, reverse=True)


def test_list_chats_many_chats_limit_slices_the_most_recent(tmp_path):
    rows = [
        (f"{i:03d}0000000000@s.whatsapp.net", f"Contact {i}", f"2026-01-01 00:{i % 60:02d}:00-08:00")
        for i in range(80)
    ]
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(db_path, rows)

    chats = db.list_chats(db_path, limit=5)

    assert len(chats) == 5
    assert chats[0].jid == "0590000000000@s.whatsapp.net"  # minute 59, most recent


@pytest.mark.parametrize(
    "raw_timestamp",
    [
        "2026-01-01T00:00:00.123456-08:00",
        "2026-01-01 00:00:00",
        "2026-01-01T00:00:00Z",
        "2026-01-01",
    ],
    ids=["microseconds", "naive-no-offset", "z-suffix", "date-only"],
)
def test_list_chats_parses_real_world_timestamp_shapes(tmp_path, raw_timestamp):
    # datetime.fromisoformat on this project's Python (3.11+) accepts all
    # four shapes without raising. If the bridge ever writes a shape that
    # fromisoformat rejects (e.g. a two-digit year), list_chats would
    # raise ValueError — a real production risk to keep an eye on, though
    # not something to guard here since none of these observed shapes
    # actually fail.
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(db_path, [("only@s.whatsapp.net", "Only", raw_timestamp)])

    chats = db.list_chats(db_path)

    assert chats[0].last_message_time == datetime.fromisoformat(raw_timestamp)


def test_list_chats_unrecognized_jid_suffix_falls_back_to_direct(tmp_path):
    # _jid_kind only special-cases @g.us and @lid; any other suffix
    # (including a bogus/unknown one) falls through to "direct". Whether
    # that fallback is the right default for a genuinely unknown suffix is
    # a product judgment call — flagging it, not changing it here.
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(db_path, [("weird@bogus.domain", "Weird", "2026-01-01 00:00:00-08:00")])

    chats = db.list_chats(db_path)

    assert chats[0].kind == "direct"


def test_list_chats_unicode_emoji_chat_name(tmp_path):
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(db_path, [("e@g.us", "\U0001f389 Party Group", "2026-01-01 00:00:00-08:00")])

    chats = db.list_chats(db_path)

    assert chats[0].name == "\U0001f389 Party Group"


def test_list_chats_falls_back_to_jid_when_name_is_empty_string(tmp_path):
    # `if name else jid` treats "" the same as NULL, not just None.
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(db_path, [("e@s.whatsapp.net", "", "2026-01-01 00:00:00-08:00")])

    chats = db.list_chats(db_path)

    assert chats[0].name == "e@s.whatsapp.net"


def test_list_chats_missing_db_file_raises_operational_error(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        db.list_chats(tmp_path / "nope.db")


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(limit=st.integers(max_value=-1))
def test_list_chats_property_any_negative_limit_returns_empty(sample_dbs, limit):
    assert db.list_chats(sample_dbs.messages_db, limit=limit) == []


# --- resolve_chat_jid() edge cases ---


@pytest.mark.parametrize(
    "recipient",
    ["x@s.whatsapp.net", "x@g.us", "x@lid", "x@bogus.domain"],
)
def test_resolve_chat_jid_at_sign_shortcut_returns_as_is_regardless_of_validity(sample_dbs, recipient):
    # Documented current behavior: any recipient containing "@" is
    # returned unchanged, with zero validation of the domain part — even
    # a made-up one.
    assert db.resolve_chat_jid(sample_dbs.messages_db, recipient) == recipient


def test_resolve_chat_jid_bare_at_sign_returned_as_is(sample_dbs):
    assert db.resolve_chat_jid(sample_dbs.messages_db, "@") == "@"


def test_resolve_chat_jid_multiple_at_signs_returned_as_is(sample_dbs):
    assert db.resolve_chat_jid(sample_dbs.messages_db, "a@b@c") == "a@b@c"


def test_resolve_chat_jid_at_sign_shortcut_wins_even_for_a_real_phone_with_wrong_domain(sample_dbs):
    result = db.resolve_chat_jid(sample_dbs.messages_db, "14157863858@wrong.domain")

    assert result == "14157863858@wrong.domain"


@pytest.mark.parametrize(
    "recipient",
    [
        "+14157863858",
        "1-415-786-3858",
        "1 415 786 3858",
        "+1 (415) 786-3858",
        "1.415.786.3858",
    ],
    ids=["leading-plus", "dashes", "spaces", "plus-parens-dash", "dots"],
)
def test_resolve_chat_jid_strips_various_non_digit_separators(sample_dbs, recipient):
    # Real bug found and fixed: the raw `%recipient%` LIKE match would
    # otherwise never match a plain-digit-stored JID against a
    # human-typed number with punctuation. resolve_chat_jid now strips
    # non-digit characters before the LIKE match.
    assert db.resolve_chat_jid(sample_dbs.messages_db, recipient) == "14157863858@s.whatsapp.net"


def test_resolve_chat_jid_leading_zeros_do_not_match_unpadded_jid(sample_dbs):
    # Documented behavior, not fixed: a leading zero is a real digit to
    # the LIKE match, and the stored JID has none, so this legitimately
    # finds nothing. Stripping leading zeros would be guessing about a
    # phone-number format this module has no business assuming.
    assert db.resolve_chat_jid(sample_dbs.messages_db, "014157863858") is None


def test_resolve_chat_jid_empty_string_matches_first_row_arbitrarily(sample_dbs):
    # An empty recipient strips to an empty digit string, so the LIKE
    # pattern becomes "%%" — matches every row; `LIMIT 1` (no ORDER BY)
    # returns whichever one SQLite happens to pick first. Documented, not
    # a crash.
    assert db.resolve_chat_jid(sample_dbs.messages_db, "") is not None


def test_resolve_chat_jid_whitespace_only_behaves_like_empty_string(sample_dbs):
    assert db.resolve_chat_jid(sample_dbs.messages_db, "   ") is not None


def test_resolve_chat_jid_very_long_numeric_string_no_match(sample_dbs):
    assert db.resolve_chat_jid(sample_dbs.messages_db, "9" * 500) is None


def test_resolve_chat_jid_unicode_digits_do_not_match_ascii_jid(sample_dbs):
    # Python's \D (used to strip non-digits) treats any Unicode decimal
    # digit as a digit, so Arabic-Indic numerals pass through unstripped
    # rather than being normalized to ASCII — and since the JID is stored
    # in ASCII digits, this legitimately finds nothing. Documented, not
    # fixed: normalizing Unicode digit scripts is out of scope for a
    # one-line strip.
    arabic_digits = "٤١٥٧٨٦٣٨٥٨"
    assert db.resolve_chat_jid(sample_dbs.messages_db, arabic_digits) is None


def test_resolve_chat_jid_no_match_after_normalization_returns_none(sample_dbs):
    assert db.resolve_chat_jid(sample_dbs.messages_db, "+1 (000) 000-0000") is None


def test_resolve_chat_jid_missing_db_file_raises_operational_error(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        db.resolve_chat_jid(tmp_path / "nope.db", "12345")


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    digits=st.text(alphabet="0123456789", min_size=5, max_size=15),
    seps=st.lists(st.sampled_from([" ", "-", "+"]), min_size=0, max_size=5),
)
def test_resolve_chat_jid_property_separators_do_not_change_the_result(tmp_path, digits, seps):
    db_path = tmp_path / f"{uuid.uuid4().hex}.db"
    conftest.build_chats_db(
        db_path, [(f"{digits}@s.whatsapp.net", "Name", "2026-01-01 00:00:00-08:00")]
    )
    decorated = "".join(seps) + digits + "".join(seps)

    plain_result = db.resolve_chat_jid(db_path, digits)
    decorated_result = db.resolve_chat_jid(db_path, decorated)

    assert plain_result == decorated_result == f"{digits}@s.whatsapp.net"


# --- recent_messages() edge cases ---


def test_recent_messages_limit_zero_returns_empty(sample_dbs):
    assert db.recent_messages(sample_dbs.messages_db, "14157863858@s.whatsapp.net", limit=0) == []


def test_recent_messages_negative_limit_is_clamped_to_zero(sample_dbs):
    assert db.recent_messages(sample_dbs.messages_db, "14157863858@s.whatsapp.net", limit=-5) == []


def test_recent_messages_huge_limit_returns_all_and_does_not_hang(sample_dbs):
    messages = db.recent_messages(sample_dbs.messages_db, "14157863858@s.whatsapp.net", limit=10**9)

    assert len(messages) == 2


def test_recent_messages_empty_chat_jid_returns_empty_list(sample_dbs):
    assert db.recent_messages(sample_dbs.messages_db, "") == []


def test_recent_messages_null_content_becomes_empty_string(tmp_path):
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(
        db_path,
        [("c@s.whatsapp.net", "C", "2026-01-01 00:00:00-08:00")],
        [("m1", "c@s.whatsapp.net", "x", None, "2026-01-01 00:00:01-08:00", 0)],
    )
    messages = db.recent_messages(db_path, "c@s.whatsapp.net")

    assert messages[0].text == ""


def test_recent_messages_empty_string_content_stays_empty_string(tmp_path):
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(
        db_path,
        [("c@s.whatsapp.net", "C", "2026-01-01 00:00:00-08:00")],
        [("m1", "c@s.whatsapp.net", "x", "", "2026-01-01 00:00:01-08:00", 0)],
    )
    messages = db.recent_messages(db_path, "c@s.whatsapp.net")

    assert messages[0].text == ""


def test_recent_messages_unicode_emoji_text(tmp_path):
    db_path = tmp_path / "messages.db"
    text = "\U0001f600" * 5 + " café"
    conftest.build_chats_db(
        db_path,
        [("c@s.whatsapp.net", "C", "2026-01-01 00:00:00-08:00")],
        [("m1", "c@s.whatsapp.net", "x", text, "2026-01-01 00:00:01-08:00", 0)],
    )
    messages = db.recent_messages(db_path, "c@s.whatsapp.net")

    assert messages[0].text == text


def test_recent_messages_very_long_text_not_truncated(tmp_path):
    db_path = tmp_path / "messages.db"
    text = "z" * 50_000
    conftest.build_chats_db(
        db_path,
        [("c@s.whatsapp.net", "C", "2026-01-01 00:00:00-08:00")],
        [("m1", "c@s.whatsapp.net", "x", text, "2026-01-01 00:00:01-08:00", 0)],
    )
    messages = db.recent_messages(db_path, "c@s.whatsapp.net")

    assert len(messages[0].text) == 50_000


def test_recent_messages_embedded_newlines_preserved(tmp_path):
    db_path = tmp_path / "messages.db"
    text = "line1\nline2\nline3"
    conftest.build_chats_db(
        db_path,
        [("c@s.whatsapp.net", "C", "2026-01-01 00:00:00-08:00")],
        [("m1", "c@s.whatsapp.net", "x", text, "2026-01-01 00:00:01-08:00", 1)],
    )
    messages = db.recent_messages(db_path, "c@s.whatsapp.net")

    assert messages[0].text == text


def test_recent_messages_many_messages_oldest_first_at_scale(tmp_path):
    db_path = tmp_path / "messages.db"
    msg_rows = [
        (f"m{i}", "big@s.whatsapp.net", "x", f"text{i}", f"2026-01-01 00:{i:02d}:00-08:00", i % 2)
        for i in range(60)
    ]
    conftest.build_chats_db(
        db_path, [("big@s.whatsapp.net", "Big", "2026-01-01 00:00:00-08:00")], msg_rows
    )
    messages = db.recent_messages(db_path, "big@s.whatsapp.net", limit=1000)

    assert [m.text for m in messages] == [f"text{i}" for i in range(60)]


def test_recent_messages_many_messages_limit_keeps_most_recent(tmp_path):
    db_path = tmp_path / "messages.db"
    msg_rows = [
        (f"m{i}", "big@s.whatsapp.net", "x", f"text{i}", f"2026-01-01 00:{i:02d}:00-08:00", i % 2)
        for i in range(60)
    ]
    conftest.build_chats_db(
        db_path, [("big@s.whatsapp.net", "Big", "2026-01-01 00:00:00-08:00")], msg_rows
    )
    messages = db.recent_messages(db_path, "big@s.whatsapp.net", limit=5)

    assert [m.text for m in messages] == [f"text{i}" for i in range(55, 60)]


def test_recent_messages_does_not_leak_from_jid_containing_query_as_substring(tmp_path):
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(
        db_path,
        [
            ("c1@s.whatsapp.net", "C1", "2026-01-01 00:00:00-08:00"),
            ("1c1@s.whatsapp.net", "C1sub", "2026-01-01 00:00:00-08:00"),
        ],
        [
            ("m1", "c1@s.whatsapp.net", "x", "mine", "2026-01-01 00:00:01-08:00", 0),
            ("m2", "1c1@s.whatsapp.net", "x", "should not leak", "2026-01-01 00:00:02-08:00", 0),
        ],
    )
    messages = db.recent_messages(db_path, "c1@s.whatsapp.net")

    assert [m.text for m in messages] == ["mine"]


def test_recent_messages_does_not_leak_from_jid_that_is_a_substring_of_query(tmp_path):
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(
        db_path,
        [("c1@s.whatsapp.net", "C1", "2026-01-01 00:00:00-08:00")],
        [("m1", "c1@s.whatsapp.net", "x", "mine", "2026-01-01 00:00:01-08:00", 0)],
    )
    messages = db.recent_messages(db_path, "c1@s.whatsapp.net.evil")

    assert messages == []


def test_recent_messages_missing_db_file_raises_operational_error(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        db.recent_messages(tmp_path / "nope.db", "x@s.whatsapp.net")


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(limit=st.integers(max_value=-1))
def test_recent_messages_property_any_negative_limit_returns_empty(sample_dbs, limit):
    assert (
        db.recent_messages(sample_dbs.messages_db, "14157863858@s.whatsapp.net", limit=limit) == []
    )


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(text=st.text(max_size=500).filter(lambda s: "\x00" not in s))
def test_recent_messages_property_text_roundtrips_unless_none(tmp_path, text):
    db_path = tmp_path / f"{uuid.uuid4().hex}.db"
    conftest.build_chats_db(
        db_path,
        [("c@s.whatsapp.net", "C", "2026-01-01 00:00:00-08:00")],
        [("m1", "c@s.whatsapp.net", "x", text, "2026-01-01 00:00:01-08:00", 0)],
    )
    messages = db.recent_messages(db_path, "c@s.whatsapp.net")

    assert messages[0].text == text


# --- extra edge cases (scale, JID-match semantics, timestamp failure mode) ---


def test_search_contacts_jid_match_is_case_insensitive(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    conftest.build_contacts_db(
        db_path, [("our@x", "MixedCase@S.WhatsApp.Net", "", "", "", "", "")]
    )
    results = db.search_contacts(db_path, "mixedcase@s.whatsapp.net")

    assert [c.their_jid for c in results] == ["MixedCase@S.WhatsApp.Net"]


def test_search_contacts_query_matching_jid_domain_matches_all_that_domain(sample_dbs):
    results = db.search_contacts(sample_dbs.whatsapp_db, "s.whatsapp.net")

    assert {c.their_jid for c in results} == {
        "14157863858@s.whatsapp.net",
        "19998887777@s.whatsapp.net",
    }


def test_search_contacts_many_contacts_matches_correct_subset(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    rows = [
        ("our@x", f"{i:03d}0000000000@s.whatsapp.net", f"Person{i}", "", "", "", "")
        for i in range(80)
    ]
    rows.append(("our@x", "target@s.whatsapp.net", "UniqueTargetName", "", "", "", ""))
    conftest.build_contacts_db(db_path, rows)

    results = db.search_contacts(db_path, "UniqueTargetName")

    assert [c.their_jid for c in results] == ["target@s.whatsapp.net"]


def test_search_contacts_many_contacts_empty_query_returns_all(tmp_path):
    db_path = tmp_path / "whatsapp.db"
    rows = [
        ("our@x", f"{i:03d}0000000000@s.whatsapp.net", f"Person{i}", "", "", "", "")
        for i in range(80)
    ]
    conftest.build_contacts_db(db_path, rows)

    results = db.search_contacts(db_path, "")

    assert len(results) == 80


def test_list_chats_limit_equal_to_row_count_returns_all(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db, limit=4)

    assert len(chats) == 4


def test_list_chats_limit_one_returns_single_most_recent_chat(sample_dbs):
    chats = db.list_chats(sample_dbs.messages_db, limit=1)

    assert len(chats) == 1
    assert chats[0].jid == "100455141081206@lid"


def test_list_chats_unparseable_timestamp_raises_value_error(tmp_path):
    # Not every conceivable timestamp shape is accepted — this is where
    # the real production risk actually lives: if the bridge ever writes
    # something datetime.fromisoformat rejects, list_chats raises rather
    # than degrading gracefully.
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(db_path, [("bad@s.whatsapp.net", "Bad", "not-a-date")])

    with pytest.raises(ValueError):
        db.list_chats(db_path)


def test_resolve_chat_jid_strips_tabs_and_newlines(sample_dbs):
    assert (
        db.resolve_chat_jid(sample_dbs.messages_db, "1\t415\n786\r3858")
        == "14157863858@s.whatsapp.net"
    )


def test_resolve_chat_jid_extra_trailing_digit_does_not_match(sample_dbs):
    assert db.resolve_chat_jid(sample_dbs.messages_db, "141578638580") is None


def test_resolve_chat_jid_matches_as_substring_without_country_code(sample_dbs):
    # The LIKE match is substring, not anchored — a 10-digit number
    # missing the leading country-code digit still matches because it's a
    # substring of the full stored JID.
    assert (
        db.resolve_chat_jid(sample_dbs.messages_db, "4157863858") == "14157863858@s.whatsapp.net"
    )


def test_recent_messages_is_from_me_is_python_bool_not_int(sample_dbs):
    messages = db.recent_messages(sample_dbs.messages_db, "14157863858@s.whatsapp.net")

    assert isinstance(messages[0].is_from_me, bool)


def test_recent_messages_unparseable_timestamp_raises_value_error(tmp_path):
    db_path = tmp_path / "messages.db"
    conftest.build_chats_db(
        db_path,
        [("c@s.whatsapp.net", "C", "2026-01-01 00:00:00-08:00")],
        [("m1", "c@s.whatsapp.net", "x", "hi", "not-a-date", 0)],
    )

    with pytest.raises(ValueError):
        db.recent_messages(db_path, "c@s.whatsapp.net")
