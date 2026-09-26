"""Tests for wa_cli.daemon — process management with injected fakes.

No real processes are ever spawned and no real signals are ever sent: every
test injects a fake `spawn` callable and/or fake `kill` callable. Fake-process
fixtures live in this file (not conftest.py) per the plan's ownership rules.
"""

from __future__ import annotations

import signal
import tempfile
import time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

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


def test_follow_step_resets_offset_when_log_truncated(tmp_path):
    logfile = tmp_path / "svc.log"
    logfile.write_text("line one\nline two\n")
    _, offset = daemon.follow_step(logfile, 0)

    logfile.write_text("fresh\n")  # externally truncated/rotated
    lines, new_offset = daemon.follow_step(logfile, offset)

    assert lines == ["fresh\n"]
    assert new_offset == len("fresh\n")


# =====================================================================
# Edge-case expansion below. No real subprocess is ever spawned and no
# real OS signal is ever sent — every test here injects a fake `spawn`
# and/or `kill` exactly like the fakes above, or a plain closure that
# follows the same contract.
# =====================================================================


# --- pid_of() edge cases ---


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", None),
        ("   ", None),
        ("\n", None),
        ("\t\n", None),
        ("007", 7),
        ("0", 0),
        ("-1", -1),
        ("-42", -42),
        ("+7", 7),
        ("99999999999999", 99999999999999),
        ("123.0", None),
        ("1e3", None),
        ("42j", None),
        ("12a3", None),
        ("abc", None),
        ("  42  ", 42),
        ("42\n", 42),
        ("123\n456", None),  # multi-line: strip() only trims outer whitespace
        ("123\n456\n", None),
        ("١٢٣", 123),  # Arabic-Indic digits — int() accepts them
        ("0x1A", None),
    ],
)
def test_pid_of_string_edge_cases(pidfile, raw, expected):
    pidfile.write_text(raw)
    assert daemon.pid_of(pidfile) == expected


def test_pid_of_missing_parent_dir_returns_none(tmp_path):
    ghost = tmp_path / "nope" / "svc.pid"  # parent doesn't even exist
    assert daemon.pid_of(ghost) is None


@given(pid=st.integers())
@settings(max_examples=50, deadline=None)
def test_pid_of_roundtrips_any_integer(pid):
    with tempfile.TemporaryDirectory() as d:
        pidfile = Path(d) / "pid.pid"
        pidfile.write_text(str(pid))
        assert daemon.pid_of(pidfile) == pid


@given(raw=st.text(min_size=0, max_size=50))
@settings(max_examples=50, deadline=None)
def test_pid_of_never_raises_on_arbitrary_text(raw):
    with tempfile.TemporaryDirectory() as d:
        pidfile = Path(d) / "pid.pid"
        pidfile.write_text(raw)
        result = daemon.pid_of(pidfile)
        assert result is None or isinstance(result, int)


# --- is_alive() edge cases ---


def test_is_alive_permission_error_treated_as_dead(pidfile):
    pidfile.write_text("55")

    def kill(pid, sig):
        raise PermissionError("no permission to signal")  # OSError subclass

    assert daemon.is_alive(pidfile, kill=kill) is False
    assert not pidfile.exists()


@pytest.mark.parametrize(
    "garbage",
    ["", "   ", "\n", "12.5", "abc", "1 2 3", "١٢٣abc", "None", "null"],
)
def test_is_alive_malformed_pidfile_never_raises(pidfile, garbage):
    pidfile.write_text(garbage)
    assert daemon.is_alive(pidfile, kill=FakeKill()) is False


def test_is_alive_missing_pidfile_never_calls_kill(pidfile):
    kill = FakeKill()
    daemon.is_alive(pidfile, kill=kill)
    assert kill.calls == []


def test_is_alive_propagates_unexpected_exception_type(pidfile):
    pidfile.write_text("901")

    def kill(pid, sig):
        raise TypeError("not a signal-related error")

    with pytest.raises(TypeError):
        daemon.is_alive(pidfile, kill=kill)


