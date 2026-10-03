"""download_media MCP path uses the shared wa_cli.media resolver."""
from __future__ import annotations

import sqlite3
import sys

import pytest

import main
import whatsapp

GROUP = "111@g.us"


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    db = tmp_path / "messages.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE messages (id TEXT, chat_jid TEXT, timestamp TEXT, media_type TEXT, filename TEXT,"
        " PRIMARY KEY (id, chat_jid));"
        "INSERT INTO messages VALUES ('M1','111@g.us','2026-10-03','image','a.jpg');"
    )
    conn.commit()
    conn.close()
    root = tmp_path / "desktop"
    (root / "Message" / "Media").mkdir(parents=True)
    ds = sqlite3.connect(root / "ChatStorage.sqlite")
    ds.executescript(
        "CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID VARCHAR);"
        "CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMEDIALOCALPATH VARCHAR);"
        "CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZSTANZAID VARCHAR, ZMEDIAITEM INTEGER, ZCHATSESSION INTEGER);"
        "INSERT INTO ZWACHATSESSION VALUES (1,'111@g.us');"
        "INSERT INTO ZWAMEDIAITEM VALUES (1,'Media/u.jpg');"
        "INSERT INTO ZWAMESSAGE VALUES (1,'M1',1,1);"
    )
    ds.commit()
    ds.close()
    (root / "Message" / "Media" / "u.jpg").write_bytes(b"img")
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(db))
    monkeypatch.setattr(whatsapp, "WHATSAPP_API_BASE_URL", "http://127.0.0.1:1/api")  # nothing listens
    monkeypatch.setenv("WA_DESKTOP_ROOT", str(root))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


def test_resolve_media_falls_back_to_desktop(media_env):
    path, detail = whatsapp.resolve_media("M1", GROUP)
    assert path and path.endswith(".jpg") and open(path, "rb").read() == b"img"
    assert str(media_env / "state") in path


def test_resolve_media_reports_cause(media_env):
    path, detail = whatsapp.resolve_media("NOPE", GROUP)
    assert path is None and "not found" in detail


def test_resolve_media_without_wa_cli_uses_bridge_only(monkeypatch):
    monkeypatch.setattr(whatsapp, "_load_media", lambda: None)
    monkeypatch.setattr(whatsapp, "download_media", lambda m, c: "/tmp/x.jpg")
    assert whatsapp.resolve_media("M1", GROUP) == ("/tmp/x.jpg", "")
    monkeypatch.setattr(whatsapp, "download_media", lambda m, c: None)
    assert whatsapp.resolve_media("M1", GROUP) == (None, "Failed to download media")


def test_load_media_finds_sibling_package(monkeypatch):
    for mod in [m for m in sys.modules if m == "wa_cli" or m.startswith("wa_cli.")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "wa-mcp" not in p])
    assert whatsapp._load_media() is not None


def test_load_media_missing_returns_none(monkeypatch, tmp_path):
    for mod in [m for m in sys.modules if m == "wa_cli" or m.startswith("wa_cli.")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "wa-mcp" not in p])
    monkeypatch.setattr(whatsapp, "SIBLING_CLI_DIR", str(tmp_path / "nope"))
    assert whatsapp._load_media() is None


def test_tool_returns_path_and_detail(monkeypatch):
    monkeypatch.setattr(main, "whatsapp_resolve_media", lambda m, c: ("/tmp/o.jpg", ""))
    assert main.download_media("m1", "a@g.us") == {
        "success": True, "message": "Media saved", "file_path": "/tmp/o.jpg"}
    monkeypatch.setattr(main, "whatsapp_resolve_media", lambda m, c: (None, "bridge: 403 | Desktop: absent"))
    assert main.download_media("m1", "a@g.us") == {
        "success": False, "message": "bridge: 403 | Desktop: absent"}


def test_import_does_no_work(monkeypatch):
    """Lazy start: importing the server module must not touch bridge, DB or wa_cli."""
    import importlib

    called = []
    monkeypatch.setattr(whatsapp.requests, "post", lambda *a, **k: called.append(1))
    monkeypatch.setattr(whatsapp.sqlite3, "connect", lambda *a, **k: called.append(2))
    importlib.reload(main)
    assert called == []
