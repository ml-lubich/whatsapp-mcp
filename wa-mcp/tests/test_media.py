"""Tests for wa_cli.media: bridge-first, WhatsApp Desktop fallback resolver.

Only fixture sqlite DBs under tmp_path are used, never the real stores.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
from pathlib import Path

import pytest

from wa_cli import media

GROUP = "120363430434063376@g.us"
OTHER = "999@g.us"


def _messages_db(path: Path, rows: list[tuple]) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
        CREATE TABLE messages (
            id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
            is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT,
            media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER,
            PRIMARY KEY (id, chat_jid)
        );
        """
    )
    conn.execute("INSERT INTO chats VALUES (?, ?, ?)", (GROUP, "Fam Group", "2026-10-03 06:22:01-07:00"))
    conn.execute("INSERT INTO chats VALUES (?, ?, ?)", (OTHER, "Other", "2026-10-01 06:22:01-07:00"))
    conn.executemany(
        "INSERT INTO messages (id, chat_jid, timestamp, media_type, filename, content) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def _desktop(root: Path, items: list[tuple[str, str, str, bytes | None]]) -> Path:
    """items: (stanza_id, chat_jid, rel_media_path, file_bytes_or_None)."""
    (root / "Message").mkdir(parents=True)
    conn = sqlite3.connect(root / "ChatStorage.sqlite")
    conn.executescript(
        """
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID VARCHAR);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMEDIALOCALPATH VARCHAR);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZSTANZAID VARCHAR,
                                 ZMEDIAITEM INTEGER, ZCHATSESSION INTEGER);
        """
    )
    sessions: dict[str, int] = {}
    for n, (stanza, jid, rel, data) in enumerate(items, start=1):
        if jid not in sessions:
            sessions[jid] = len(sessions) + 1
            conn.execute("INSERT INTO ZWACHATSESSION VALUES (?, ?)", (sessions[jid], jid))
        conn.execute("INSERT INTO ZWAMEDIAITEM VALUES (?, ?)", (n, rel or None))
        conn.execute("INSERT INTO ZWAMESSAGE VALUES (?, ?, ?, ?)", (n, stanza, n, sessions[jid]))
        if data is not None:
            f = root / "Message" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
    conn.commit()
    conn.close()
    return root


@pytest.fixture
def env(tmp_path):
    class E:
        out = tmp_path / "out dir é"
        root = tmp_path / "desktop"
        db = _messages_db(
            tmp_path / "messages.db",
            [
                ("M1", GROUP, "2026-10-03 06:22:01-07:00", "image", "image_a.jpg", ""),
                ("M2", GROUP, "2026-10-02 06:22:01-07:00", "image", "image_a.jpg", ""),
                ("TXT", GROUP, "2026-10-01 06:22:01-07:00", "", "", "hello"),
                ("M3", OTHER, "2026-09-30 06:22:01-07:00", "video", "v.mp4", ""),
                ("DUP", GROUP, "2026-09-29 06:22:01-07:00", "image", "x.jpg", ""),
                ("DUP", OTHER, "2026-09-28 06:22:01-07:00", "image", "x.jpg", ""),
            ],
        )

        @staticmethod
        def call(message_id, chat_jid=None, bridge=None):
            return media.resolve(
                message_id,
                chat_jid,
                messages_db=E.db,
                bridge_url="http://bridge",
                desktop_root=E.root,
                out_dir=E.out,
                post=bridge or _bridge_down,
            )

    return E


def _bridge_down(url, payload, timeout):
    raise urllib.error.URLError("refused")


def _bridge_says(body):
    return lambda url, payload, timeout: body


def test_missing_message(env):
    with pytest.raises(media.MediaError, match="not found"):
        env.call("NOPE", GROUP)


def test_not_media(env):
    with pytest.raises(media.MediaError, match="not a media message"):
        env.call("TXT", GROUP)


def test_ambiguous_without_chat(env):
    with pytest.raises(media.MediaError, match="--chat"):
        env.call("DUP")


def test_infers_chat_when_unique(env):
    _desktop(env.root, [("M1", GROUP, "Media/g/a/1.jpg", b"jpegdata")])
    res = env.call("M1")
    assert res.chat_jid == GROUP and res.path.read_bytes() == b"jpegdata"


def test_bridge_success_copies_not_moves(env, tmp_path):
    src = tmp_path / "bridge store" / "M1.jpg"
    src.parent.mkdir()
    src.write_bytes(b"abc")
    res = env.call("M1", GROUP, bridge=_bridge_says({"success": True, "path": str(src)}))
    assert res.source == "bridge"
    assert src.exists() and res.path != src and res.path.read_bytes() == b"abc"
    assert res.path.parent.parent == env.out  # <out>/<chat>/<file>
    assert res.size == 3


def test_expired_media_falls_back_to_desktop(env):
    _desktop(env.root, [("M1", GROUP, "Media/g/a/uuid.jpg", b"fromdesktop")])
    res = env.call("M1", GROUP, bridge=_bridge_says({"success": False, "message": "status code 403"}))
    assert res.source == "desktop"
    assert res.path.suffix == ".jpg" and res.path.read_bytes() == b"fromdesktop"


def test_bridge_success_but_file_missing_falls_back(env, tmp_path):
    _desktop(env.root, [("M1", GROUP, "Media/g/a/u.jpg", b"d")])
    res = env.call("M1", GROUP, bridge=_bridge_says({"success": True, "path": str(tmp_path / "gone.jpg")}))
    assert res.source == "desktop"


def test_bridge_garbage_response_falls_back(env):
    _desktop(env.root, [("M1", GROUP, "Media/g/a/u.jpg", b"d")])
    res = env.call("M1", GROUP, bridge=_bridge_says(["not", "a", "dict"]))
    assert res.source == "desktop"


def test_desktop_absent_names_cause(env):
    with pytest.raises(media.MediaError) as exc:
        env.call("M1", GROUP)
    msg = str(exc.value)
    assert "bridge" in msg and "WhatsApp Desktop" in msg and "not installed" in msg


def test_desktop_has_no_such_message(env):
    _desktop(env.root, [("OTHERID", GROUP, "Media/x.jpg", b"1")])
    with pytest.raises(media.MediaError, match="no record"):
        env.call("M1", GROUP)


def test_desktop_item_without_local_path(env):
    _desktop(env.root, [("M1", GROUP, "", None)])
    with pytest.raises(media.MediaError, match="never downloaded"):
        env.call("M1", GROUP)


def test_desktop_file_deleted_on_disk(env):
    _desktop(env.root, [("M1", GROUP, "Media/g/a/u.jpg", None)])
    with pytest.raises(media.MediaError, match="missing on disk"):
        env.call("M1", GROUP)


def test_desktop_prefers_matching_chat(env):
    _desktop(
        env.root,
        [("DUP", OTHER, "Media/o/x.jpg", b"other"), ("DUP", GROUP, "Media/g/x.jpg", b"group")],
    )
    assert env.call("DUP", GROUP).path.read_bytes() == b"group"


def test_desktop_permission_denied_names_grant(env, monkeypatch):
    _desktop(env.root, [("M1", GROUP, "Media/g/a/u.jpg", b"d")])

    def boom(*a, **k):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(media.sqlite3, "connect", lambda *a, **k: boom())
    with pytest.raises(media.MediaError, match="Full Disk Access"):
        media._desktop_lookup("M1", GROUP, env.root)


def test_desktop_file_permission_denied(env, monkeypatch):
    _desktop(env.root, [("M1", GROUP, "Media/g/a/u.jpg", b"d")])
    real = media.shutil.copy2

    def deny(src, dst):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(media.shutil, "copy2", deny)
    with pytest.raises(media.MediaError, match="Full Disk Access"):
        env.call("M1", GROUP)
    monkeypatch.setattr(media.shutil, "copy2", real)


def test_same_filename_different_messages_do_not_collide(env):
    _desktop(
        env.root,
        [("M1", GROUP, "Media/g/a/1.jpg", b"one"), ("M2", GROUP, "Media/g/b/1.jpg", b"two")],
    )
    a, b = env.call("M1", GROUP), env.call("M2", GROUP)
    assert a.path != b.path
    assert (a.path.read_bytes(), b.path.read_bytes()) == (b"one", b"two")


def test_second_call_hits_cache_without_bridge(env):
    _desktop(env.root, [("M1", GROUP, "Media/g/a/u.jpg", b"d")])
    env.call("M1", GROUP)

    def must_not_call(*a, **k):
        raise AssertionError("bridge called")

    res = env.call("M1", GROUP, bridge=must_not_call)
    assert res.source == "cache"


def test_post_default_posts_json(monkeypatch):
    seen = {}

    class Resp:
        def read(self):
            return json.dumps({"success": True}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen["url"], seen["body"], seen["timeout"] = req.full_url, json.loads(req.data), timeout
        return Resp()

    monkeypatch.setattr(media.urllib.request, "urlopen", fake_urlopen)
    assert media._post_json("http://b/api/download", {"a": 1}, 5) == {"success": True}
    assert seen == {"url": "http://b/api/download", "body": {"a": 1}, "timeout": 5}


def test_latest_orders_filters_and_limits(env):
    _desktop(
        env.root,
        [("M1", GROUP, "Media/g/1.jpg", b"1"), ("M2", GROUP, "Media/g/2.jpg", b"2")],
    )
    out = media.latest(
        chat_jid=GROUP,
        limit=2,
        messages_db=env.db,
        bridge_url="http://bridge",
        desktop_root=env.root,
        out_dir=env.out,
        post=_bridge_down,
    )
    assert [r.message_id for r in out] == ["M1", "M2"]
    assert all(r.path and not r.error for r in out)


def test_latest_reports_per_item_errors(env):
    out = media.latest(
        chat_jid=GROUP, limit=1, messages_db=env.db, bridge_url="http://bridge",
        desktop_root=env.root, out_dir=env.out, post=_bridge_down,
    )
    assert out[0].path is None and "WhatsApp Desktop" in out[0].error


def test_latest_by_name(env):
    out = media.latest(
        name="fam", limit=5, messages_db=env.db, bridge_url="http://bridge",
        desktop_root=env.root, out_dir=env.out, post=_bridge_down,
    )
    assert {r.chat_jid for r in out} == {GROUP}
    assert [r.message_id for r in out][:2] == ["M1", "M2"]


def test_latest_unknown_name(env):
    with pytest.raises(media.MediaError, match="no chat matches"):
        media.latest(name="zzz", limit=1, messages_db=env.db, bridge_url="b",
                     desktop_root=env.root, out_dir=env.out)


def test_latest_without_filter_spans_chats(env):
    out = media.latest(limit=10, messages_db=env.db, bridge_url="b",
                       desktop_root=env.root, out_dir=env.out, post=_bridge_down)
    assert "M3" in [r.message_id for r in out] and "TXT" not in [r.message_id for r in out]


def test_latest_negative_limit_is_empty(env):
    assert media.latest(limit=-3, messages_db=env.db, bridge_url="b",
                        desktop_root=env.root, out_dir=env.out) == []


def test_messages_db_missing(tmp_path):
    with pytest.raises(media.MediaError, match="messages database not found"):
        media.resolve("M1", GROUP, messages_db=tmp_path / "nope.db", bridge_url="b",
                      desktop_root=tmp_path, out_dir=tmp_path / "o")


def test_filename_sanitised_for_odd_chat_jid(env, tmp_path):
    _desktop(env.root, [("M3", OTHER, "Media/o/v.mp4", b"vid")])
    res = env.call("M3", OTHER)
    assert res.path.suffix == ".mp4" and os.sep not in res.path.name


def _http_error(code, body):
    import io

    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))


def test_post_json_parses_http_500_json_body(monkeypatch):
    def raiser(req, timeout):
        raise _http_error(500, b'{"success": false, "message": "status code 403"}')

    monkeypatch.setattr(media.urllib.request, "urlopen", raiser)
    assert media._post_json("http://b/x", {}, 1)["message"] == "status code 403"


def test_post_json_http_error_with_text_body(monkeypatch):
    def raiser(req, timeout):
        raise _http_error(400, b"Invalid request format")

    monkeypatch.setattr(media.urllib.request, "urlopen", raiser)
    assert "HTTP 400" in media._post_json("http://b/x", {}, 1)["message"]