@given(raw=st.text(min_size=0, max_size=50))
@settings(max_examples=30, deadline=None)
def test_is_alive_never_raises_on_arbitrary_pidfile_content(raw):
    with tempfile.TemporaryDirectory() as d:
        pidfile = Path(d) / "pid.pid"
        pidfile.write_text(raw)
        result = daemon.is_alive(pidfile, kill=FakeKill())
        assert isinstance(result, bool)


# --- start() edge cases ---


def test_start_creates_deeply_nested_logfile_parent_dirs(tmp_path):
    pidfile = tmp_path / "svc.pid"
    logfile = tmp_path / "deep" / "nested" / "dir" / "svc.log"
    spawn = FakeSpawn(pid=111)
    kill = FakeKill(alive_pids=set())

    daemon.start("svc", ["./bin"], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)

    assert logfile.parent.is_dir()
    assert pidfile.exists()


def test_start_creates_deeply_nested_pidfile_parent_dirs(tmp_path):
    pidfile = tmp_path / "deep" / "pid" / "dir" / "svc.pid"
    logfile = tmp_path / "svc.log"
    spawn = FakeSpawn(pid=112)
    kill = FakeKill(alive_pids=set())

    daemon.start("svc", ["./bin"], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)

    assert pidfile.parent.is_dir()
    assert pidfile.read_text().strip() == "112"


def test_start_argv_with_unicode_and_long_args(tmp_path, pidfile, logfile):
    spawn = FakeSpawn(pid=113)
    kill = FakeKill(alive_pids=set())
    long_arg = "x" * 10_000
    argv = ["./bin", "--name", "héllo wörld 你好", long_arg]

    daemon.start("svc", argv, cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)

    assert spawn.calls[0]["argv"] == argv


def test_start_with_empty_argv(tmp_path, pidfile, logfile):
    spawn = FakeSpawn(pid=114)
    kill = FakeKill(alive_pids=set())

    daemon.start("svc", [], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)

    assert spawn.calls[0]["argv"] == []
    assert pidfile.read_text().strip() == "114"


def test_start_stop_start_cycle_no_stale_state(tmp_path, pidfile, logfile):
    spawn = FakeSpawn(pid=201)
    kill = FakeKill(alive_pids={201}, dies_after=1)

    r1 = daemon.start("svc", ["./bin"], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)
    assert r1.already_running is False
    assert pidfile.read_text().strip() == "201"

    daemon.stop(pidfile, kill=kill)
    assert not pidfile.exists()

    spawn2 = FakeSpawn(pid=202)
    kill2 = FakeKill(alive_pids=set())  # nothing alive now
    r2 = daemon.start("svc", ["./bin"], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn2, kill=kill2)
    assert r2.already_running is False
    assert pidfile.read_text().strip() == "202"


def test_start_overwrites_garbage_pidfile_content(tmp_path, pidfile, logfile):
    pidfile.write_text("not-a-pid-garbage!!\n")
    spawn = FakeSpawn(pid=303)
    kill = FakeKill(alive_pids=set())

    result = daemon.start("svc", ["./bin"], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)

    assert result.already_running is False
    assert pidfile.read_text().strip() == "303"


def test_start_appends_to_existing_logfile_not_truncate(tmp_path, pidfile, logfile):
    logfile.write_text("old log content\n")
    spawn = FakeSpawn(pid=115)
    kill = FakeKill(alive_pids=set())

    daemon.start("svc", ["./bin"], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)

    assert logfile.read_text().startswith("old log content\n")


def test_start_does_not_validate_cwd_existence(tmp_path, pidfile, logfile):
    spawn = FakeSpawn(pid=116)
    kill = FakeKill(alive_pids=set())
    nonexistent_cwd = tmp_path / "does" / "not" / "exist"

    daemon.start("svc", ["./bin"], cwd=nonexistent_cwd, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)

    assert spawn.calls[0]["cwd"] == nonexistent_cwd
    assert pidfile.read_text().strip() == "116"


