"""Tests for the outbound actions in whatsapp.py: send_message, send_file,
send_audio_message, download_media.

Boundary mocked: requests.post (the HTTP call to the local bridge at
localhost:8080). No real WhatsApp message is ever sent — the bridge call
itself is what's replaced. File-existence checks run for real against
tmp_path.
"""

from __future__ import annotations

import json

import pytest
import requests

import whatsapp

RECIPIENT = "15551234567@s.whatsapp.net"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", json_error=False):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise json.JSONDecodeError("bad json", "doc", 0)
        return self._payload


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "clip.ogg"
    path.write_bytes(b"fake ogg bytes")
    return str(path)


@pytest.fixture
def raw_audio_file(tmp_path):
    path = tmp_path / "clip.m4a"
    path.write_bytes(b"fake m4a bytes")
    return str(path)


# --- send_message --------------------------------------------------------------

def test_send_message_missing_recipient():
    assert whatsapp.send_message("", "hi") == (False, "Recipient must be provided")


def test_send_message_success(monkeypatch):
    monkeypatch.setattr(
        requests, "post",
        lambda url, json: _FakeResponse(200, {"success": True, "message": "sent"}),
    )
    assert whatsapp.send_message(RECIPIENT, "hi") == (True, "sent")


def test_send_message_non_200_returns_error(monkeypatch):
    monkeypatch.setattr(
        requests, "post",
        lambda url, json: _FakeResponse(500, text="boom"),
    )
    success, message = whatsapp.send_message(RECIPIENT, "hi")
    assert success is False
    assert "HTTP 500" in message


def test_send_message_request_exception_caught(monkeypatch):
    def _raise(url, json):
        raise requests.RequestException("bridge unreachable")

    monkeypatch.setattr(requests, "post", _raise)
    success, message = whatsapp.send_message(RECIPIENT, "hi")
    assert success is False
    assert "Request error" in message


def test_send_message_json_decode_error_caught(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda url, json: _FakeResponse(200, json_error=True))
    success, message = whatsapp.send_message(RECIPIENT, "hi")
    assert success is False
    assert "Error parsing response" in message


def test_send_message_unexpected_error_caught(monkeypatch):
    def _raise(url, json):
        raise ValueError("something else broke")

    monkeypatch.setattr(requests, "post", _raise)
    success, message = whatsapp.send_message(RECIPIENT, "hi")
    assert success is False
    assert "Unexpected error" in message


# --- send_file ------------------------------------------------------------------

def test_send_file_missing_recipient():
    assert whatsapp.send_file("", "/tmp/x")[0] is False


def test_send_file_missing_media_path():
    success, message = whatsapp.send_file(RECIPIENT, "")
    assert success is False
    assert "Media path" in message


def test_send_file_not_found(tmp_path):
    missing = str(tmp_path / "nope.jpg")
    success, message = whatsapp.send_file(RECIPIENT, missing)
    assert success is False
    assert "not found" in message


def test_send_file_success(monkeypatch, tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"fake image bytes")
    monkeypatch.setattr(
        requests, "post",
        lambda url, json: _FakeResponse(200, {"success": True, "message": "sent"}),
    )
    assert whatsapp.send_file(RECIPIENT, str(path)) == (True, "sent")


def test_send_file_request_exception_caught(monkeypatch, tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"fake image bytes")

    def _raise(url, json):
        raise requests.RequestException("bridge unreachable")

    monkeypatch.setattr(requests, "post", _raise)
    success, message = whatsapp.send_file(RECIPIENT, str(path))
    assert success is False
    assert "Request error" in message


# --- send_audio_message ----------------------------------------------------------

def test_send_audio_message_missing_recipient():
    assert whatsapp.send_audio_message("", "/tmp/x")[0] is False


def test_send_audio_message_missing_media_path():
    success, message = whatsapp.send_audio_message(RECIPIENT, "")
    assert success is False
    assert "Media path" in message


def test_send_audio_message_not_found(tmp_path):
    missing = str(tmp_path / "nope.ogg")
    success, message = whatsapp.send_audio_message(RECIPIENT, missing)
    assert success is False
    assert "not found" in message


def test_send_audio_message_already_ogg_skips_conversion(monkeypatch, audio_file):
    converted = []
    monkeypatch.setattr(
        whatsapp.audio, "convert_to_opus_ogg_temp",
        lambda path: converted.append(path) or "/should/not/be/called.ogg",
    )
    monkeypatch.setattr(
        requests, "post",
        lambda url, json: _FakeResponse(200, {"success": True, "message": "sent"}),
    )
    assert whatsapp.send_audio_message(RECIPIENT, audio_file) == (True, "sent")
    assert converted == []


def test_send_audio_message_converts_non_ogg_then_sends(monkeypatch, raw_audio_file):
    monkeypatch.setattr(
        whatsapp.audio, "convert_to_opus_ogg_temp",
        lambda path: "/tmp/converted.ogg",
    )
    posts = []
    monkeypatch.setattr(
        requests, "post",
        lambda url, json: (posts.append(json), _FakeResponse(200, {"success": True, "message": "sent"}))[1],
    )
    result = whatsapp.send_audio_message(RECIPIENT, raw_audio_file)
    assert result == (True, "sent")
    assert posts[0]["media_path"] == "/tmp/converted.ogg"


def test_send_audio_message_conversion_failure_reports_ffmpeg_hint(monkeypatch, raw_audio_file):
    def _raise(path):
        raise RuntimeError("ffmpeg not found")

    monkeypatch.setattr(whatsapp.audio, "convert_to_opus_ogg_temp", _raise)
    success, message = whatsapp.send_audio_message(RECIPIENT, raw_audio_file)
    assert success is False
    assert "ffmpeg" in message.lower()


def test_send_audio_message_non_200_returns_error(monkeypatch, audio_file):
    monkeypatch.setattr(requests, "post", lambda url, json: _FakeResponse(503, text="down"))
    success, message = whatsapp.send_audio_message(RECIPIENT, audio_file)
    assert success is False
    assert "HTTP 503" in message


# --- download_media ---------------------------------------------------------------

def test_download_media_success(monkeypatch):
    monkeypatch.setattr(
        requests, "post",
        lambda url, json: _FakeResponse(200, {"success": True, "path": "/tmp/downloaded.jpg"}),
    )
    assert whatsapp.download_media("m1", "chat@g.us") == "/tmp/downloaded.jpg"


def test_download_media_bridge_reports_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        requests, "post",
        lambda url, json: _FakeResponse(200, {"success": False, "message": "no media"}),
    )
    assert whatsapp.download_media("m1", "chat@g.us") is None


def test_download_media_non_200_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda url, json: _FakeResponse(404, text="not found"))
    assert whatsapp.download_media("m1", "chat@g.us") is None


def test_download_media_request_exception_returns_none(monkeypatch):
    def _raise(url, json):
        raise requests.RequestException("bridge unreachable")

    monkeypatch.setattr(requests, "post", _raise)
    assert whatsapp.download_media("m1", "chat@g.us") is None


def test_download_media_json_decode_error_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda url, json: _FakeResponse(200, json_error=True))
    assert whatsapp.download_media("m1", "chat@g.us") is None


def test_download_media_unexpected_error_returns_none(monkeypatch):
    def _raise(url, json):
        raise ValueError("boom")

    monkeypatch.setattr(requests, "post", _raise)
    assert whatsapp.download_media("m1", "chat@g.us") is None
