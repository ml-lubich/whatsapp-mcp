"""Integration tests for the wa_cli.main typer app via CliRunner.

Never spawns real processes, never hits the real (live) bridge, never reads
the real store DBs — every test monkeypatches the api/db/daemon module
functions that wa_cli.main calls.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from wa_cli import agent, api, daemon, db, main

runner = CliRunner()


def _flat(output: str) -> str:
    """Collapse rich's box-drawing/line-wrapping so multi-word phrases can be
    asserted on regardless of where the terminal width wrapped them."""
    return " ".join(output.split())

EXPECTED_COMMANDS = {
    "up",
    "down",
    "status",
    "logs",
    "send",
    "contacts",
    "chats",
    "download",
    "doctor",
}


# --- --help lists exactly the eight commands ---


def test_help_lists_exactly_eight_commands():
    result = runner.invoke(main.app, ["--help"])
    assert result.exit_code == 0
    for cmd in EXPECTED_COMMANDS:
        assert cmd in result.output


def test_app_registers_exactly_eight_commands():
    registered = {c.name or c.callback.__name__ for c in main.app.registered_commands}
    assert registered == EXPECTED_COMMANDS


# --- up / down / status ---


def test_up_reports_success(monkeypatch):
    result_obj = daemon.UpResult(
        bridge=daemon.StartResult(name="bridge", pid=1, already_running=False, healthy=True),
        mcp=daemon.StartResult(name="mcp", pid=2, already_running=False),
    )
    monkeypatch.setattr(daemon, "up_all", lambda **kwargs: result_obj)

    result = runner.invoke(main.app, ["up"])

    assert result.exit_code == 0
    assert "bridge" in result.output.lower()
    assert "mcp" in result.output.lower()


def test_up_reports_unhealthy_bridge_nonzero_exit(monkeypatch):
    result_obj = daemon.UpResult(
        bridge=daemon.StartResult(name="bridge", pid=1, already_running=False, healthy=False),
        mcp=daemon.StartResult(name="mcp", pid=2, already_running=False),
    )
    monkeypatch.setattr(daemon, "up_all", lambda **kwargs: result_obj)

    result = runner.invoke(main.app, ["up"])

    assert result.exit_code != 0


def test_down_reports_success(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(daemon, "down_all", lambda **kwargs: called.__setitem__("n", called["n"] + 1))

    result = runner.invoke(main.app, ["down"])

    assert result.exit_code == 0
    assert called["n"] == 1


def test_status_renders_table(monkeypatch):
    statuses = daemon.Statuses(
        bridge=daemon.ServiceStatus(name="bridge", pid=123, alive=True, rest_reachable=True),
        mcp=daemon.ServiceStatus(name="mcp", pid=456, alive=True, rest_reachable=None),
    )
    monkeypatch.setattr(daemon, "statuses", lambda **kwargs: statuses)

    result = runner.invoke(main.app, ["status"])

    assert result.exit_code == 0
    assert "123" in result.output
    assert "456" in result.output


def test_status_down_daemons_still_exit_zero(monkeypatch):
    statuses = daemon.Statuses(
        bridge=daemon.ServiceStatus(name="bridge", pid=None, alive=False, rest_reachable=False),
        mcp=daemon.ServiceStatus(name="mcp", pid=None, alive=False, rest_reachable=None),
    )
    monkeypatch.setattr(daemon, "statuses", lambda **kwargs: statuses)

    result = runner.invoke(main.app, ["status"])

    assert result.exit_code == 0


# --- logs ---


def test_logs_tails_default_50_lines(monkeypatch, tmp_path):
    monkeypatch.setattr(main.config, "bridge_log", lambda: tmp_path / "bridge.log")
    monkeypatch.setattr(main.config, "mcp_log", lambda: tmp_path / "mcp.log")

    captured = {}

    def fake_tail_lines(logfile, n):
        captured[str(logfile)] = n
        return [f"line from {logfile.name}\n"]

    monkeypatch.setattr(daemon, "tail_lines", fake_tail_lines)

    result = runner.invoke(main.app, ["logs"])

    assert result.exit_code == 0
    assert set(captured.values()) == {50}


def test_logs_follow_flag_uses_follow_step(monkeypatch, tmp_path):
    bridge_log = tmp_path / "bridge.log"
    mcp_log = tmp_path / "mcp.log"
    monkeypatch.setattr(main.config, "bridge_log", lambda: bridge_log)
    monkeypatch.setattr(main.config, "mcp_log", lambda: mcp_log)
    monkeypatch.setattr(daemon, "tail_lines", lambda logfile, n: [])

    calls = {"n": 0}

    def fake_follow_step(logfile, offset):
        calls["n"] += 1
        if calls["n"] > 2:
            raise KeyboardInterrupt
        return ([f"new line {calls['n']}\n"], offset + 1)

    monkeypatch.setattr(daemon, "follow_step", fake_follow_step)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    result = runner.invoke(main.app, ["logs", "-f"])

    assert calls["n"] >= 1
    assert result.exit_code == 0


# --- send ---


def test_send_happy_path(monkeypatch):
    monkeypatch.setattr(main.config, "messages_db", lambda: Path("/does-not-exist"))
    monkeypatch.setattr(api, "send_message", lambda recipient, message, *, base_url: (True, "sent ok"))

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", "hello"])

    assert result.exit_code == 0
    assert "sent ok" in result.output


def test_send_failure_exits_nonzero(monkeypatch):
    monkeypatch.setattr(main.config, "messages_db", lambda: Path("/does-not-exist"))
    monkeypatch.setattr(
        api, "send_message", lambda recipient, message, *, base_url: (False, "bridge not running — try `wa up`")
    )

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", "hello"])

    assert result.exit_code != 0
    assert "bridge not running" in result.output


# --- contacts ---


def test_contacts_happy_path(monkeypatch, sample_dbs):
    monkeypatch.setattr(main.config, "contacts_db", lambda: sample_dbs.whatsapp_db)

    result = runner.invoke(main.app, ["contacts", "Alice"])

    assert result.exit_code == 0
    assert "Alice" in result.output


def test_contacts_no_matches_exits_zero(monkeypatch, sample_dbs):
    monkeypatch.setattr(main.config, "contacts_db", lambda: sample_dbs.whatsapp_db)

    result = runner.invoke(main.app, ["contacts", "zzz-no-such-contact"])

    assert result.exit_code == 0


def test_contacts_db_missing_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr(main.config, "contacts_db", lambda: tmp_path / "does-not-exist.db")

    result = runner.invoke(main.app, ["contacts", "Alice"])

    assert result.exit_code != 0


# --- chats ---


def test_chats_default_limit(monkeypatch, sample_dbs):
    monkeypatch.setattr(main.config, "messages_db", lambda: sample_dbs.messages_db)

    result = runner.invoke(main.app, ["chats"])

    assert result.exit_code == 0
    assert "Bob Baker" in result.output


def test_chats_custom_limit(monkeypatch, sample_dbs):
    monkeypatch.setattr(main.config, "messages_db", lambda: sample_dbs.messages_db)

    result = runner.invoke(main.app, ["chats", "--limit", "2"])

    assert result.exit_code == 0


def test_chats_db_missing_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")

    result = runner.invoke(main.app, ["chats"])

    assert result.exit_code != 0


# --- download ---


def test_download_success(monkeypatch):
    monkeypatch.setattr(main.api, "download_media", lambda m, c, base_url: (True, "doc.pdf", "/tmp/doc.pdf"))

    result = runner.invoke(main.app, ["download", "msg123", "chat456@s.whatsapp.net"])

    assert result.exit_code == 0
    assert "Downloaded doc.pdf to /tmp/doc.pdf" in result.output


def test_download_failure(monkeypatch):
    monkeypatch.setattr(main.api, "download_media", lambda m, c, base_url: (False, "", "media not found"))

    result = runner.invoke(main.app, ["download", "msg123", "chat456@s.whatsapp.net"])

    assert result.exit_code != 0
    assert "download failed: media not found" in result.output


# --- doctor ---


def _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs):
    bridge_binary = tmp_path / "whatsapp-bridge"
    bridge_binary.write_text("#!/bin/sh\n")
    bridge_binary.chmod(0o755)
    monkeypatch.setattr(main.config, "bridge_binary", lambda: bridge_binary)
    monkeypatch.setattr(main.config, "contacts_db", lambda: sample_dbs.whatsapp_db)
    monkeypatch.setattr(main.config, "messages_db", lambda: sample_dbs.messages_db)
    monkeypatch.setattr(main.shutil, "which", lambda name: "/usr/bin/uv")

    statuses = daemon.Statuses(
        bridge=daemon.ServiceStatus(name="bridge", pid=1, alive=True, rest_reachable=True),
        mcp=daemon.ServiceStatus(name="mcp", pid=2, alive=True, rest_reachable=None),
    )
    monkeypatch.setattr(daemon, "statuses", lambda **kwargs: statuses)


def test_doctor_all_checks_pass(monkeypatch, tmp_path, sample_dbs):
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code == 0


def test_doctor_missing_bridge_binary_fails(monkeypatch, tmp_path, sample_dbs):
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)
    monkeypatch.setattr(main.config, "bridge_binary", lambda: tmp_path / "does-not-exist-binary")

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code != 0


def test_doctor_repo_root_unresolvable_fails_cleanly_not_a_crash(monkeypatch, tmp_path, sample_dbs):
    # Regression: a fresh `pip install wa-mcp` / `uv tool install` runs outside
    # the whatsapp-mcp repo clone, so config.repo_root() (called transitively
    # by bridge_binary()) raises RuntimeError. `wa doctor` exists specifically
    # to diagnose a broken install, so it must report this as a clean failed
    # check line, never let the RuntimeError escape as an unhandled traceback.
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)

    def _raise_no_repo():
        raise RuntimeError("could not locate repo root: no whatsapp-bridge/ directory found")

    monkeypatch.setattr(main.config, "bridge_binary", _raise_no_repo)

    result = runner.invoke(main.app, ["doctor"])

    # A clean, handled failure raises typer.Exit -> SystemExit(1); the raw
    # RuntimeError from repo_root() must never be what escapes.
    assert not isinstance(result.exception, RuntimeError)
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "WA_REPO" in result.output


def test_doctor_contacts_db_repo_root_unresolvable_fails_cleanly(monkeypatch, tmp_path, sample_dbs):
    # Same fresh-install scenario, but hitting contacts_db()'s repo_root()
    # call in the store-DBs check loop rather than bridge_binary()'s.
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)

    def _raise_no_repo():
        raise RuntimeError("could not locate repo root")

    monkeypatch.setattr(main.config, "contacts_db", _raise_no_repo)

    result = runner.invoke(main.app, ["doctor"])

    assert not isinstance(result.exception, RuntimeError)
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_doctor_uv_missing_from_path_fails(monkeypatch, tmp_path, sample_dbs):
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)
    monkeypatch.setattr(main.shutil, "which", lambda name: None)

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code != 0


def test_doctor_daemons_not_running_fails(monkeypatch, tmp_path, sample_dbs):
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)
    statuses = daemon.Statuses(
        bridge=daemon.ServiceStatus(name="bridge", pid=None, alive=False, rest_reachable=False),
        mcp=daemon.ServiceStatus(name="mcp", pid=None, alive=False, rest_reachable=None),
    )
    monkeypatch.setattr(daemon, "statuses", lambda **kwargs: statuses)

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code != 0


def test_doctor_db_unreadable_fails(monkeypatch, tmp_path, sample_dbs):
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)
    monkeypatch.setattr(main.config, "contacts_db", lambda: tmp_path / "missing-contacts.db")

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code != 0


def test_doctor_corrupt_db_file_fails_cleanly(monkeypatch, tmp_path, sample_dbs):
    """doctor's checks are already wrapped in try/except — a corrupt (not a
    valid sqlite file) contacts_db must fail the check, not crash the CLI."""
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)
    corrupt = tmp_path / "corrupt-whatsapp.db"
    corrupt.write_bytes(b"not a sqlite file at all, just garbage bytes")
    monkeypatch.setattr(main.config, "contacts_db", lambda: corrupt)

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)  # a clean typer.Exit, not an unhandled traceback


# --- --help output for every command ---

_HELP_CASES = [
    pytest.param(["up", "--help"], ["Usage: wa up", "--help", "-h"], id="up"),
    pytest.param(["down", "--help"], ["Usage: wa down", "--help", "-h"], id="down"),
    pytest.param(["status", "--help"], ["Usage: wa status", "--help", "-h"], id="status"),
    pytest.param(["logs", "--help"], ["Usage: wa logs", "--follow", "-f", "--help", "-h"], id="logs"),
    pytest.param(
        ["send", "--help"],
        ["Usage: wa send", "RECIPIENT", "MESSAGE", "--force", "--help", "-h"],
        id="send",
    ),
    pytest.param(["contacts", "--help"], ["Usage: wa contacts", "QUERY", "--help", "-h"], id="contacts"),
    pytest.param(
        ["chats", "--help"],
        ["Usage: wa chats", "--limit", "INTEGER", "[default: 20]", "--help", "-h"],
        id="chats",
    ),
    pytest.param(
        ["download", "--help"],
        ["Usage: wa download", "MESSAGE_ID", "CHAT_JID", "--help", "-h"],
        id="download",
    ),
    pytest.param(["doctor", "--help"], ["Usage: wa doctor", "--help", "-h"], id="doctor"),
    pytest.param(["agent", "--help"], ["Usage: wa agent", "schema", "guide", "--help", "-h"], id="agent-group"),
    pytest.param(
        ["agent", "schema", "--help"], ["Usage: wa agent schema", "--help", "-h"], id="agent-schema-long"
    ),
    pytest.param(["agent", "schema", "-h"], ["Usage: wa agent schema", "--help", "-h"], id="agent-schema-short"),
    pytest.param(["agent", "guide", "--help"], ["Usage: wa agent guide", "--help", "-h"], id="agent-guide-long"),
    pytest.param(["agent", "guide", "-h"], ["Usage: wa agent guide", "--help", "-h"], id="agent-guide-short"),
]


@pytest.mark.parametrize("args, expected_substrings", _HELP_CASES)
def test_command_help_exits_zero_and_shows_expected_content(args, expected_substrings):
    result = runner.invoke(main.app, args)
    flat = _flat(result.output)

    assert result.exit_code == 0
    for substring in expected_substrings:
        assert substring in flat


# --- missing required arguments ---

_MISSING_ARG_CASES = [
    pytest.param(["send"], "Missing argument 'RECIPIENT'", id="send-0-of-2"),
    pytest.param(["send", "14157863858@s.whatsapp.net"], "Missing argument 'MESSAGE'", id="send-1-of-2"),
    pytest.param(["contacts"], "Missing argument 'QUERY'", id="contacts-0-of-1"),
    pytest.param(["download"], "Missing argument 'MESSAGE_ID'", id="download-0-of-2"),
    pytest.param(["download", "msg123"], "Missing argument 'CHAT_JID'", id="download-1-of-2"),
]


@pytest.mark.parametrize("args, expected_message", _MISSING_ARG_CASES)
def test_missing_required_argument_is_usage_error(args, expected_message):
    result = runner.invoke(main.app, args)

    assert result.exit_code != 0
    assert result.exit_code == 2
    flat = _flat(result.output)
    assert "Usage:" in flat
    assert expected_message in flat


# --- unknown/extra positional args and unknown flags ---

_UNKNOWN_ARG_CASES = [
    pytest.param(["send", "x", "y", "z"], id="send-extra-positional"),
    pytest.param(["status", "--bogus-flag"], id="status-bogus-flag"),
    pytest.param(["up", "--bogus-flag"], id="up-bogus-flag"),
    pytest.param(["down", "--bogus-flag"], id="down-bogus-flag"),
    pytest.param(["logs", "--bogus-flag"], id="logs-bogus-flag"),
    pytest.param(["contacts", "a", "b"], id="contacts-extra-positional"),
    pytest.param(["chats", "--bogus-flag"], id="chats-bogus-flag"),
    pytest.param(["doctor", "--bogus-flag"], id="doctor-bogus-flag"),
    pytest.param(["download", "a", "b", "c"], id="download-extra-positional"),
    pytest.param(["download", "--bogus-flag", "a", "b"], id="download-bogus-flag"),
    pytest.param(["bogus-top-level-command"], id="unknown-top-level-command"),
]


@pytest.mark.parametrize("args", _UNKNOWN_ARG_CASES)
def test_unknown_args_or_flags_is_usage_error(args):
    result = runner.invoke(main.app, args)

    assert result.exit_code != 0
    assert result.exit_code == 2
    flat = _flat(result.output)
    assert "Usage:" in flat


# --- chats --limit edge cases ---


def test_chats_limit_non_integer_is_usage_error(monkeypatch, sample_dbs):
    monkeypatch.setattr(main.config, "messages_db", lambda: sample_dbs.messages_db)

    result = runner.invoke(main.app, ["chats", "--limit", "abc"])

    assert result.exit_code == 2
    flat = _flat(result.output)
    assert "not a valid integer" in flat


@pytest.mark.parametrize(
    "limit_arg, expected_value",
    [
        pytest.param("-1", -1, id="negative-one"),
        pytest.param("-100", -100, id="very-negative"),
        pytest.param("0", 0, id="zero"),
        pytest.param("999999999", 999999999, id="extremely-large"),
    ],
)
def test_chats_limit_passthrough_current_behavior(monkeypatch, tmp_path, limit_arg, expected_value):
    """main.py does not validate --limit; it passes whatever int typer parses
    straight to db.list_chats. This documents that current behavior exactly
    (SQLite treats a negative LIMIT as "no limit", so this does not crash)."""
    existing_db = tmp_path / "messages.db"
    existing_db.write_text("")
    monkeypatch.setattr(main.config, "messages_db", lambda: existing_db)

    captured = {}

    def fake_list_chats(db_path, limit=20):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(db, "list_chats", fake_list_chats)

    result = runner.invoke(main.app, ["chats", "--limit", limit_arg])

    assert result.exit_code == 0
    assert captured["limit"] == expected_value


# --- send: unicode/emoji, long messages, flag-lookalike, force+failure ---

_SEND_TEXT_CASES = [
    pytest.param("😀🎉👍🏽 see you at 5pm!", id="emoji-message"),
    pytest.param("مرحبا، هل نلتقي غدا؟", id="rtl-arabic-message"),
    pytest.param("café — naïve — résumé", id="accented-latin-message"),
    pytest.param("日本語のメッセージです", id="japanese-message"),
]


@pytest.mark.parametrize("message", _SEND_TEXT_CASES)
def test_send_unicode_message_passes_through_unmangled(monkeypatch, tmp_path, message):
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = []

    def fake_send(recipient, msg, *, base_url):
        calls.append((recipient, msg))
        return True, "sent ok"

    monkeypatch.setattr(api, "send_message", fake_send)

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", message])

    assert result.exit_code == 0
    assert calls == [("14157863858@s.whatsapp.net", message)]


def test_send_unicode_recipient_passes_through_unmangled(monkeypatch, tmp_path):
    # main.py never validates recipient format — it forwards whatever the
    # operator typed straight to api.send_message. Prove an unusual
    # (non-JID) unicode recipient string survives untouched too.
    recipient = "Đặng Văn 😀"
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = []

    def fake_send(r, m, *, base_url):
        calls.append((r, m))
        return True, "sent ok"

    monkeypatch.setattr(api, "send_message", fake_send)

    result = runner.invoke(main.app, ["send", recipient, "hi"])

    assert result.exit_code == 0
    assert calls == [(recipient, "hi")]


def test_send_very_long_message_passes_through_unmangled(monkeypatch, tmp_path):
    long_message = "x" * 50_000
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = []

    def fake_send(recipient, msg, *, base_url):
        calls.append((recipient, msg))
        return True, "sent ok"

    monkeypatch.setattr(api, "send_message", fake_send)

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", long_message])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert len(calls[0][1]) == 50_000
    assert calls[0][1] == long_message


def test_send_message_that_looks_like_a_flag_without_separator_is_misparsed(monkeypatch, tmp_path):
    """`wa send RECIPIENT --force` is a real user footgun: click sees
    `--force` as the boolean --force option (matching the send command's own
    flag), not as the MESSAGE positional, and MESSAGE ends up missing. This
    documents the actual (click-standard) behavior rather than a bug in
    main.py — the fix is the `--` separator, tested below."""
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = []

    def fake_send(r, m, *, base_url):
        calls.append((r, m))
        return True, "ok"

    monkeypatch.setattr(api, "send_message", fake_send)

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", "--force"])

    assert result.exit_code == 2
    assert "Missing argument 'MESSAGE'" in _flat(result.output)
    assert calls == []


def test_send_message_that_looks_like_a_flag_with_separator_sends_literal_text(monkeypatch, tmp_path):
    """The documented escape hatch for the footgun above: `--` tells click
    "everything after this is positional", so the literal string "--force"
    reaches MESSAGE, not the --force option."""
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = []

    def fake_send(recipient, msg, *, base_url):
        calls.append((recipient, msg))
        return True, "sent ok"

    monkeypatch.setattr(api, "send_message", fake_send)

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", "--", "--force"])

    assert result.exit_code == 0
    assert calls == [("14157863858@s.whatsapp.net", "--force")]


def test_send_force_flag_with_db_read_failure_still_sends(monkeypatch, tmp_path):
    """Mirrors test_send_db_read_failure_still_sends in test_send_guard.py,
    but with --force also passed: a broken context fetch must still never
    block a real send, force or not."""
    fake_db = tmp_path / "messages.db"
    fake_db.write_text("")
    monkeypatch.setattr(main.config, "messages_db", lambda: fake_db)

    def _raise(db_path, recipient):
        raise RuntimeError("db read error")

    monkeypatch.setattr(db, "resolve_chat_jid", _raise)

    calls = []

    def fake_send(recipient, msg, *, base_url):
        calls.append((recipient, msg))
        return True, "sent ok"

    monkeypatch.setattr(api, "send_message", fake_send)

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", "hi", "--force"])

    assert result.exit_code == 0
    assert calls == [("14157863858@s.whatsapp.net", "hi")]


# --- contacts / chats: corrupt (not-a-sqlite-file) db ---


def _write_corrupt_db(path: Path) -> None:
    path.write_bytes(b"this is not a sqlite database, just garbage bytes 0123456789")


def test_contacts_corrupt_db_fails_cleanly_not_a_traceback(monkeypatch, tmp_path):
    """Before the fix, an unhandled sqlite3.DatabaseError propagated out of
    `wa contacts` instead of a clean `_fail()` message. main.py now wraps
    db.search_contacts in try/except and reports a clean error."""
    corrupt = tmp_path / "corrupt-whatsapp.db"
    _write_corrupt_db(corrupt)
    monkeypatch.setattr(main.config, "contacts_db", lambda: corrupt)

    result = runner.invoke(main.app, ["contacts", "Alice"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)  # a clean typer.Exit, not an unhandled traceback
    assert "could not read" in _flat(result.output).lower()


def test_chats_corrupt_db_fails_cleanly_not_a_traceback(monkeypatch, tmp_path):
    """Same fix, for `wa chats` / db.list_chats."""
    corrupt = tmp_path / "corrupt-messages.db"
    _write_corrupt_db(corrupt)
    monkeypatch.setattr(main.config, "messages_db", lambda: corrupt)

    result = runner.invoke(main.app, ["chats"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)  # a clean typer.Exit, not an unhandled traceback
    assert "could not read" in _flat(result.output).lower()


def test_contacts_corrupt_db_real_sqlite_error_surfaces_in_message(monkeypatch, tmp_path):
    """Cross-check against an actual sqlite3 error (not just garbage bytes) —
    a db file that opens fine but has no whatsmeow_contacts table at all."""
    empty_db = tmp_path / "empty.db"
    conn = sqlite3.connect(empty_db)
    conn.close()
    monkeypatch.setattr(main.config, "contacts_db", lambda: empty_db)

    result = runner.invoke(main.app, ["contacts", "Alice"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)  # a clean typer.Exit, not an unhandled traceback
    flat = _flat(result.output).lower()
    assert "could not read" in flat
    assert "no such table" in flat


def test_chats_corrupt_db_real_sqlite_error_surfaces_in_message(monkeypatch, tmp_path):
    empty_db = tmp_path / "empty.db"
    conn = sqlite3.connect(empty_db)
    conn.close()
    monkeypatch.setattr(main.config, "messages_db", lambda: empty_db)

    result = runner.invoke(main.app, ["chats"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)  # a clean typer.Exit, not an unhandled traceback
    flat = _flat(result.output).lower()
    assert "could not read" in flat
    assert "no such table" in flat


# --- logs -f: multiple follow iterations, interleaved batches ---


def test_logs_follow_multiple_interleaved_batches_until_interrupt(monkeypatch, tmp_path):
    bridge_log = tmp_path / "bridge.log"
    mcp_log = tmp_path / "mcp.log"
    monkeypatch.setattr(main.config, "bridge_log", lambda: bridge_log)
    monkeypatch.setattr(main.config, "mcp_log", lambda: mcp_log)
    monkeypatch.setattr(daemon, "tail_lines", lambda logfile, n: [])
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    # Iteration 1: bridge has 2 new lines, mcp has 0.
    # Iteration 2: bridge has 0 new lines, mcp has 3.
    # Iteration 3: both have 1 new line each.
    # Iteration 4: raise KeyboardInterrupt.
    batches = {
        bridge_log: [["b1\n", "b2\n"], [], ["b3\n"]],
        mcp_log: [[], ["m1\n", "m2\n", "m3\n"], ["m4\n"]],
    }
    call_counts = {bridge_log: 0, mcp_log: 0}

    def fake_follow_step(logfile, offset):
        idx = call_counts[logfile]
        call_counts[logfile] += 1
        if idx >= len(batches[logfile]):
            raise KeyboardInterrupt
        lines = batches[logfile][idx]
        return (lines, offset + len(lines))

    monkeypatch.setattr(daemon, "follow_step", fake_follow_step)

    result = runner.invoke(main.app, ["logs", "-f"])

    assert result.exit_code == 0
    assert "b1" in result.output
    assert "b2" in result.output
    assert "m1" in result.output
    assert "m3" in result.output
    assert "b3" in result.output
    assert "m4" in result.output
    assert call_counts[bridge_log] >= 3
    assert call_counts[mcp_log] >= 3


def test_logs_no_follow_bridge_logfile_missing_still_prints_both_headers(monkeypatch, tmp_path):
    bridge_log = tmp_path / "does-not-exist-bridge.log"
    mcp_log = tmp_path / "mcp.log"
    monkeypatch.setattr(main.config, "bridge_log", lambda: bridge_log)
    monkeypatch.setattr(main.config, "mcp_log", lambda: mcp_log)

    def fake_tail_lines(logfile, n):
        return [] if logfile == bridge_log else ["mcp line\n"]

    monkeypatch.setattr(daemon, "tail_lines", fake_tail_lines)

    result = runner.invoke(main.app, ["logs"])

    assert result.exit_code == 0
    assert "── bridge ──" in result.output
    assert "── mcp ──" in result.output
    assert "mcp line" in result.output


def test_logs_no_follow_mcp_logfile_missing_still_prints_both_headers(monkeypatch, tmp_path):
    bridge_log = tmp_path / "bridge.log"
    mcp_log = tmp_path / "does-not-exist-mcp.log"
    monkeypatch.setattr(main.config, "bridge_log", lambda: bridge_log)
    monkeypatch.setattr(main.config, "mcp_log", lambda: mcp_log)

    def fake_tail_lines(logfile, n):
        return ["bridge line\n"] if logfile == bridge_log else []

    monkeypatch.setattr(daemon, "tail_lines", fake_tail_lines)

    result = runner.invoke(main.app, ["logs"])

    assert result.exit_code == 0
    assert "── bridge ──" in result.output
    assert "── mcp ──" in result.output
    assert "bridge line" in result.output


# --- agent: no subcommand, --help, unknown subcommand ---


def test_agent_no_subcommand_shows_help_and_exits_nonzero():
    """`agent_app` has no_args_is_help=True, but a Typer group with a
    required COMMAND argument and no subcommand given is a click usage
    error (exit code 2), not a plain help-and-exit-0."""
    result = runner.invoke(main.app, ["agent"])

    assert result.exit_code == 2
    flat = _flat(result.output)
    assert "Usage: wa agent" in flat
    assert "schema" in flat
    assert "guide" in flat


def test_agent_help_flag_exits_zero():
    result = runner.invoke(main.app, ["agent", "--help"])

    assert result.exit_code == 0
    flat = _flat(result.output)
    assert "schema" in flat
    assert "guide" in flat


def test_agent_unknown_subcommand_is_usage_error():
    result = runner.invoke(main.app, ["agent", "bogus-subcommand"])

    assert result.exit_code == 2
    flat = _flat(result.output)
    assert "Usage: wa agent" in flat
    assert "No such command 'bogus-subcommand'" in flat


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["agent", "schema", "extra-arg"], id="agent-schema-extra-arg"),
        pytest.param(["agent", "guide", "extra-arg"], id="agent-guide-extra-arg"),
        pytest.param(["agent", "schema", "--bogus"], id="agent-schema-bogus-flag"),
        pytest.param(["agent", "guide", "--bogus"], id="agent-guide-bogus-flag"),
        pytest.param(["send", "-x", "r", "m"], id="send-unknown-short-flag"),
        pytest.param(["logs", "-x"], id="logs-unknown-short-flag"),
        pytest.param(["--bogus-top-level-flag"], id="top-level-unknown-flag"),
    ],
)
def test_more_unknown_args_or_flags_is_usage_error(args):
    result = runner.invoke(main.app, args)

    assert result.exit_code == 2
    assert "Usage:" in _flat(result.output)


# --- download: positional args are forwarded verbatim, including edge text ---


@pytest.mark.parametrize(
    "message_id, chat_jid",
    [
        pytest.param("msg-123", "14157863858@s.whatsapp.net", id="normal"),
        pytest.param("", "14157863858@s.whatsapp.net", id="empty-message-id"),
        pytest.param("msg-😀-123", "120363319322076067@g.us", id="emoji-in-message-id"),
    ],
)
def test_download_forwards_args_verbatim(monkeypatch, message_id, chat_jid):
    calls = []

    def fake_download(m, c, base_url):
        calls.append((m, c))
        return True, "file.bin", "/tmp/file.bin"

    monkeypatch.setattr(main.api, "download_media", fake_download)

    result = runner.invoke(main.app, ["download", message_id, chat_jid])

    assert result.exit_code == 0
    assert calls == [(message_id, chat_jid)]


# --- contacts: unicode query never crashes, even with zero matches ---


def test_contacts_emoji_query_no_matches_exits_zero(monkeypatch, sample_dbs):
    monkeypatch.setattr(main.config, "contacts_db", lambda: sample_dbs.whatsapp_db)

    result = runner.invoke(main.app, ["contacts", "😀🎉-no-such-contact"])

    assert result.exit_code == 0
    assert "0 match(es)" in result.output


# --- doctor: corrupt messages.db (not just contacts) fails cleanly ---


def test_doctor_corrupt_messages_db_fails_cleanly(monkeypatch, tmp_path, sample_dbs):
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)
    corrupt = tmp_path / "corrupt-messages.db"
    _write_corrupt_db(corrupt)
    monkeypatch.setattr(main.config, "messages_db", lambda: corrupt)

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


# --- logs: additional follow/no-follow edge cases ---


def test_logs_follow_large_single_batch_all_lines_rendered(monkeypatch, tmp_path):
    bridge_log = tmp_path / "bridge.log"
    mcp_log = tmp_path / "mcp.log"
    monkeypatch.setattr(main.config, "bridge_log", lambda: bridge_log)
    monkeypatch.setattr(main.config, "mcp_log", lambda: mcp_log)
    monkeypatch.setattr(daemon, "tail_lines", lambda logfile, n: [])
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    big_batch = [f"line-{i}\n" for i in range(50)]
    call_counts = {bridge_log: 0, mcp_log: 0}

    def fake_follow_step(logfile, offset):
        call_counts[logfile] += 1
        if call_counts[logfile] > 1:
            raise KeyboardInterrupt
        return (big_batch if logfile == bridge_log else [], offset + 1)

    monkeypatch.setattr(daemon, "follow_step", fake_follow_step)

    result = runner.invoke(main.app, ["logs", "-f"])

    assert result.exit_code == 0
    assert "line-0" in result.output
    assert "line-49" in result.output


def test_logs_no_follow_both_logfiles_missing_still_prints_both_headers(monkeypatch, tmp_path):
    bridge_log = tmp_path / "does-not-exist-bridge.log"
    mcp_log = tmp_path / "does-not-exist-mcp.log"
    monkeypatch.setattr(main.config, "bridge_log", lambda: bridge_log)
    monkeypatch.setattr(main.config, "mcp_log", lambda: mcp_log)
    monkeypatch.setattr(daemon, "tail_lines", lambda logfile, n: [])

    result = runner.invoke(main.app, ["logs"])

    assert result.exit_code == 0
    assert "── bridge ──" in result.output
    assert "── mcp ──" in result.output


def test_logs_follow_zero_iterations_before_interrupt(monkeypatch, tmp_path):
    """KeyboardInterrupt on the very first follow_step call — the loop must
    exit cleanly with no lines ever printed, not propagate the interrupt."""
    bridge_log = tmp_path / "bridge.log"
    mcp_log = tmp_path / "mcp.log"
    monkeypatch.setattr(main.config, "bridge_log", lambda: bridge_log)
    monkeypatch.setattr(main.config, "mcp_log", lambda: mcp_log)
    monkeypatch.setattr(daemon, "tail_lines", lambda logfile, n: [])
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    def fake_follow_step(logfile, offset):
        raise KeyboardInterrupt

    monkeypatch.setattr(daemon, "follow_step", fake_follow_step)

    result = runner.invoke(main.app, ["logs", "-f"])

    assert result.exit_code == 0


# --- property-based: send/chats pass arbitrary values through unmangled ---


@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.function_scoped_fixture],
)
@given(message=st.text(min_size=1, max_size=500).filter(lambda s: not s.startswith("-")))
def test_send_arbitrary_message_property_passes_through_unmangled(monkeypatch, tmp_path, message):
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = []

    def fake_send(recipient, msg, *, base_url):
        calls.append((recipient, msg))
        return True, "sent ok"

    monkeypatch.setattr(api, "send_message", fake_send)

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", message])

    assert result.exit_code == 0
    assert calls == [("14157863858@s.whatsapp.net", message)]


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(limit=st.integers(min_value=-1000, max_value=1000))
def test_chats_limit_property_always_passed_through_exactly(monkeypatch, tmp_path, limit):
    existing_db = tmp_path / "messages.db"
    existing_db.write_text("")
    monkeypatch.setattr(main.config, "messages_db", lambda: existing_db)

    captured = {}

    def fake_list_chats(db_path, limit=20):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(db, "list_chats", fake_list_chats)

    result = runner.invoke(main.app, ["chats", "--limit", str(limit)])

    assert result.exit_code == 0
    assert captured["limit"] == limit


# --- agent schema/guide: main.py's wiring to wa_cli.agent, not agent's own logic ---


def test_agent_schema_prints_valid_json_with_all_commands():
    result = runner.invoke(main.app, ["agent", "schema"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, dict)
    command_names = {c["name"] for c in data["commands"]}
    assert command_names >= EXPECTED_COMMANDS


def test_agent_guide_prints_exact_build_guide_output_no_extra_newline():
    """main.py calls typer.echo(agent.build_guide(), nl=False) — confirm the
    CLI wiring doesn't add or drop a trailing newline vs. the raw string."""
    result = runner.invoke(main.app, ["agent", "guide"])

    assert result.exit_code == 0
    assert result.output == agent.build_guide()


