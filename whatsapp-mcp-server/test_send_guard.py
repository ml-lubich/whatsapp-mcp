"""Tests for the send_message guards on the MCP tool:
  1. review gate  — refuse to post into a live thread the caller hasn't acknowledged
  2. duplicate guard — refuse near-identical outbound repeats

No network calls: the recent-messages fetch and the raw whatsapp.send_message
are both monkeypatched on the `main` module.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import main
from whatsapp import Message


def _msg(text: str, is_from_me: bool, minutes_ago: int = 1) -> Message:
    return Message(
        timestamp=datetime.now() - timedelta(minutes=minutes_ago),
        sender="15551234567@s.whatsapp.net",
        content=text,
        is_from_me=is_from_me,
        chat_jid="15551234567@s.whatsapp.net",
        id=f"id-{minutes_ago}",
    )


def _patch_recent(monkeypatch, messages):
    monkeypatch.setattr(main, "whatsapp_get_recent_messages", lambda chat_jid, limit=10: messages)


def _patch_send(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main,
        "whatsapp_send_message",
        lambda recipient, message: (calls.append((recipient, message)), (True, "sent"))[1],
    )
    return calls


RECIPIENT = "15551234567@s.whatsapp.net"
# The newest recent message drives thread_head; helper ids are f"id-{minutes_ago}",
# so a single message 5 minutes ago has head "id-5".
HEAD_5 = "id-5"


# --- review gate -----------------------------------------------------------

def test_send_blocked_for_review_without_acknowledge(monkeypatch):
    recent = [_msg("some earlier message", is_from_me=False, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "a brand new message")

    assert result["success"] is False
    assert "review" in result["message"].lower()
    assert result["thread_head"] == HEAD_5
    assert send_calls == []


def test_send_succeeds_with_correct_acknowledge(monkeypatch):
    recent = [_msg("some earlier message", is_from_me=False, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "a brand new message", acknowledge=HEAD_5)

    assert result["success"] is True
    assert send_calls == [(RECIPIENT, "a brand new message")]


def test_stale_acknowledge_blocked(monkeypatch):
    # Acknowledging an older head than the current tail means new messages arrived.
    recent = [_msg("newest message", is_from_me=False, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "reply text", acknowledge="id-99")

    assert result["success"] is False
    assert "review" in result["message"].lower()
    assert send_calls == []


def test_empty_thread_sends_without_acknowledge(monkeypatch):
    # First contact: nothing to review, so no acknowledgement is required.
    _patch_recent(monkeypatch, [])
    send_calls = _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "first hello")

    assert result["success"] is True
    assert result["thread_head"] is None
    assert send_calls == [(RECIPIENT, "first hello")]


def test_force_bypasses_review_gate(monkeypatch):
    recent = [_msg("some earlier message", is_from_me=False, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "urgent new message", force=True)

    assert result["success"] is True
    assert send_calls == [(RECIPIENT, "urgent new message")]


def test_thread_head_uses_newest_message(monkeypatch):
    # Head must be the most recent message regardless of list order.
    recent = [
        _msg("older", is_from_me=False, minutes_ago=30),
        _msg("newest", is_from_me=True, minutes_ago=2),
        _msg("middle", is_from_me=False, minutes_ago=10),
    ]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    blocked = main.send_message(RECIPIENT, "hi")
    assert blocked["thread_head"] == "id-2"
    assert send_calls == []

    ok = main.send_message(RECIPIENT, "hi", acknowledge="id-2")
    assert ok["success"] is True


# --- duplicate guard (reached only after the review gate passes) -----------

def test_exact_duplicate_outbound_blocked_with_acknowledge(monkeypatch):
    recent = [_msg("Hey are we still on for Friday?", is_from_me=True, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "Hey are we still on for Friday?", acknowledge=HEAD_5)

    assert result["success"] is False
    assert "already sent" in result["message"].lower()
    assert send_calls == []


def test_force_true_overrides_duplicate_and_sends(monkeypatch):
    recent = [_msg("Hey are we still on for Friday?", is_from_me=True, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "Hey are we still on for Friday?", force=True)

    assert result["success"] is True
    assert send_calls == [(RECIPIENT, "Hey are we still on for Friday?")]


def test_new_message_sends_normally(monkeypatch):
    recent = [_msg("Hey are we still on for Friday?", is_from_me=True, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    result = main.send_message(
        RECIPIENT, "Completely different message about Saturday plans", acknowledge=HEAD_5
    )

    assert result["success"] is True
    assert send_calls == [(RECIPIENT, "Completely different message about Saturday plans")]


def test_near_duplicate_blocked_with_acknowledge(monkeypatch):
    recent = [_msg("Hey are we still on for Friday night?", is_from_me=True, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    # Near-identical text (one word changed) should still be caught by the
    # difflib ratio check even though it's not an exact normalized match.
    result = main.send_message(RECIPIENT, "Hey are we still on for Friday nite?", acknowledge=HEAD_5)

    assert result["success"] is False
    assert send_calls == []


# --- recent_context surfaced everywhere ------------------------------------

def test_recent_context_present_on_review_block(monkeypatch):
    recent = [_msg("something to read", is_from_me=False, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "new text")

    assert "recent_context" in result
    assert len(result["recent_context"]) == 1
    assert result["recent_context"][0]["text"] == "something to read"


def test_recent_context_present_on_send(monkeypatch):
    recent = [_msg("unrelated earlier message", is_from_me=False, minutes_ago=10)]
    _patch_recent(monkeypatch, recent)
    _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "brand new text", acknowledge="id-10")

    assert "recent_context" in result
    assert len(result["recent_context"]) == 1


def test_context_fetch_raising_exception_still_sends(monkeypatch):
    def _raise(chat_jid, limit=10):
        raise RuntimeError("db read error")

    monkeypatch.setattr(main, "whatsapp_get_recent_messages", _raise)
    send_calls = _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "some message text")

    assert result["success"] is True
    assert send_calls == [(RECIPIENT, "some message text")]
    assert result["recent_context"] == []
    assert result["thread_head"] is None
