"""Integration tests for the wa_cli.main typer app via CliRunner.

Never spawns real processes, never hits the real (live) bridge, never reads
the real store DBs — every test monkeypatches the api/db/daemon module
functions that wa_cli.main calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wa_cli import api, daemon, db, main

runner = CliRunner()

EXPECTED_COMMANDS = {
    "up",
    "down",
    "status",
    "logs",
    "send",
    "contacts",
    "chats",
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
    monkeypatch.setattr(api, "send_message", lambda recipient, message, *, base_url: (True, "sent ok"))

    result = runner.invoke(main.app, ["send", "14157863858@s.whatsapp.net", "hello"])

    assert result.exit_code == 0
    assert "sent ok" in result.output


def test_send_failure_exits_nonzero(monkeypatch):
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
