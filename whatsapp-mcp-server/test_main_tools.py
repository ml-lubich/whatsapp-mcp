"""Tests for the plain @mcp.tool() wrapper functions in main.py that just
pass through to whatsapp.py, plus the _resolve_chat_jid branch (phone number,
no "@") that test_send_guard.py doesn't exercise since its RECIPIENT is
already a JID.

No network/DB access: every whatsapp_* function main.py imports is
monkeypatched on the `main` module, matching the style in test_send_guard.py.
"""

from __future__ import annotations

import main


def test_search_contacts_passes_through(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_search_contacts", lambda query: [{"jid": "x"}])
    assert main.search_contacts("alice") == [{"jid": "x"}]


def test_list_messages_passes_through(monkeypatch):
    captured = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return ["msg"]

    monkeypatch.setattr(main, "whatsapp_list_messages", _fake)
    result = main.list_messages(chat_jid="a@g.us", limit=5)
    assert result == ["msg"]
    assert captured["chat_jid"] == "a@g.us"
    assert captured["limit"] == 5


def test_list_chats_passes_through(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_list_chats", lambda **kw: ["chat"])
    assert main.list_chats(query="q") == ["chat"]


def test_get_chat_passes_through(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_get_chat", lambda chat_jid, include_last_message: {"jid": chat_jid})
    assert main.get_chat("a@g.us") == {"jid": "a@g.us"}


def test_get_direct_chat_by_contact_passes_through(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_get_direct_chat_by_contact", lambda number: {"jid": number})
    assert main.get_direct_chat_by_contact("555") == {"jid": "555"}


def test_get_contact_chats_passes_through(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_get_contact_chats", lambda jid, limit, page: [jid])
    assert main.get_contact_chats("a@g.us", limit=5, page=1) == ["a@g.us"]


def test_get_last_interaction_passes_through(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_get_last_interaction", lambda jid: f"last for {jid}")
    assert main.get_last_interaction("a@g.us") == "last for a@g.us"


def test_get_message_context_passes_through(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_get_message_context", lambda message_id, before, after: {"id": message_id})
    assert main.get_message_context("m1") == {"id": "m1"}


def test_send_file_success_shapes_result(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_send_file", lambda recipient, media_path: (True, "sent"))
    assert main.send_file("555", "/tmp/x.jpg") == {"success": True, "message": "sent"}


def test_send_file_failure_shapes_result(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_send_file", lambda recipient, media_path: (False, "no file"))
    assert main.send_file("555", "/tmp/x.jpg") == {"success": False, "message": "no file"}


def test_send_audio_message_success_shapes_result(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_audio_voice_message", lambda recipient, media_path: (True, "sent"))
    assert main.send_audio_message("555", "/tmp/x.ogg") == {"success": True, "message": "sent"}


def test_send_audio_message_failure_shapes_result(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_audio_voice_message", lambda recipient, media_path: (False, "ffmpeg missing"))
    assert main.send_audio_message("555", "/tmp/x.m4a") == {"success": False, "message": "ffmpeg missing"}


def test_download_media_success_shapes_result(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_download_media", lambda message_id, chat_jid: "/tmp/out.jpg")
    result = main.download_media("m1", "a@g.us")
    assert result == {
        "success": True,
        "message": "Media downloaded successfully",
        "file_path": "/tmp/out.jpg",
    }


def test_download_media_failure_shapes_result(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_download_media", lambda message_id, chat_jid: None)
    result = main.download_media("m1", "a@g.us")
    assert result == {"success": False, "message": "Failed to download media"}


# --- _resolve_chat_jid: phone-number recipient (no "@") -----------------------

def test_resolve_chat_jid_looks_up_contact_when_no_at_sign(monkeypatch):
    class _Chat:
        jid = "555@s.whatsapp.net"

    monkeypatch.setattr(main, "whatsapp_get_direct_chat_by_contact", lambda number: _Chat())
    assert main._resolve_chat_jid("555") == "555@s.whatsapp.net"


def test_resolve_chat_jid_unknown_phone_number_returns_none(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_get_direct_chat_by_contact", lambda number: None)
    assert main._resolve_chat_jid("555") is None


def test_resolve_chat_jid_jid_recipient_skips_lookup(monkeypatch):
    def _should_not_be_called(number):
        raise AssertionError("lookup must be skipped when recipient already has @")

    monkeypatch.setattr(main, "whatsapp_get_direct_chat_by_contact", _should_not_be_called)
    assert main._resolve_chat_jid("555@s.whatsapp.net") == "555@s.whatsapp.net"


# --- run() entry point ---------------------------------------------------------

def test_run_starts_stdio_transport(monkeypatch):
    calls = []
    monkeypatch.setattr(main.mcp, "run", lambda transport=None: calls.append(transport))
    main.run()
    assert calls == ["stdio"]