def test_agent_schema_ignores_no_options_besides_help():
    result = runner.invoke(main.app, ["agent", "schema", "-h"])

    assert result.exit_code == 0
    assert "Options" in result.output


# --- more --help / usage-error rows (real gaps in the tables above) ---


@pytest.mark.parametrize(
    "args, expected_message",
    [
        pytest.param(["send", "", ""], None, id="send-empty-strings-not-missing"),
        pytest.param(["contacts", "--limit", "5"], "No such option: --limit", id="contacts-has-no-limit-option"),
        pytest.param(["send", "r", "m", "--force", "extra"], "Got unexpected extra argument", id="send-extra-after-force"),
    ],
)
def test_more_argument_edge_cases(monkeypatch, tmp_path, args, expected_message):
    if expected_message is None:
        # `wa send "" ""` — empty strings are valid (present) values, not a
        # missing-argument usage error; the command proceeds to try to send.
        monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
        monkeypatch.setattr(api, "send_message", lambda r, m, *, base_url: (True, "sent ok"))
        result = runner.invoke(main.app, args)
        assert result.exit_code == 0
        return

    result = runner.invoke(main.app, args)
    assert result.exit_code == 2
    assert expected_message in _flat(result.output)


def test_doctor_bridge_binary_present_but_not_executable_fails(monkeypatch, tmp_path, sample_dbs):
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)
    non_exec = tmp_path / "whatsapp-bridge-not-exec"
    non_exec.write_text("#!/bin/sh\n")
    non_exec.chmod(0o644)
    monkeypatch.setattr(main.config, "bridge_binary", lambda: non_exec)

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code != 0