def test_start_pidfile_content_has_trailing_newline(tmp_path, pidfile, logfile):
    spawn = FakeSpawn(pid=117)
    kill = FakeKill(alive_pids=set())

    daemon.start("svc", ["./bin"], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)

    assert pidfile.read_text() == "117\n"


def test_start_result_name_matches_input(tmp_path, pidfile, logfile):
    spawn = FakeSpawn(pid=118)
    kill = FakeKill(alive_pids=set())

    result = daemon.start(
        "my-service", ["./bin"], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill
    )

    assert result.name == "my-service"
    assert result.healthy is None  # start() never sets health itself


def test_start_handles_pid_zero_from_spawn(tmp_path, pidfile, logfile):
    spawn = FakeSpawn(pid=0)
    kill = FakeKill(alive_pids=set())

    result = daemon.start("svc", ["./bin"], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)

    assert result.pid == 0
    assert pidfile.read_text().strip() == "0"


def test_start_stdout_and_stderr_share_same_file_handle(tmp_path, pidfile, logfile):
    spawn = FakeSpawn(pid=119)
    kill = FakeKill(alive_pids=set())

    daemon.start("svc", ["./bin"], cwd=tmp_path, logfile=logfile, pidfile=pidfile, spawn=spawn, kill=kill)

    call = spawn.calls[0]
    assert call["stdout"] is call["stderr"]


# --- stop() edge cases ---


def test_stop_dies_between_probe_and_sigterm_send(pidfile):
    """Closes the coverage gap around the SIGTERM except-block: the process
    can die in the window between the initial liveness probe and the
    SIGTERM actually being delivered."""
    pidfile.write_text("500")
    call_count = {"n": 0}

    def kill(pid, sig):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return  # initial probe: still alive
        raise ProcessLookupError("died before the signal was delivered")

    daemon.stop(pidfile, kill=kill)

    assert call_count["n"] == 2
    assert not pidfile.exists()


def test_stop_no_sigterm_sent_when_already_dead_before_probe(pidfile):
    pidfile.write_text("400")
    kill = FakeKill(alive_pids=set())

    daemon.stop(pidfile, kill=kill)

    signals_sent = [sig for pid, sig in kill.calls if pid == 400]
    assert signal.SIGTERM not in signals_sent


def test_stop_dies_on_first_grace_loop_check(pidfile):
    pidfile.write_text("910")
    probe_calls = {"n": 0}

    def kill(pid, sig):
        if sig == 0:
            probe_calls["n"] += 1
            if probe_calls["n"] == 1:
                return  # pre-SIGTERM probe: alive
            raise ProcessLookupError()  # first grace-loop probe: already dead
        return  # SIGTERM accepted

    sleeps = []
    daemon.stop(pidfile, kill=kill, sleep=sleeps.append, grace_seconds=10.0, poll_interval=0.1)

    assert sleeps == []  # returned before ever sleeping
    assert not pidfile.exists()


def test_stop_dies_on_last_grace_loop_check(pidfile):
    """Boundary: dies on the LAST grace-loop probe, not the post-loop one —
    exercised via the loop's own early-return branch."""
    pidfile.write_text("911")
    probe_calls = {"n": 0}

    def kill(pid, sig):
        if sig == 0:
            probe_calls["n"] += 1
            # initial probe (1) + two grace-loop probes (2, 3) alive; dies on
            # the third grace-loop probe (4).
            if probe_calls["n"] >= 4:
                raise ProcessLookupError()
            return
        return

    sleeps = []
    daemon.stop(pidfile, kill=kill, sleep=sleeps.append, grace_seconds=0.03, poll_interval=0.01)

    assert len(sleeps) == 2  # died before a third sleep would happen
    assert not pidfile.exists()


