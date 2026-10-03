"""CLI wiring for `wa media ...` and `wa mcp ...` (fixture DBs, fake runner)."""
from __future__ import annotations

import json
import urllib.error

import pytest
from typer.testing import CliRunner

from test_media import GROUP, _desktop, _messages_db
from wa_cli import main, mcp_reg, media

runner = CliRunner()


@pytest.fixture
def wired(tmp_path, monkeypatch):
    store = tmp_path / "repo" / "whatsapp-bridge" / "store"
    store.mkdir(parents=True)
    _messages_db(
        store / "messages.db",
        [
            ("M1", GROUP, "2026-10-03 06:22:01-07:00", "image", "a.jpg", ""),
            ("TXT", GROUP, "2026-10-01 06:22:01-07:00", "", "", "hi"),
        ],
    )
    root = tmp_path / "desktop"
    monkeypatch.setenv("WA_REPO", str(tmp_path / "repo"))
    monkeypatch.setenv("WA_DESKTOP_ROOT", str(root))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    def down(url, payload, timeout):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(media, "_post_json", down)
    return tmp_path, root


def test_media_help_everywhere():
    for args in (["media", "-h"], ["media", "--help"], ["media", "get", "-h"], ["media", "latest", "-h"],
                 ["mcp", "-h"], ["mcp", "enable", "-h"]):
        assert runner.invoke(main.app, args).exit_code == 0


def test_get_json(wired):
    tmp, root = wired
    _desktop(root, [("M1", GROUP, "Media/g/u.jpg", b"jpeg")])
    out_dir = tmp / "my out"
    r = runner.invoke(main.app, ["media", "get", "M1", "--chat", GROUP, "-o", str(out_dir), "-j"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["source"] == "desktop" and data["size"] == 4
    assert data["path"].startswith(str(out_dir))


def test_get_human_prints_path_and_default_dir(wired):
    tmp, root = wired
    _desktop(root, [("M1", GROUP, "Media/g/u.jpg", b"jpeg")])
    r = runner.invoke(main.app, ["media", "get", "M1"])
    assert r.exit_code == 0
    assert str(tmp / "state" / "wa-cli" / "media") in r.output.replace("\n", "")


def test_get_error_exit_1_names_cause(wired):
    r = runner.invoke(main.app, ["media", "get", "NOPE"])
    assert r.exit_code == 1 and "not found" in r.output


def test_get_error_json(wired):
    r = runner.invoke(main.app, ["media", "get", "TXT", "-j"])
    assert r.exit_code == 1
    assert "not a media message" in json.loads(r.stdout)["error"]


def test_latest_json_mixed_results(wired):
    tmp, root = wired
    r = runner.invoke(main.app, ["media", "latest", "--chat", GROUP, "-n", "1", "-j", "-o", str(tmp / "o")])
    assert r.exit_code == 1  # nothing resolved
    assert "WhatsApp Desktop" in json.loads(r.stdout)[0]["error"]
    _desktop(root, [("M1", GROUP, "Media/g/u.jpg", b"x")])
    r = runner.invoke(main.app, ["media", "latest", "--name", "fam", "-j", "-o", str(tmp / "o")])
    assert r.exit_code == 0 and json.loads(r.stdout)[0]["message_id"] == "M1"


def test_latest_human(wired):
    tmp, root = wired
    _desktop(root, [("M1", GROUP, "Media/g/u.jpg", b"x")])
    ok = runner.invoke(main.app, ["media", "latest", "-o", str(tmp / "o")])
    assert ok.exit_code == 0 and "M1" in ok.output
    bad = runner.invoke(main.app, ["media", "latest", "--name", "zzz"])
    assert bad.exit_code == 1 and "no chat matches" in bad.output


def test_latest_human_failure_row(wired):
    tmp, _ = wired
    r = runner.invoke(main.app, ["media", "latest", "-o", str(tmp / "o")])
    assert r.exit_code == 1 and "M1" in r.output


# --- wa mcp enable|disable|status ---------------------------------------------

class FakeRunner:
    def __init__(self, rc=0, out=""):
        self.calls, self.rc, self.out = [], rc, out

    def __call__(self, cmd):
        self.calls.append(cmd)
        return self.rc, self.out


def test_enable_command(wired, monkeypatch):
    fake = FakeRunner()
    monkeypatch.setattr(mcp_reg, "run", fake)
    r = runner.invoke(main.app, ["mcp", "enable"])
    assert r.exit_code == 0
    repo = wired[0] / "repo"
    assert fake.calls == [[
        "claude", "mcp", "add", "--scope", "user", "whatsapp", "--",
        "uv", "--directory", str(repo / "whatsapp-mcp-server"), "run", "main.py",
    ]]


def test_disable_command(monkeypatch):
    fake = FakeRunner()
    monkeypatch.setattr(mcp_reg, "run", fake)
    assert runner.invoke(main.app, ["mcp", "disable"]).exit_code == 0
    assert fake.calls == [["claude", "mcp", "remove", "--scope", "user", "whatsapp"]]


def test_dry_run_runs_nothing_and_prints_command(wired, monkeypatch):
    fake = FakeRunner()
    monkeypatch.setattr(mcp_reg, "run", fake)
    r = runner.invoke(main.app, ["mcp", "enable", "-n"])
    assert r.exit_code == 0 and fake.calls == []
    assert "claude mcp add --scope user whatsapp" in r.output.replace("\n", " ")
    d = runner.invoke(main.app, ["mcp", "disable", "--dry-run"])
    assert "claude mcp remove --scope user whatsapp" in d.output.replace("\n", " ")


def test_status_registered_and_not(monkeypatch):
    monkeypatch.setattr(mcp_reg, "run", FakeRunner(0, "whatsapp: ..."))
    on = runner.invoke(main.app, ["mcp", "status"])
    assert on.exit_code == 0 and "enabled" in on.output
    monkeypatch.setattr(mcp_reg, "run", FakeRunner(1, "No MCP server found"))
    off = runner.invoke(main.app, ["mcp", "status"])
    assert off.exit_code == 0 and "disabled" in off.output


def test_failure_surfaces_exit_1(monkeypatch):
    monkeypatch.setattr(mcp_reg, "run", FakeRunner(1, "boom"))
    r = runner.invoke(main.app, ["mcp", "disable"])
    assert r.exit_code == 1 and "boom" in r.output


def test_real_run_missing_claude_binary(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    rc, out = mcp_reg.run(["claude", "mcp", "get", "whatsapp"])
    assert rc == 127 and "claude" in out


def test_real_run_captures_output():
    rc, out = mcp_reg.run(["python3", "-c", "print('hi')"]) if False else mcp_reg.run(
        [__import__("sys").executable, "-c", "print('hi')"]
    )
    assert rc == 0 and out.strip() == "hi"
