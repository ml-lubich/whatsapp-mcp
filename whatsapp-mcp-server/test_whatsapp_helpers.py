"""Tests for whatsapp.py helpers not covered by test_reads.py:
formatting, sender-name lookup, get_recent_messages, the Chat.is_group
property, list_messages date filters, and the "not found" (None) branches
of the single-row lookups.

Same real-sqlite fixture approach as test_reads.py (via conftest.py).
"""

from __future__ import annotations

import sqlite3

import pytest

import whatsapp
from conftest import DIRECT_JID, GROUP_JID
from whatsapp import Chat, Message


# --- Chat.is_group -----------------------------------------------------------

def test_is_group_true_for_group_jid():
    assert Chat(jid=GROUP_JID, name="g", last_message_time=None).is_group is True


def test_is_group_false_for_direct_jid():
    assert Chat(jid=DIRECT_JID, name="d", last_message_time=None).is_group is False


# --- get_sender_name ----------------------------------------------------------

def test_get_sender_name_exact_jid_match(seeded_db):
    assert whatsapp.get_sender_name(GROUP_JID) == "Test Group :)"


def test_get_sender_name_falls_back_to_like_on_phone_part(seeded_db):
    # "222" isn't a jid itself, but exists inside DIRECT_JID ("222@s.whatsapp.net").
    assert whatsapp.get_sender_name("222") == "Alice Example"


def test_get_sender_name_no_at_sign_uses_whole_value_as_phone_part(seeded_db):
    assert whatsapp.get_sender_name("222") == "Alice Example"


def test_get_sender_name_unknown_returns_input_unchanged(seeded_db):
    assert whatsapp.get_sender_name("999@s.whatsapp.net") == "999@s.whatsapp.net"


def test_get_sender_name_db_error_returns_input_not_raise(missing_db):
    # Root-cause note: unlike the read functions in test_reads.py, get_sender_name
    # is a formatting helper and intentionally degrades to the raw JID on a DB
    # error rather than propagating, since it's called deep inside message
    # formatting where raising would break display of otherwise-good messages.
    assert whatsapp.get_sender_name("222@s.whatsapp.net") == "222@s.whatsapp.net"


# --- format_message / format_messages_list -----------------------------------

def test_format_message_from_me_uses_me_label():
    msg = Message(
        timestamp=whatsapp.datetime(2026, 1, 1, 12, 0, 0),
        sender="222@s.whatsapp.net",
        content="hi",
        is_from_me=True,
        chat_jid=DIRECT_JID,
        id="m1",
        chat_name="Alice Example",
    )
    output = whatsapp.format_message(msg)
    assert "From: Me: hi" in output


def test_format_message_without_chat_info_omits_chat_name():
    msg = Message(
        timestamp=whatsapp.datetime(2026, 1, 1, 12, 0, 0),
        sender="222@s.whatsapp.net",
        content="hi",
        is_from_me=True,
        chat_jid=DIRECT_JID,
        id="m1",
        chat_name="Alice Example",
    )
    output = whatsapp.format_message(msg, show_chat_info=False)
    assert "Chat:" not in output
    assert "hi" in output


def test_format_message_media_type_included_in_prefix():
    msg = Message(
        timestamp=whatsapp.datetime(2026, 1, 1, 12, 0, 0),
        sender="222@s.whatsapp.net",
        content="a photo",
        is_from_me=False,
        chat_jid=DIRECT_JID,
        id="m1",
        media_type="image",
    )
    output = whatsapp.format_message(msg, show_chat_info=False)
    assert "[image - Message ID: m1" in output


def test_format_messages_list_empty_returns_placeholder():
    assert whatsapp.format_messages_list([]) == "No messages to display."


def test_format_messages_list_joins_multiple_messages():
    msgs = [
        Message(
            timestamp=whatsapp.datetime(2026, 1, 1, 12, 0, 0),
            sender="a", content="one", is_from_me=True, chat_jid=DIRECT_JID, id="m1",
        ),
        Message(
            timestamp=whatsapp.datetime(2026, 1, 1, 12, 1, 0),
            sender="a", content="two", is_from_me=True, chat_jid=DIRECT_JID, id="m2",
        ),
    ]
    output = whatsapp.format_messages_list(msgs, show_chat_info=False)
    assert "one" in output and "two" in output


# --- get_recent_messages -------------------------------------------------------

def test_get_recent_messages_returns_oldest_first(seeded_db):
    result = whatsapp.get_recent_messages(GROUP_JID, limit=10)
    assert [m.content for m in result] == ["hello group", "Thanks"]


def test_get_recent_messages_respects_limit(seeded_db):
    result = whatsapp.get_recent_messages(GROUP_JID, limit=1)
    assert [m.content for m in result] == ["Thanks"]


def test_get_recent_messages_empty_chat_returns_empty_list(seeded_db):
    assert whatsapp.get_recent_messages("nobody@g.us") == []


def test_get_recent_messages_surfaces_db_open_error(missing_db):
    with pytest.raises(sqlite3.Error):
        whatsapp.get_recent_messages(GROUP_JID)


# --- list_messages date filters + include_context -----------------------------

def test_list_messages_after_filter_excludes_earlier_messages(seeded_db):
    output = whatsapp.list_messages(
        after="2026-09-24 15:22:00", limit=5, include_context=False
    )
    assert "Thanks" in output
    assert "hello group" not in output


def test_list_messages_before_filter_excludes_later_messages(seeded_db):
    output = whatsapp.list_messages(
        before="2026-09-24 15:22:00", limit=5, include_context=False
    )
    assert "hello group" in output
    assert "Thanks" not in output


def test_list_messages_invalid_after_date_raises_value_error(seeded_db):
    with pytest.raises(ValueError, match="after"):
        whatsapp.list_messages(after="not-a-date", include_context=False)


def test_list_messages_invalid_before_date_raises_value_error(seeded_db):
    with pytest.raises(ValueError, match="before"):
        whatsapp.list_messages(before="not-a-date", include_context=False)


def test_list_messages_sender_phone_number_filter(seeded_db):
    output = whatsapp.list_messages(
        sender_phone_number="333@s.whatsapp.net", limit=5, include_context=False
    )
    assert "Thanks" in output
    assert "hi alice" not in output


def test_list_messages_chat_jid_filter(seeded_db):
    output = whatsapp.list_messages(chat_jid=DIRECT_JID, limit=5, include_context=False)
    assert "hi alice" in output
    assert "Thanks" not in output


def test_list_messages_include_context_expands_around_match(seeded_db):
    output = whatsapp.list_messages(
        query="Thanks", limit=5, include_context=True, context_before=1, context_after=0
    )
    # The matched message plus the one immediately before it in the chat.
    assert "Thanks" in output
    assert "hello group" in output


def test_list_messages_no_results_returns_placeholder(seeded_db):
    output = whatsapp.list_messages(query="nonexistent-text", include_context=False)
    assert output == "No messages to display."


# --- get_message_context: not-found -------------------------------------------

def test_get_message_context_unknown_id_raises_value_error(seeded_db):
    with pytest.raises(ValueError, match="not found"):
        whatsapp.get_message_context("no-such-id")


# --- single-row lookups: not-found (None) branches -----------------------------

def test_get_last_interaction_unknown_contact_returns_none(seeded_db):
    assert whatsapp.get_last_interaction("nobody@s.whatsapp.net") is None


def test_get_chat_unknown_jid_returns_none(seeded_db):
    assert whatsapp.get_chat("nobody@g.us") is None


def test_get_direct_chat_by_contact_unknown_number_returns_none(seeded_db):
    assert whatsapp.get_direct_chat_by_contact("00000") is None
