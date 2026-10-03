"""Resolve a WhatsApp media message to a readable local file.

Order: (0) our own earlier copy, (1) the Go bridge's /api/download, (2) a
read-only lookup in WhatsApp Desktop's local store (ChatStorage.sqlite, joined
on ZSTANZAID == bridge message id). The result is always a COPY under
`out_dir/<chat>/<message-id><ext>`; no source file or database is modified.

Stdlib-only on purpose: the MCP server imports this same module.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

BRIDGE_TIMEOUT = 45.0  # the bridge waits up to 30s for a phone re-upload
FDA_HINT = (
    "macOS blocked access to WhatsApp Desktop's container: grant Full Disk Access "
    "to the app running this command (System Settings > Privacy & Security > "
    "Full Disk Access) and retry"
)

Post = Callable[[str, dict, float], object]


class MediaError(Exception):
    """A user-presentable reason a message could not be resolved."""


@dataclass
class Result:
    message_id: str
    chat_jid: str
    path: Path | None = None
    source: str = ""  # cache | bridge | desktop
    size: int = 0
    error: str | None = None


def _post_json(url: str, payload: dict, timeout: float) -> object:
    req = urllib.request.Request(
        url, json.dumps(payload).encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:  # the bridge reports failures as HTTP 500 + JSON
        try:
            return json.loads(exc.read())
        except ValueError:
            return {"success": False, "message": f"bridge returned HTTP {exc.code}"}


def _safe(name: str) -> str:
    return re.sub(r"[^\w.@-]", "_", name)


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _lookup(db: Path, message_id: str, chat_jid: str | None) -> tuple[str, str]:
    if not db.exists():
        raise MediaError(f"messages database not found at {db} - has the bridge ever run?")
    sql, args = "SELECT chat_jid, media_type FROM messages WHERE id = ?", [message_id]
    if chat_jid:
        sql, args = sql + " AND chat_jid = ?", args + [chat_jid]
    conn = _connect_ro(db)
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    if not rows:
        raise MediaError(f"message {message_id} not found in {db.name}")
    if len(rows) > 1:
        raise MediaError(f"message id {message_id} exists in {len(rows)} chats; pass --chat JID")
    if not rows[0][1]:
        raise MediaError(f"message {message_id} is not a media message")
    return rows[0]


def _bridge(post: Post, bridge_url: str, message_id: str, chat_jid: str) -> tuple[Path | None, str]:
    try:
        body = post(
            f"{bridge_url.rstrip('/')}/api/download",
            {"message_id": message_id, "chat_jid": chat_jid},
            BRIDGE_TIMEOUT,
        )
    except (OSError, ValueError) as exc:  # URLError is an OSError
        return None, f"bridge unreachable or invalid reply ({exc}); is the launchd bridge running?"
    if not isinstance(body, dict):
        return None, "bridge returned an unexpected response"
    if not body.get("success"):
        return None, str(body.get("message") or "bridge download failed")
    path = Path(str(body.get("path", "")))
    if not path.is_file():
        return None, "bridge reported success but the file is not on disk"
    return path, ""


def _desktop_lookup(message_id: str, chat_jid: str, root: Path) -> Path:
    db = root / "ChatStorage.sqlite"
    if not db.exists():
        raise MediaError(f"WhatsApp Desktop store not installed/found at {root}")
    try:
        conn = _connect_ro(db)
        try:
            rows = conn.execute(
                """
                SELECT i.ZMEDIALOCALPATH, s.ZCONTACTJID
                FROM ZWAMESSAGE m
                JOIN ZWAMEDIAITEM i ON i.Z_PK = m.ZMEDIAITEM
                LEFT JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
                WHERE m.ZSTANZAID = ?
                """,
                (message_id,),
            ).fetchall()
        finally:
            conn.close()
    except (sqlite3.OperationalError, PermissionError) as exc:
        raise MediaError(f"cannot read {db} ({exc}). {FDA_HINT}") from exc
    if not rows:
        raise MediaError("WhatsApp Desktop has no record of this message")
    rows.sort(key=lambda r: r[1] != chat_jid)  # same-chat match first
    rel = rows[0][0]
    if not rel:
        raise MediaError("WhatsApp Desktop never downloaded this media (no local path)")
    path = root / "Message" / rel
    if not path.is_file():
        raise MediaError(f"WhatsApp Desktop media file is missing on disk: {path}")
    return path


def resolve(
    message_id: str,
    chat_jid: str | None,
    *,
    messages_db: Path,
    bridge_url: str,
    desktop_root: Path,
    out_dir: Path,
    post: Post | None = None,
) -> Result:
    post = post or _post_json
    chat_jid, _ = _lookup(messages_db, message_id, chat_jid)
    dest_dir = out_dir / _safe(chat_jid)
    stem = _safe(message_id)

    for cached in dest_dir.glob("*") if dest_dir.is_dir() else ():
        if cached.stem == stem and cached.stat().st_size:
            return Result(message_id, chat_jid, cached, "cache", cached.stat().st_size)

    source = "bridge"
    src, why_bridge = _bridge(post, bridge_url, message_id, chat_jid)
    if src is None:
        source = "desktop"
        try:
            src = _desktop_lookup(message_id, chat_jid, desktop_root)
        except MediaError as exc:
            raise MediaError(f"bridge: {why_bridge} | {exc}") from exc

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}{src.suffix}"
    try:
        shutil.copy2(src, dest)
    except PermissionError as exc:
        raise MediaError(f"cannot read {src} ({exc}). {FDA_HINT}") from exc
    return Result(message_id, chat_jid, dest, source, dest.stat().st_size)


def latest(
    *,
    chat_jid: str | None = None,
    name: str | None = None,
    limit: int = 1,
    messages_db: Path,
    bridge_url: str,
    desktop_root: Path,
    out_dir: Path,
    post: Post | None = None,
) -> list[Result]:
    """Resolve the newest `limit` media messages; per-item failures land in Result.error."""
    if limit <= 0:
        return []
    if not messages_db.exists():
        raise MediaError(f"messages database not found at {messages_db} - has the bridge ever run?")
    conn = _connect_ro(messages_db)
    try:
        if name:
            row = conn.execute(
                "SELECT jid FROM chats WHERE name LIKE ? COLLATE NOCASE "
                "ORDER BY last_message_time DESC LIMIT 1",
                (f"%{name}%",),
            ).fetchone()
            if not row:
                raise MediaError(f"no chat matches name {name!r}")
            chat_jid = row[0]
        sql = "SELECT id, chat_jid FROM messages WHERE media_type IS NOT NULL AND media_type != ''"
        args: list = []
        if chat_jid:
            sql, args = sql + " AND chat_jid = ?", [chat_jid]
        rows = conn.execute(sql + " ORDER BY timestamp DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()

    results = []
    for message_id, jid in rows:
        try:
            results.append(
                resolve(message_id, jid, messages_db=messages_db, bridge_url=bridge_url,
                        desktop_root=desktop_root, out_dir=out_dir, post=post)
            )
        except MediaError as exc:
            results.append(Result(message_id, jid, error=str(exc)))
    return results
