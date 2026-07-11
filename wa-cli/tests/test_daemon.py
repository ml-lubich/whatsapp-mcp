"""Tests for wa_cli.daemon — process management with injected fakes.

No real processes are ever spawned and no real signals are ever sent: every
test injects a fake `spawn` callable and/or fake `kill` callable. Fake-process
fixtures live in this file (not conftest.py) per the plan's ownership rules.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from wa_cli import daemon


class FakeProcess:
    """Stand-in for the subprocess.Popen object daemon.start() receives."""

    def __init__(self, pid: int) -> None:
        self.pid = pid


class FakeSpawn:
    """Injectable replacement for subprocess.Popen — records calls, never spawns."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.calls: list[dict] = []

    def __call__(self, argv, *, cwd=None, stdout=None, stderr=None, start_new_session=None):
        self.calls.append(
            {
                "argv": argv,
                "cwd": cwd,
                "stdout": stdout,
                "stderr": stderr,
                "start_new_session": start_new_session,
            }
        )
        return FakeProcess(self.pid)


class FakeKill:
    """Injectable replacement for os.kill — records signals, never sends real ones.

    Simulates process lifetime: a pid "dies" either immediately (never alive)
    or after `dies_after` SIGTERM/SIGKILL signals sent to it.
    """

    def __init__(self, alive_pids: set[int] | None = None, dies_after: int | None = None):
        self.alive_pids = alive_pids if alive_pids is not None else set()
        self.dies_after = dies_after
        self.signal_counts: dict[int, int] = {}
        self.calls: list[tuple[int, int]] = []

    def __call__(self, pid: int, sig: int) -> None:
        self.calls.append((pid, sig))
        if pid not in self.alive_pids:
            raise ProcessLookupError(f"no such process: {pid}")

        if sig == 0:
            # Liveness probe — no side effect.
            return

        self.signal_counts[pid] = self.signal_counts.get(pid, 0) + 1
        if self.dies_after is not None and self.signal_counts[pid] >= self.dies_after:
            self.alive_pids.discard(pid)


@pytest.fixture
def pidfile(tmp_path: Path) -> Path:
    return tmp_path / "svc.pid"


@pytest.fixture
def logfile(tmp_path: Path) -> Path:
    return tmp_path / "svc.log"


# --- start() ---


def test_start_writes_pidfile_and_spawns_detached(tmp_path, pidfile, logfile):
    spawn = FakeSpawn(pid=1234)
    kill = FakeKill(alive_pids=set())  # not yet alive before start

    daemon.start(
        "bridge",
        ["./whatsapp-bridge"],
        cwd=tmp_path,
        logfile=logfile,
        pidfile=pidfile,
        spawn=spawn,
        kill=kill,
    )

    assert pidfile.read_text().strip() == "1234"
    assert len(spawn.calls) == 1
    call = spawn.calls[0]
    assert call["argv"] == ["./whatsapp-bridge"]
    assert call["cwd"] == tmp_path
    assert call["start_new_session"] is True


def test_start_redirects_stdout_stderr_to_logfile(tmp_path, pidfile, logfile):
    spawn = FakeSpawn(pid=99)
    kill = FakeKill(alive_pids=set())

    daemon.start(
        "mcp",
        ["uv", "run", "main.py"],
        cwd=tmp_path,
        logfile=logfile,
        pidfile=pidfile,
        spawn=spawn,
        kill=kill,
    )

    assert logfile.exists()
    call = spawn.calls[0]
    # stdout/stderr must be file handles pointed at the logfile (appended).
    assert call["stdout"] is not None
    assert call["stderr"] is not None


def test_start_idempotent_when_already_alive(tmp_path, pidfile, logfile):
    pidfile.write_text("555")
    spawn = FakeSpawn(pid=999)
    kill = FakeKill(alive_pids={555})  # existing pid is alive

    result = daemon.start(
        "bridge",
        ["./whatsapp-bridge"],
        cwd=tmp_path,
        logfile=logfile,
        pidfile=pidfile,
        spawn=spawn,
        kill=kill,
    )

    assert len(spawn.calls) == 0
    assert pidfile.read_text().strip() == "555"
    assert result.already_running is True


def test_start_respawns_when_pidfile_stale(tmp_path, pidfile, logfile):
    pidfile.write_text("555")
    spawn = FakeSpawn(pid=999)
    kill = FakeKill(alive_pids=set())  # 555 is dead

    result = daemon.start(
        "bridge",
        ["./whatsapp-bridge"],
        cwd=tmp_path,
        logfile=logfile,
        pidfile=pidfile,
        spawn=spawn,
        kill=kill,
    )

    assert len(spawn.calls) == 1
    assert pidfile.read_text().strip() == "999"
    assert result.already_running is False