def test_stop_dies_just_before_sigkill_probe_skips_sigkill(pidfile):
    """Boundary: survives the entire grace loop, then dies right at the
    final pre-SIGKILL probe — SIGKILL must never be sent."""
    pidfile.write_text("912")
    probe_calls = {"n": 0}

    def kill(pid, sig):
        if sig == 0:
            probe_calls["n"] += 1
            # initial(1) + two grace-loop probes(2, 3) alive; final pre-SIGKILL
            # probe(4) finds it dead.
            if probe_calls["n"] >= 4:
                raise ProcessLookupError()
            return
        if sig == signal.SIGKILL:
            pytest.fail("SIGKILL must not be sent when the final probe finds the process already dead")
        return  # SIGTERM accepted

    daemon.stop(pidfile, kill=kill, sleep=lambda s: None, grace_seconds=0.02, poll_interval=0.01)

    assert not pidfile.exists()


def test_stop_grace_seconds_zero_skips_loop_straight_to_escalation(pidfile):
    kill = FakeKill(alive_pids={920}, dies_after=None)  # never dies
    pidfile.write_text("920")

    sleeps = []
    daemon.stop(pidfile, kill=kill, sleep=sleeps.append, grace_seconds=0.0, poll_interval=0.01)

    assert sleeps == []  # loop body never executed
    signals_sent = [sig for pid, sig in kill.calls if pid == 920]
    assert signal.SIGKILL in signals_sent
    assert not pidfile.exists()


def test_stop_poll_interval_greater_than_grace_runs_loop_once(pidfile):
    pidfile.write_text("930")
    kill = FakeKill(alive_pids={930}, dies_after=None)  # never dies

    sleeps = []
    daemon.stop(pidfile, kill=kill, sleep=sleeps.append, grace_seconds=0.01, poll_interval=1.0)

    assert sleeps == [1.0]  # exactly one iteration despite poll_interval > grace_seconds
    signals_sent = [sig for pid, sig in kill.calls if pid == 930]
    assert signal.SIGKILL in signals_sent


def test_stop_poll_interval_equal_to_grace_seconds_runs_loop_once(pidfile):
    pidfile.write_text("950")
    kill = FakeKill(alive_pids={950}, dies_after=None)  # never dies

    sleeps = []
    daemon.stop(pidfile, kill=kill, sleep=sleeps.append, grace_seconds=0.05, poll_interval=0.05)

    assert sleeps == [0.05]


def test_stop_absorbs_exception_from_sigkill_call(pidfile):
    pidfile.write_text("940")

    def kill(pid, sig):
        if sig == 0:
            return  # always alive
        if sig == signal.SIGKILL:
            raise OSError("cannot signal")
        return  # SIGTERM accepted silently

    daemon.stop(pidfile, kill=kill, sleep=lambda s: None, grace_seconds=0.02, poll_interval=0.01)

    assert not pidfile.exists()  # must not raise, and must still clean up


def test_stop_propagates_unexpected_exception_type_from_sigterm(pidfile):
    pidfile.write_text("900")

    def kill(pid, sig):
        if sig == 0:
            return  # alive
        raise TypeError("not a signal-related error")

    with pytest.raises(TypeError):
        daemon.stop(pidfile, kill=kill)


def test_stop_malformed_pidfile_no_kill_calls(pidfile):
    pidfile.write_text("garbage-not-a-pid")
    kill = FakeKill()

    daemon.stop(pidfile, kill=kill)

    assert kill.calls == []
    assert not pidfile.exists()