def test_send_message_with_only_whitespace_still_sends(monkeypatch, tmp_path):
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = []

    def fake_send(recipient, msg, *, base_url):
        calls.append((recipient, msg))
        return True, "sent ok"

    monkeypatch.setattr(api, "send_message", fake_send)

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", "   "])

    assert result.exit_code == 0
    assert calls == [("14157863858@s.whatsapp.net", "   ")]


def test_send_message_with_embedded_newlines_passes_through(monkeypatch, tmp_path):
    message = "line one\nline two\nline three"
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = []

    def fake_send(recipient, msg, *, base_url):
        calls.append((recipient, msg))
        return True, "sent ok"

    monkeypatch.setattr(api, "send_message", fake_send)

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", message])

    assert result.exit_code == 0
    assert calls == [("14157863858@s.whatsapp.net", message)]


def test_send_message_that_is_a_single_dash_sends_literally(monkeypatch, tmp_path):
    """A bare "-" is not recognized as an option marker by click (only
    "--foo"/"-f" style tokens are) — it reaches MESSAGE as literal text."""
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = []

    def fake_send(recipient, msg, *, base_url):
        calls.append((recipient, msg))
        return True, "sent ok"

    monkeypatch.setattr(api, "send_message", fake_send)

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", "-"])

    assert result.exit_code == 0
    assert calls == [("14157863858@s.whatsapp.net", "-")]