# --- stop() ---


def test_stop_sends_sigterm_and_removes_pidfile(pidfile):
    import signal

    pidfile.write_text("111")
    kill = FakeKill(alive_pids={111}, dies_after=1)

    daemon.stop(pidfile, kill=kill)

    assert (111, signal.SIGTERM) in kill.calls
    assert not pidfile.exists()


def test_stop_escalates_to_sigkill_after_grace(pidfile):
    import signal

    pidfile.write_text("222")
    # Process ignores SIGTERM (never dies from it), only dies after enough
    # signals overall — force escalation path by never dying from SIGTERM.
    kill = FakeKill(alive_pids={222}, dies_after=None)

    sleeps: list[float] = []

    daemon.stop(
        pidfile,
        kill=kill,
        sleep=sleeps.append,
        grace_seconds=0.01,
        poll_interval=0.01,
    )

    signals_sent = [sig for pid, sig in kill.calls if pid == 222]
    assert signal.SIGTERM in signals_sent
    assert signal.SIGKILL in signals_sent
    assert not pidfile.exists()


def test_stop_tolerates_dead_pid(pidfile):
    pidfile.write_text("333")
    kill = FakeKill(alive_pids=set())  # already dead

    daemon.stop(pidfile, kill=kill)

    assert not pidfile.exists()


def test_stop_tolerates_missing_pidfile(pidfile):
    assert not pidfile.exists()
    kill = FakeKill()

    daemon.stop(pidfile, kill=kill)  # must not raise

    assert kill.calls == []


# --- is_alive() ---


def test_is_alive_true_for_running_pid(pidfile):
    pidfile.write_text("42")
    kill = FakeKill(alive_pids={42})

    assert daemon.is_alive(pidfile, kill=kill) is True


def test_is_alive_false_for_dead_pid(pidfile):
    pidfile.write_text("42")
    kill = FakeKill(alive_pids=set())

    assert daemon.is_alive(pidfile, kill=kill) is False


def test_is_alive_false_for_missing_pidfile(pidfile):
    assert not pidfile.exists()
    kill = FakeKill()

    assert daemon.is_alive(pidfile, kill=kill) is False


def test_is_alive_cleans_up_stale_pidfile(pidfile):
    pidfile.write_text("42")
    kill = FakeKill(alive_pids=set())

    daemon.is_alive(pidfile, kill=kill)

    assert not pidfile.exists()


def test_is_alive_handles_malformed_pidfile(pidfile):
    pidfile.write_text("not-a-pid\n")
    kill = FakeKill()

    assert daemon.is_alive(pidfile, kill=kill) is False


# --- read_pid() helper behavior via is_alive/status ---


def test_pid_of_returns_none_when_missing(pidfile):
    assert daemon.pid_of(pidfile) is None


def test_pid_of_returns_int_when_present(pidfile):
    pidfile.write_text("777\n")
    assert daemon.pid_of(pidfile) == 777


