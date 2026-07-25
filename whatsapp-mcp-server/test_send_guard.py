"""Tests for the anti-repeat guard on the MCP send_message tool.

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


def test_exact_duplicate_outbound_blocked_without_force(monkeypatch):
    recent = [_msg("Hey are we still on for Friday?", is_from_me=True, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "Hey are we still on for Friday?")

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

    result = main.send_message(RECIPIENT, "Completely different message about Saturday plans")

    assert result["success"] is True
    assert send_calls == [(RECIPIENT, "Completely different message about Saturday plans")]


def test_recent_context_present_on_block(monkeypatch):
    recent = [_msg("Hey are we still on for Friday?", is_from_me=True, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "Hey are we still on for Friday?")

    assert "recent_context" in result
    assert len(result["recent_context"]) == 1
    assert result["recent_context"][0]["from_me"] is True
    assert result["recent_context"][0]["text"] == "Hey are we still on for Friday?"


def test_recent_context_present_on_send(monkeypatch):
    recent = [_msg("unrelated earlier message", is_from_me=False, minutes_ago=10)]
    _patch_recent(monkeypatch, recent)
    _patch_send(monkeypatch)

    result = main.send_message(RECIPIENT, "brand new text")

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


def test_near_duplicate_blocked_without_force(monkeypatch):
    recent = [_msg("Hey are we still on for Friday night?", is_from_me=True, minutes_ago=5)]
    _patch_recent(monkeypatch, recent)
    send_calls = _patch_send(monkeypatch)

    # Near-identical text (one word changed) should still be caught by the
    # difflib ratio check even though it's not an exact normalized match.
    result = main.send_message(RECIPIENT, "Hey are we still on for Friday nite?")

    assert result["success"] is False
    assert send_calls == []