# --- short -h works identically to --help for every top-level command ---

_SHORT_HELP_CASES = [
    pytest.param(["up", "-h"], "Usage: wa up", id="up"),
    pytest.param(["down", "-h"], "Usage: wa down", id="down"),
    pytest.param(["status", "-h"], "Usage: wa status", id="status"),
    pytest.param(["logs", "-h"], "Usage: wa logs", id="logs"),
    pytest.param(["send", "-h"], "Usage: wa send", id="send"),
    pytest.param(["contacts", "-h"], "Usage: wa contacts", id="contacts"),
    pytest.param(["chats", "-h"], "Usage: wa chats", id="chats"),
    pytest.param(["download", "-h"], "Usage: wa download", id="download"),
    pytest.param(["doctor", "-h"], "Usage: wa doctor", id="doctor"),
    pytest.param(["-h"], "Usage: wa", id="top-level"),
]


@pytest.mark.parametrize("args, expected_usage", _SHORT_HELP_CASES)
def test_short_help_flag_matches_long_flag(args, expected_usage):
    result = runner.invoke(main.app, args)

    assert result.exit_code == 0
    assert expected_usage in _flat(result.output)


# --- contacts/chats: db path exists() but is a directory, not a file ---


def test_contacts_db_path_is_a_directory_fails_cleanly(monkeypatch, tmp_path):
    directory_db = tmp_path / "im-a-directory.db"
    directory_db.mkdir()
    monkeypatch.setattr(main.config, "contacts_db", lambda: directory_db)

    result = runner.invoke(main.app, ["contacts", "Alice"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "could not read" in _flat(result.output).lower()


def test_chats_db_path_is_a_directory_fails_cleanly(monkeypatch, tmp_path):
    directory_db = tmp_path / "im-a-directory.db"
    directory_db.mkdir()
    monkeypatch.setattr(main.config, "messages_db", lambda: directory_db)

    result = runner.invoke(main.app, ["chats"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "could not read" in _flat(result.output).lower()


# --- doctor: partial-failure combinations not covered above ---


def test_doctor_rest_unreachable_but_daemons_alive_fails(monkeypatch, tmp_path, sample_dbs):
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)
    statuses = daemon.Statuses(
        bridge=daemon.ServiceStatus(name="bridge", pid=1, alive=True, rest_reachable=False),
        mcp=daemon.ServiceStatus(name="mcp", pid=2, alive=True, rest_reachable=None),
    )
    monkeypatch.setattr(daemon, "statuses", lambda **kwargs: statuses)

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code != 0


def test_doctor_all_checks_pass_reports_all_checks_passed_message(monkeypatch, tmp_path, sample_dbs):
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code == 0
    assert "all checks passed" in result.output.lower()


def test_doctor_failure_reports_hint_text_for_failed_check(monkeypatch, tmp_path, sample_dbs):
    _patch_doctor_all_pass(monkeypatch, tmp_path, sample_dbs)
    monkeypatch.setattr(main.shutil, "which", lambda name: None)

    result = runner.invoke(main.app, ["doctor"])

    assert result.exit_code != 0
    assert "install uv" in _flat(result.output).lower()