# --- up_all() / down_all() / statuses() ---


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Monkeypatch config paths to point at tmp_path so up_all/down_all/statuses
    never touch the real repo or ~/.wa-cli."""
    bridge_pidfile = tmp_path / "bridge.pid"
    bridge_log = tmp_path / "bridge.log"
    mcp_pidfile = tmp_path / "mcp.pid"
    mcp_log = tmp_path / "mcp.log"
    bridge_binary = tmp_path / "whatsapp-bridge"
    bridge_binary.write_text("#!/bin/sh\n")
    repo_root = tmp_path

    monkeypatch.setattr(daemon.config, "bridge_pidfile", lambda: bridge_pidfile)
    monkeypatch.setattr(daemon.config, "bridge_log", lambda: bridge_log)
    monkeypatch.setattr(daemon.config, "mcp_pidfile", lambda: mcp_pidfile)
    monkeypatch.setattr(daemon.config, "mcp_log", lambda: mcp_log)
    monkeypatch.setattr(daemon.config, "bridge_binary", lambda: bridge_binary)
    monkeypatch.setattr(daemon.config, "repo_root", lambda: repo_root)
    monkeypatch.setattr(daemon.config, "bridge_url", lambda: "http://localhost:8080")

    return {
        "bridge_pidfile": bridge_pidfile,
        "bridge_log": bridge_log,
        "mcp_pidfile": mcp_pidfile,
        "mcp_log": mcp_log,
        "bridge_binary": bridge_binary,
        "repo_root": repo_root,
    }


def test_up_all_spawns_both_and_health_gates_bridge(state, monkeypatch):
    spawn = FakeSpawn(pid=100)
    kill = FakeKill(alive_pids=set())

    calls = {"n": 0}

    def fake_bridge_alive(base_url, timeout=1.0):
        calls["n"] += 1
        return calls["n"] >= 2  # alive on the second poll

    monkeypatch.setattr(daemon.api, "bridge_alive", fake_bridge_alive)

    result = daemon.up_all(spawn=spawn, kill=kill, sleep=lambda s: None)

    assert len(spawn.calls) == 2
    assert result.bridge.healthy is True
    assert result.mcp.already_running is False


def test_up_all_reports_unhealthy_bridge_after_timeout(state, monkeypatch):
    spawn = FakeSpawn(pid=101)
    kill = FakeKill(alive_pids=set())

    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: False)

    fake_clock = {"t": 0.0}

    def fake_monotonic():
        return fake_clock["t"]

    def fake_sleep(seconds):
        fake_clock["t"] += seconds

    result = daemon.up_all(
        spawn=spawn,
        kill=kill,
        sleep=fake_sleep,
        monotonic=fake_monotonic,
        health_timeout=0.05,
        poll_interval=0.01,
    )

    assert result.bridge.healthy is False


def test_up_all_idempotent_no_double_spawn(state, monkeypatch):
    state["bridge_pidfile"].write_text("55")
    state["mcp_pidfile"].write_text("56")
    spawn = FakeSpawn(pid=999)
    kill = FakeKill(alive_pids={55, 56})

    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: True)

    result = daemon.up_all(spawn=spawn, kill=kill, sleep=lambda s: None)

    assert len(spawn.calls) == 0
    assert result.bridge.already_running is True
    assert result.mcp.already_running is True


def test_down_all_stops_both(state):
    import signal

    state["bridge_pidfile"].write_text("55")
    state["mcp_pidfile"].write_text("56")
    kill = FakeKill(alive_pids={55, 56}, dies_after=1)

    daemon.down_all(kill=kill)

    assert not state["bridge_pidfile"].exists()
    assert not state["mcp_pidfile"].exists()
    assert (55, signal.SIGTERM) in kill.calls
    assert (56, signal.SIGTERM) in kill.calls


def test_statuses_reports_bridge_and_mcp(state, monkeypatch):
    state["bridge_pidfile"].write_text("55")
    state["mcp_pidfile"].write_text("56")
    kill = FakeKill(alive_pids={55, 56})

    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: True)

    statuses = daemon.statuses(kill=kill)

    assert statuses.bridge.name == "bridge"
    assert statuses.bridge.pid == 55
    assert statuses.bridge.alive is True
    assert statuses.bridge.rest_reachable is True
    assert statuses.mcp.name == "mcp"
    assert statuses.mcp.pid == 56
    assert statuses.mcp.alive is True
    # MCP is pidfile-only — no network probe.
    assert statuses.mcp.rest_reachable is None


def test_statuses_reports_down_when_pidfiles_missing(state, monkeypatch):
    kill = FakeKill(alive_pids=set())
    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: False)

    statuses = daemon.statuses(kill=kill)

    assert statuses.bridge.alive is False
    assert statuses.bridge.pid is None
    assert statuses.mcp.alive is False
    assert statuses.mcp.pid is None


# --- logs following seam (single-iteration, unit-testable) ---


def test_follow_step_yields_new_lines_appended_since_last_offset(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("line1\nline2\n")

    offset = log.stat().st_size
    log.write_text(log.read_text() + "line3\n")

    new_lines, new_offset = daemon.follow_step(log, offset)

    assert new_lines == ["line3\n"]
    assert new_offset == log.stat().st_size


def test_follow_step_no_new_lines_returns_empty(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("line1\n")
    offset = log.stat().st_size

    new_lines, new_offset = daemon.follow_step(log, offset)

    assert new_lines == []
    assert new_offset == offset


def test_follow_step_missing_file_returns_empty(tmp_path):
    log = tmp_path / "does-not-exist.log"

    new_lines, new_offset = daemon.follow_step(log, 0)

    assert new_lines == []
    assert new_offset == 0


def test_tail_lines_returns_last_n(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("\n".join(f"line{i}" for i in range(1, 101)) + "\n")

    lines = daemon.tail_lines(log, 5)

    assert lines == [f"line{i}\n" for i in range(96, 101)] or lines == [
        f"line{i}" for i in range(96, 101)
    ]


def test_tail_lines_missing_file_returns_empty(tmp_path):
    log = tmp_path / "does-not-exist.log"

    assert daemon.tail_lines(log, 50) == []
