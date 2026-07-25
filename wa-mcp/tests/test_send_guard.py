"""Tests for the send-time anti-repeat guard: `wa send --force` and the
printed recent-thread context.

Mirrors test_main.py's isolation contract: no network, no real store DBs —
every test monkeypatches db.resolve_chat_jid / db.recent_messages / api.send_message.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from typer.testing import CliRunner

from wa_cli import api, db, main

runner = CliRunner()

RECIPIENT = "14157863858@s.whatsapp.net"


def _msg(text: str, is_from_me: bool, minutes_ago: int = 1) -> db.Message:
    return db.Message(
        is_from_me=is_from_me,
        timestamp=datetime.now() - timedelta(minutes=minutes_ago),
        text=text,
    )


def _patch_db_path(monkeypatch, tmp_path):
    # An existing (but otherwise unused) file — just needs .exists() to be True
    # so the guard proceeds to call db.resolve_chat_jid / db.recent_messages,
    # which are monkeypatched separately in each test.
    fake_db = tmp_path / "messages.db"
    fake_db.write_text("")
    monkeypatch.setattr(main.config, "messages_db", lambda: fake_db)


def _patch_recent(monkeypatch, tmp_path, messages):
    _patch_db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(db, "resolve_chat_jid", lambda db_path, recipient: RECIPIENT)
    monkeypatch.setattr(db, "recent_messages", lambda db_path, chat_jid, limit=10: messages)


def _patch_send(monkeypatch, ok: bool = True, detail: str = "sent ok"):
    calls = []

    def fake_send(recipient, message, *, base_url):
        calls.append((recipient, message))
        return ok, detail

    monkeypatch.setattr(api, "send_message", fake_send)
    return calls


# --- duplicate guard ---


def test_send_blocks_exact_duplicate_without_force(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday?", is_from_me=True)])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?"])

    assert result.exit_code != 0
    assert "already sent" in result.output.lower()
    assert calls == []


def test_send_near_duplicate_blocked_without_force(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday night?", is_from_me=True)])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday nite?"])

    assert result.exit_code != 0
    assert calls == []


def test_send_force_overrides_duplicate_and_sends(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday?", is_from_me=True)])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?", "--force"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "Hey are we still on for Friday?")]


def test_send_new_message_sends_normally(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday?", is_from_me=True)])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Completely different message about Saturday"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "Completely different message about Saturday")]


def test_send_only_blocks_on_our_own_outbound_messages(monkeypatch, tmp_path):
    # Identical text from THEM (not us) must not trigger the duplicate guard.
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday?", is_from_me=False)])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "Hey are we still on for Friday?")]


# --- recent-thread context printed ---


def test_send_prints_recent_thread_on_success(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("earlier message", is_from_me=False)])
    _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "brand new text"])

    assert result.exit_code == 0
    assert "earlier message" in result.output


def test_send_prints_recent_thread_on_block(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday?", is_from_me=True)])
    _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?"])

    assert result.exit_code != 0
    assert "Hey are we still on for Friday?" in result.output


# --- resilience: context fetch failures never block a real send ---


def test_send_db_read_failure_still_sends(monkeypatch, tmp_path):
    _patch_db_path(monkeypatch, tmp_path)

    def _raise(db_path, recipient):
        raise RuntimeError("db read error")

    monkeypatch.setattr(db, "resolve_chat_jid", _raise)
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "some message text"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "some message text")]


def test_send_missing_db_still_sends(monkeypatch, tmp_path):
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "some message text"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "some message text")]
