"""Read-only SQLite queries against the bridge's store databases.

`search_contacts` queries `whatsmeow_contacts` in `whatsapp.db` — not the
`chats` table in `messages.db` (that was the approach used by the old
`whatsapp-mcp-server/whatsapp.py`; do not copy it here, per spec §2.3/§4.3).

Both databases are opened via a `file:...?mode=ro` URI so writes are
impossible at the SQLite level, never via the app-level connection.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Contact:
    their_jid: str
    first_name: str
    full_name: str
    push_name: str
    business_name: str


@dataclass
class Chat:
    jid: str
    name: str
    last_message_time: datetime
    kind: str


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _jid_kind(jid: str) -> str:
    if jid.endswith("@g.us"):
        return "group"
    if jid.endswith("@lid"):
        return "linked"
    return "direct"


def search_contacts(db_path: Path, query: str) -> list[Contact]:
    """Case-insensitive LIKE search across contact name fields and the JID.

    Matches against first_name, full_name, push_name, business_name, and
    their_jid. Deduplicates by their_jid (a their_jid can appear under more
    than one our_jid row).
    """
    like = f"%{query}%"
    conn = _connect_ro(db_path)
    try:
        rows = conn.execute(
            """
            SELECT their_jid,
                   COALESCE(first_name, '') AS first_name,
                   COALESCE(full_name, '') AS full_name,
                   COALESCE(push_name, '') AS push_name,
                   COALESCE(business_name, '') AS business_name
            FROM whatsmeow_contacts
            WHERE first_name LIKE ? COLLATE NOCASE
               OR full_name LIKE ? COLLATE NOCASE
               OR push_name LIKE ? COLLATE NOCASE
               OR business_name LIKE ? COLLATE NOCASE
               OR their_jid LIKE ? COLLATE NOCASE
            ORDER BY their_jid
            """,
            (like, like, like, like, like),
        ).fetchall()
    finally:
        conn.close()

    seen: set[str] = set()
    contacts: list[Contact] = []
    for their_jid, first_name, full_name, push_name, business_name in rows:
        if their_jid in seen:
            continue
        seen.add(their_jid)
        contacts.append(
            Contact(
                their_jid=their_jid,
                first_name=first_name,
                full_name=full_name,
                push_name=push_name,
                business_name=business_name,
            )
        )
    return contacts


def list_chats(db_path: Path, limit: int = 20) -> list[Chat]:
    """Most-recently-active chats, ordered by last_message_time DESC.

    Timestamps are stored as ISO-8601 text with a UTC offset (lexically
    sortable, hence the plain ORDER BY), and are parsed into datetimes via
    datetime.fromisoformat. A null chat name falls back to the JID.
    """
    conn = _connect_ro(db_path)
    try:
        rows = conn.execute(
            """
            SELECT jid, name, last_message_time
            FROM chats
            ORDER BY last_message_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    chats: list[Chat] = []
    for jid, name, last_message_time in rows:
        chats.append(
            Chat(
                jid=jid,
                name=name if name else jid,
                last_message_time=datetime.fromisoformat(last_message_time),
                kind=_jid_kind(jid),
            )
        )
    return chats
