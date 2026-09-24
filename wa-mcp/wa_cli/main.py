"""Typer app wiring all eight `wa` commands.

Renders every output via wa_cli.ui (banner, badges, styled tables, error
panels) and delegates to config/api/db/daemon for behavior. Every command
exits non-zero on failure via typer.Exit(code=1).
"""

from __future__ import annotations

import difflib
import json
import shutil
import time
from pathlib import Path

import typer

from wa_cli import agent, api, config, daemon, db, ui

app = typer.Typer(
    name="wa",
    help="Operate the local WhatsApp bridge + MCP stack.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

DEFAULT_TAIL_LINES = 50

# Anti-repeat guard: how many trailing messages to inspect, and how close a
# match has to be (via difflib.SequenceMatcher ratio) to count as a repeat.
RECENT_CONTEXT_LIMIT = 10
DUPLICATE_RATIO_THRESHOLD = 0.92


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def _fetch_recent(recipient: str) -> list[db.Message]:
    """Best-effort recent-thread fetch for the duplicate guard.

    Any failure (missing DB, unreadable file, unresolved chat) yields an
    empty list rather than blocking the send — context is a nice-to-have,
    a fetch failure must never swallow a real send.
    """
    try:
        db_path = config.messages_db()
        if not db_path.exists():
            return []
        chat_jid = db.resolve_chat_jid(db_path, recipient)
        if not chat_jid:
            return []
        return db.recent_messages(db_path, chat_jid, limit=RECENT_CONTEXT_LIMIT)
    except Exception:
        return []


def _find_duplicate_outbound(message: str, recent: list[db.Message]) -> db.Message | None:
    """Return the recent outbound message this text duplicates, or None."""
    normalized_outgoing = _normalize_text(message)
    for msg in recent:
        if not msg.is_from_me:
            continue
        normalized_existing = _normalize_text(msg.text)
        if normalized_existing == normalized_outgoing:
            return msg
        ratio = difflib.SequenceMatcher(None, normalized_existing, normalized_outgoing).ratio()
        if ratio >= DUPLICATE_RATIO_THRESHOLD:
            return msg
    return None


def _render_recent_thread(recent: list[db.Message]) -> str:
    """Compact recent-thread view, printed before every send attempt."""
    lines = ["recent thread:"]
    for m in recent:
        who = "me" if m.is_from_me else "them"
        lines.append(f"  [{who}] {m.timestamp.isoformat()} {m.text[:200]}")
    return "\n".join(lines)


def _fail(msg: str) -> None:
    ui.console.print(ui.error_panel(msg))
    raise typer.Exit(code=1)


@app.command()
def up() -> None:
    """Start the bridge and MCP daemons as detached background processes."""
    ui.console.print(ui.banner())
    result = daemon.up_all()

    bridge_state = "up" if result.bridge.healthy else "down"
    mcp_state = "up" if (result.mcp.already_running or result.mcp.pid) else "down"

    bridge_note = "already running" if result.bridge.already_running else f"started (pid {result.bridge.pid})"
    mcp_note = "already running" if result.mcp.already_running else f"started (pid {result.mcp.pid})"

    ui.console.print(ui.badge(bridge_state), f"bridge — {bridge_note}")
    ui.console.print(ui.badge(mcp_state), f"mcp — {mcp_note}")

    if not result.bridge.healthy:
        _fail("bridge did not become healthy within the health-check window")


@app.command()
def down() -> None:
    """Stop the bridge and MCP daemons."""
    daemon.down_all()
    ui.console.print(ui.badge("down"), "bridge and mcp stopped")


@app.command()
def status() -> None:
    """Show daemon status: pid, alive state, and bridge REST reachability."""
    ui.console.print(ui.banner())
    statuses = daemon.statuses()

    table = ui.styled_table("wa status", ["Service", "PID", "Alive", "REST"])
    for svc in (statuses.bridge, statuses.mcp):
        pid_str = str(svc.pid) if svc.pid is not None else "—"
        alive_badge = ui.badge("up" if svc.alive else "down")
        if svc.rest_reachable is None:
            rest_str = "—"
        else:
            rest_str = str(ui.badge("up" if svc.rest_reachable else "down"))
        table.add_row(svc.name, pid_str, str(alive_badge), rest_str)

    ui.console.print(table)


@app.command()
def logs(
    follow: bool = typer.Option(False, "-f", "--follow", help="Stream appended log lines until interrupted."),
) -> None:
    """Tail the bridge and MCP daemon logs (last 50 lines by default)."""
    bridge_log = config.bridge_log()
    mcp_log = config.mcp_log()

    if not follow:
        for name, logfile in (("bridge", bridge_log), ("mcp", mcp_log)):
            ui.console.print(f"── {name} ──")
            for line in daemon.tail_lines(logfile, DEFAULT_TAIL_LINES):
                ui.console.print(line.rstrip("\n"))
        return

    offsets = {bridge_log: 0, mcp_log: 0}
    try:
        while True:
            for name, logfile in (("bridge", bridge_log), ("mcp", mcp_log)):
                new_lines, offsets[logfile] = daemon.follow_step(logfile, offsets[logfile])
                for line in new_lines:
                    ui.console.print(f"[{name}] {line.rstrip(chr(10))}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


@app.command()
def send(
    recipient: str,
    message: str,
    force: bool = typer.Option(
        False, "--force", help="Send even if it looks identical to a message we already sent."
    ),
) -> None:
    """Send a WhatsApp message via the bridge REST API.

    RECIPIENT accepts any of the three JID forms (<digits>@s.whatsapp.net,
    <digits>@lid, <digits>@g.us) or plain phone digits.

    Review the recent thread printed below before composing; never resend a
    message already in it or re-answer something the other party already
    replied to. Near-duplicate outbound messages are blocked unless --force
    is passed.
    """
    recent = _fetch_recent(recipient)
    if recent:
        ui.console.print(_render_recent_thread(recent))

    if not force:
        duplicate = _find_duplicate_outbound(message, recent)
        if duplicate is not None:
            _fail(
                "not sent: this message looks identical to one we already sent at "
                f"{duplicate.timestamp.isoformat()}. Pass --force to send it anyway."
            )

    with ui.spinner(f"Sending to {recipient}..."):
        ok, detail = api.send_message(recipient, message, base_url=config.bridge_url())

    if ok:
        ui.console.print(ui.badge("up"), detail or "sent")
    else:
        _fail(detail or "send failed")


@app.command()
def contacts(query: str) -> None:
    """Search contacts by name, push name, business name, or JID."""
    db_path = config.contacts_db()
    if not db_path.exists():
        _fail(f"contacts database not found at {db_path} — has the bridge ever run?")

    results = db.search_contacts(db_path, query)

    table = ui.styled_table("wa contacts", ["JID", "Name", "Push name", "Business name"])
    for c in results:
        name = c.full_name or c.push_name or c.first_name
        table.add_row(c.their_jid, name, c.push_name, c.business_name)

    ui.console.print(table)
    ui.console.print(f"{len(results)} match(es)")


@app.command()
def chats(limit: int = typer.Option(20, "--limit", help="Number of chats to show.")) -> None:
    """List the most recently active chats."""
    db_path = config.messages_db()
    if not db_path.exists():
        _fail(f"messages database not found at {db_path} — has the bridge ever run?")

    results = db.list_chats(db_path, limit=limit)

    table = ui.styled_table("wa chats", ["Name", "JID", "Last activity", "Kind"])
    for c in results:
        table.add_row(c.name, c.jid, c.last_message_time.isoformat(), c.kind)

    ui.console.print(table)


@app.command()
def download(
    message_id: str = typer.Argument(..., help="ID of the message with media."),
    chat_jid: str = typer.Argument(..., help="JID of the chat containing the message."),
) -> None:
    """Download media attachment for a message."""
    with ui.spinner(f"Downloading media for {message_id}..."):
        ok, filename, path_or_detail = api.download_media(message_id, chat_jid, base_url=config.bridge_url())

    if ok:
        ui.console.print(ui.badge("up"), f"Downloaded {filename or 'media'} to {path_or_detail}")
    else:
        _fail(f"download failed: {path_or_detail}")


@app.command()
def doctor() -> None:
    """Aggregated health report: binary, uv, daemons, REST, and store DBs."""
    ui.console.print(ui.banner())
    checks: list[tuple[str, bool, str]] = []

    bridge_bin = config.bridge_binary()
    bridge_bin_ok = bridge_bin.exists() and (Path(bridge_bin).stat().st_mode & 0o111 != 0)
    checks.append(("bridge binary present & executable", bridge_bin_ok, f"expected at {bridge_bin}"))

    uv_ok = shutil.which("uv") is not None
    checks.append(("uv on PATH", uv_ok, "install uv: https://docs.astral.sh/uv/"))

    statuses = daemon.statuses()
    daemons_ok = statuses.bridge.alive and statuses.mcp.alive
    checks.append(("daemons running", daemons_ok, "run `wa up` to start the bridge and mcp daemons"))

    rest_ok = bool(statuses.bridge.rest_reachable)
    checks.append(("bridge REST reachable on 8080", rest_ok, "run `wa up` or check bridge.log"))

    dbs_ok = True
    db_detail = ""
    for label, path, check in (
        ("whatsapp.db", config.contacts_db(), lambda p: db.search_contacts(p, "")),
        ("messages.db", config.messages_db(), lambda p: db.list_chats(p, limit=1)),
    ):
        try:
            if not path.exists():
                raise FileNotFoundError(path)
            check(path)
        except Exception:
            dbs_ok = False
            db_detail = f"{label} not present or unreadable at {path}"
    checks.append(("store DBs present & readable", dbs_ok, db_detail or "check the store/ directory"))

    all_ok = True
    for label, ok, hint in checks:
        state = "up" if ok else "down"
        ui.console.print(ui.badge(state), label if ok else f"{label} — {hint}")
        all_ok = all_ok and ok

    if all_ok:
        ui.console.print(ui.badge("up"), "all checks passed")
    else:
        _fail("one or more doctor checks failed")


agent_app = typer.Typer(
    name="agent",
    help="Machine-readable schema and playbook for LLM/automation use.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(agent_app, name="agent")


@agent_app.command("schema")
def agent_schema_cmd() -> None:
    """Print JSON schema of every stable command + params."""
    typer.echo(json.dumps(agent.build_schema(), indent=2))


@agent_app.command("guide")
def agent_guide_cmd() -> None:
    """Print a short markdown playbook for LLM agents."""
    typer.echo(agent.build_guide(), nl=False)