@given(
    grace_seconds=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    poll_interval=st.floats(min_value=0.001, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=25, deadline=None)
def test_stop_always_escalates_when_process_never_dies(grace_seconds, poll_interval):
    with tempfile.TemporaryDirectory() as d:
        pidfile = Path(d) / "svc.pid"
        pidfile.write_text("1000")
        kill = FakeKill(alive_pids={1000}, dies_after=None)  # never dies

        daemon.stop(pidfile, kill=kill, sleep=lambda s: None, grace_seconds=grace_seconds, poll_interval=poll_interval)

        assert not pidfile.exists()
        signals_sent = [sig for pid, sig in kill.calls if pid == 1000]
        assert signal.SIGKILL in signals_sent


@given(
    grace_seconds=st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False),
    poll_interval=st.floats(min_value=0.001, max_value=0.5, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=25, deadline=None)
def test_stop_process_dying_from_sigterm_never_gets_sigkill(grace_seconds, poll_interval):
    with tempfile.TemporaryDirectory() as d:
        pidfile = Path(d) / "svc.pid"
        pidfile.write_text("7000")
        # Dies after exactly one non-zero signal (the SIGTERM) is delivered.
        kill = FakeKill(alive_pids={7000}, dies_after=1)

        daemon.stop(pidfile, kill=kill, sleep=lambda s: None, grace_seconds=grace_seconds, poll_interval=poll_interval)

        signals_sent = [sig for pid, sig in kill.calls if pid == 7000]
        assert signal.SIGKILL not in signals_sent
        assert not pidfile.exists()


# --- follow_step() edge cases ---


def test_follow_step_exact_size_equal_offset_does_not_reset(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("line1\nline2\n")
    offset = log.stat().st_size  # exactly at EOF, not beyond it

    new_lines, new_offset = daemon.follow_step(log, offset)

    assert new_lines == []
    assert new_offset == offset  # NOT reset to 0 — the check is `<`, not `<=`


def test_follow_step_negative_offset_raises(tmp_path):
    """Documents current behavior: a negative offset is an invalid caller
    state (offsets are always produced internally as non-negative), not
    external input, so it is intentionally left unguarded."""
    log = tmp_path / "svc.log"
    log.write_text("line1\n")

    with pytest.raises(ValueError):
        daemon.follow_step(log, -1)


def test_follow_step_last_line_no_trailing_newline(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("line1\nline2")  # no trailing newline

    lines, offset = daemon.follow_step(log, 0)

    assert lines == ["line1\n", "line2"]
    assert offset == log.stat().st_size


def test_follow_step_partial_line_no_newline_at_all(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("incomplete line without newline")

    lines, _ = daemon.follow_step(log, 0)

    assert lines == ["incomplete line without newline"]


def test_follow_step_tolerates_invalid_utf8_byte(tmp_path):
    """Real robustness gap found and fixed: a daemon log is written by an
    external process and can contain a stray non-UTF-8 byte; `wa logs -f`
    must not crash on it. Fixed with open(..., errors="replace")."""
    log = tmp_path / "svc.log"
    raw = b"line1\n\xffline2\n"
    log.write_bytes(raw)

    lines, offset = daemon.follow_step(log, 0)

    assert lines[0] == "line1\n"
    assert "line2" in lines[1]  # replacement char + "line2\n", no crash
    assert offset == len(raw)


def test_follow_step_empty_file_zero_offset(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("")

    lines, offset = daemon.follow_step(log, 0)

    assert lines == []
    assert offset == 0


def test_follow_step_resets_when_truncated_to_empty(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("line1\nline2\nline3\n")
    offset = log.stat().st_size

    log.write_text("")  # truncated to empty externally

    lines, new_offset = daemon.follow_step(log, offset)

    assert lines == []
    assert new_offset == 0


def test_follow_step_raises_for_directory_path(tmp_path):
    d = tmp_path / "not_a_file"
    d.mkdir()

    with pytest.raises(IsADirectoryError):
        daemon.follow_step(d, 0)


def test_follow_step_multiple_sequential_appends(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("a\n")

    lines1, offset1 = daemon.follow_step(log, 0)
    assert lines1 == ["a\n"]

    with open(log, "a") as fh:
        fh.write("b\n")
    lines2, offset2 = daemon.follow_step(log, offset1)
    assert lines2 == ["b\n"]

    with open(log, "a") as fh:
        fh.write("c\nd\n")
    lines3, offset3 = daemon.follow_step(log, offset2)
    assert lines3 == ["c\n", "d\n"]
    assert offset3 == log.stat().st_size


# --- tail_lines() edge cases ---


def test_tail_lines_n_zero_returns_empty(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("a\nb\nc\n")
    assert daemon.tail_lines(log, 0) == []


def test_tail_lines_negative_n_returns_empty(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("a\nb\nc\n")
    assert daemon.tail_lines(log, -3) == []


def test_tail_lines_n_greater_than_total_returns_all(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("a\nb\nc\n")
    assert daemon.tail_lines(log, 100) == ["a\n", "b\n", "c\n"]


def test_tail_lines_n_equal_to_total_returns_all(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("a\nb\nc\n")
    assert daemon.tail_lines(log, 3) == ["a\n", "b\n", "c\n"]


def test_tail_lines_empty_file_returns_empty(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("")
    assert daemon.tail_lines(log, 5) == []


def test_tail_lines_tolerates_invalid_utf8_byte(tmp_path):
    log = tmp_path / "svc.log"
    log.write_bytes(b"a\n\xffb\n")
    lines = daemon.tail_lines(log, 2)
    assert len(lines) == 2


def test_tail_lines_raises_for_directory_path(tmp_path):
    d = tmp_path / "not_a_file"
    d.mkdir()

    with pytest.raises(IsADirectoryError):
        daemon.tail_lines(d, 5)


def test_tail_lines_n_one_no_trailing_newline(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("only line no newline")
    assert daemon.tail_lines(log, 1) == ["only line no newline"]


def test_tail_lines_bool_n_treated_as_int(tmp_path):
    """bool is an int subclass in Python: True acts like 1, False like 0."""
    log = tmp_path / "svc.log"
    log.write_text("a\nb\nc\n")
    assert daemon.tail_lines(log, True) == ["c\n"]
    assert daemon.tail_lines(log, False) == []  # falls into the `else []` branch


def test_tail_lines_counts_trailing_blank_line(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("a\nb\n\n")
    assert daemon.tail_lines(log, 2) == ["b\n", "\n"]


# --- up_all() / down_all() / statuses() edge cases ---


def test_up_all_bridge_healthy_on_first_poll_no_extra_sleep(state, monkeypatch):
    spawn = FakeSpawn(pid=600)
    kill = FakeKill(alive_pids=set())
    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: True)

    sleeps = []
    result = daemon.up_all(spawn=spawn, kill=kill, sleep=sleeps.append)

    assert result.bridge.healthy is True
    assert sleeps == []


def test_up_all_health_poll_interval_zero_terminates(state, monkeypatch):
    spawn = FakeSpawn(pid=200)
    kill = FakeKill(alive_pids=set())
    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: False)

    clock = {"t": 0.0}

    def fake_monotonic():
        clock["t"] += 0.001  # real time.monotonic() always ticks forward
        return clock["t"]

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) > 1000:
            pytest.fail("up_all health loop did not terminate for poll_interval=0")

    result = daemon.up_all(
        spawn=spawn, kill=kill, sleep=fake_sleep, monotonic=fake_monotonic, health_timeout=0.05, poll_interval=0
    )

    assert result.bridge.healthy is False
    assert all(s == 0 for s in sleep_calls)
    assert len(sleep_calls) < 1000


def test_up_all_bridge_already_running_mcp_not(state, monkeypatch):
    state["bridge_pidfile"].write_text("77")
    spawn = FakeSpawn(pid=500)
    kill = FakeKill(alive_pids={77})
    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: True)

    result = daemon.up_all(spawn=spawn, kill=kill, sleep=lambda s: None)

    assert result.bridge.already_running is True
    assert result.mcp.already_running is False
    assert len(spawn.calls) == 1  # only mcp spawned
    assert spawn.calls[0]["argv"][0] == "uv"


def test_up_all_mcp_already_running_bridge_not(state, monkeypatch):
    state["mcp_pidfile"].write_text("88")
    spawn = FakeSpawn(pid=501)
    kill = FakeKill(alive_pids={88})
    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: True)

    result = daemon.up_all(spawn=spawn, kill=kill, sleep=lambda s: None)

    assert result.mcp.already_running is True
    assert result.bridge.already_running is False
    assert len(spawn.calls) == 1  # only bridge spawned


def test_up_all_spawns_bridge_and_mcp_with_correct_cwd(state, monkeypatch):
    spawn = FakeSpawn(pid=700)
    kill = FakeKill(alive_pids=set())
    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: True)

    daemon.up_all(spawn=spawn, kill=kill, sleep=lambda s: None)

    bridge_call, mcp_call = spawn.calls
    assert bridge_call["cwd"] == state["repo_root"] / "whatsapp-bridge"
    assert mcp_call["cwd"] == state["repo_root"]


def test_statuses_reports_stale_mcp_pidfile_as_dead(state, monkeypatch):
    state["mcp_pidfile"].write_text("9999")
    kill = FakeKill(alive_pids=set())  # 9999 is not alive
    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: False)

    result = daemon.statuses(kill=kill)

    assert result.mcp.alive is False
    assert result.mcp.pid is None
    assert not state["mcp_pidfile"].exists()  # cleaned up as a side effect of is_alive


def test_statuses_mixed_bridge_up_mcp_down(state, monkeypatch):
    state["bridge_pidfile"].write_text("81")
    kill = FakeKill(alive_pids={81})
    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: True)

    result = daemon.statuses(kill=kill)

    assert result.bridge.alive is True
    assert result.mcp.alive is False
    assert result.mcp.pid is None


def test_statuses_mixed_mcp_up_bridge_down(state, monkeypatch):
    state["mcp_pidfile"].write_text("82")
    kill = FakeKill(alive_pids={82})
    monkeypatch.setattr(daemon.api, "bridge_alive", lambda base_url, timeout=1.0: False)

    result = daemon.statuses(kill=kill)

    assert result.mcp.alive is True
    assert result.mcp.pid == 82
    assert result.bridge.alive is False
    assert result.bridge.rest_reachable is False


def test_statuses_calls_bridge_alive_even_when_bridge_pidfile_missing(state, monkeypatch):
    called = {"n": 0}

    def fake_bridge_alive(base_url, timeout=1.0):
        called["n"] += 1
        return False

    monkeypatch.setattr(daemon.api, "bridge_alive", fake_bridge_alive)

    daemon.statuses(kill=FakeKill())

    assert called["n"] == 1


def test_down_all_tolerates_both_already_dead(state):
    state["bridge_pidfile"].write_text("70")
    state["mcp_pidfile"].write_text("71")
    kill = FakeKill(alive_pids=set())

    daemon.down_all(kill=kill)  # must not raise

    assert not state["bridge_pidfile"].exists()
    assert not state["mcp_pidfile"].exists()


def test_down_all_tolerates_missing_pidfiles(state):
    daemon.down_all(kill=FakeKill())  # neither pidfile exists — must not raise


def test_down_all_mixed_one_escalates_one_terminates_cleanly(state):
    state["bridge_pidfile"].write_text("60")
    state["mcp_pidfile"].write_text("61")
    alive = {60, 61}
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        if sig == 0:
            if pid not in alive:
                raise ProcessLookupError()
            return
        if pid == 60:
            alive.discard(60)  # bridge dies cleanly from SIGTERM
        # mcp (61) never dies from a signal -> escalates to SIGKILL
        return

    daemon.down_all(kill=kill, sleep=lambda s: None)

    assert not state["bridge_pidfile"].exists()
    assert not state["mcp_pidfile"].exists()
    assert (61, signal.SIGKILL) in calls
    assert (60, signal.SIGKILL) not in calls
