"""Background process management for the bridge and MCP daemons.

Both the detached-spawn call and the log-follow loop body are kept behind
small injectable seams (`spawn`, `kill`, `sleep`/`monotonic`, `follow_step`)
so the control flow is exercisable from unit tests without ever starting a
real process or sending a real signal.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from wa_cli import api, config

DEFAULT_GRACE_SECONDS = 5.0
DEFAULT_POLL_INTERVAL = 0.1
DEFAULT_HEALTH_TIMEOUT = 10.0

Kill = Callable[[int, int], None]
Sleep = Callable[[float], None]
Monotonic = Callable[[], float]


@dataclass
class StartResult:
    name: str
    pid: int | None
    already_running: bool
    healthy: bool | None = None  # None when no health probe applies (e.g. MCP)


@dataclass
class ServiceStatus:
    name: str
    pid: int | None
    alive: bool
    rest_reachable: bool | None  # None for services with no network probe (MCP)


@dataclass
class UpResult:
    bridge: StartResult
    mcp: StartResult


@dataclass
class Statuses:
    bridge: ServiceStatus
    mcp: ServiceStatus


def pid_of(pidfile: Path) -> int | None:
    """Read the pid from `pidfile`, or None if missing/unreadable."""
    try:
        raw = pidfile.read_text().strip()
    except FileNotFoundError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def is_alive(pidfile: Path, *, kill: Kill = os.kill) -> bool:
    """Check whether the pid in `pidfile` refers to a live process.

    Uses signal 0 (no-op) via the injectable `kill` callable. Cleans up a
    stale pidfile (pid recorded but process gone) as a side effect.
    """
    pid = pid_of(pidfile)
    if pid is None:
        return False

    try:
        kill(pid, 0)
    except (ProcessLookupError, OSError):
        pidfile.unlink(missing_ok=True)
        return False
    return True


def start(
    name: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    logfile: Path,
    pidfile: Path,
    spawn: Callable[..., object] = subprocess.Popen,
    kill: Kill = os.kill,
) -> StartResult:
    """Start `argv` as a detached background daemon, unless already running.

    stdout/stderr are appended to `logfile`; the child's pid is written to
    `pidfile`. Detached via start_new_session=True (new session, no
    controlling terminal). Idempotent: if `pidfile` names a live pid,
    nothing is spawned and already_running=True is reported.
    """
    if is_alive(pidfile, kill=kill):
        return StartResult(name=name, pid=pid_of(pidfile), already_running=True)

    logfile.parent.mkdir(parents=True, exist_ok=True)
    with open(logfile, "ab") as log_handle:
        process = spawn(
            list(argv),
            cwd=cwd,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
        )

    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(f"{process.pid}\n")
    return StartResult(name=name, pid=process.pid, already_running=False)


def stop(
    pidfile: Path,
    *,
    kill: Kill = os.kill,
    sleep: Sleep = time.sleep,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> None:
    """Stop the process named by `pidfile`: SIGTERM, grace wait, SIGKILL escalation.

    Tolerates a missing pidfile or an already-dead pid. Always removes the
    pidfile on return (if it existed).
    """
    pid = pid_of(pidfile)
    if pid is None:
        pidfile.unlink(missing_ok=True)
        return

    def _probe_alive() -> bool:
        try:
            kill(pid, 0)
        except (ProcessLookupError, OSError):
            return False
        return True

    if not _probe_alive():
        pidfile.unlink(missing_ok=True)
        return

    try:
        kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pidfile.unlink(missing_ok=True)
        return

    elapsed = 0.0
    while elapsed < grace_seconds:
        if not _probe_alive():
            pidfile.unlink(missing_ok=True)
            return
        sleep(poll_interval)
        elapsed += poll_interval

    if _probe_alive():
        try:
            kill(pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    pidfile.unlink(missing_ok=True)


def follow_step(logfile: Path, offset: int) -> tuple[list[str], int]:
    """Single-iteration read of any lines appended to `logfile` since `offset`.

    Returns (new_lines, new_offset). Missing file yields no lines and the
    offset unchanged. This is the injectable body of the `logs -f` loop —
    callers repeatedly invoke it (e.g. with a sleep between calls) to stream
    appended lines; kept as a single step so it is unit-testable without an
    actual blocking follow loop.
    """
    try:
        if logfile.stat().st_size < offset:
            # Log was truncated/rotated externally; restart from the top.
            offset = 0
        # errors="replace": a daemon log is written by an external process and
        # may contain a stray non-UTF-8 byte; `wa logs -f` must not crash on it.
        with open(logfile, "r", errors="replace") as fh:
            fh.seek(offset)
            new_lines = fh.readlines()
            new_offset = fh.tell()
    except FileNotFoundError:
        return [], offset
    return new_lines, new_offset


def tail_lines(logfile: Path, n: int) -> list[str]:
    """Return the last `n` lines of `logfile`, or [] if it doesn't exist."""
    try:
        lines = logfile.read_text(errors="replace").splitlines(keepends=True)
    except FileNotFoundError:
        return []
    return lines[-n:] if n > 0 else []


def up_all(
    *,
    spawn: Callable[..., object] = subprocess.Popen,
    kill: Kill = os.kill,
    sleep: Sleep = time.sleep,
    monotonic: Monotonic = time.monotonic,
    health_timeout: float = DEFAULT_HEALTH_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> UpResult:
    """Start the bridge and MCP daemons, health-gating the bridge afterward.

    The bridge is polled via api.bridge_alive for up to `health_timeout`
    seconds; the MCP daemon has no network probe (pidfile-only per spec §2.2).
    """
    bridge_result = start(
        "bridge",
        [str(config.bridge_binary())],
        cwd=config.repo_root() / "whatsapp-bridge",
        logfile=config.bridge_log(),
        pidfile=config.bridge_pidfile(),
        spawn=spawn,
        kill=kill,
    )

    mcp_result = start(
        "mcp",
        ["uv", "--directory", str(config.repo_root() / "whatsapp-mcp-server"), "run", "main.py"],
        cwd=config.repo_root(),
        logfile=config.mcp_log(),
        pidfile=config.mcp_pidfile(),
        spawn=spawn,
        kill=kill,
    )

    healthy = False
    deadline = monotonic() + health_timeout
    while True:
        if api.bridge_alive(config.bridge_url()):
            healthy = True
            break
        if monotonic() >= deadline:
            break
        sleep(poll_interval)

    bridge_result.healthy = healthy
    return UpResult(bridge=bridge_result, mcp=mcp_result)


def down_all(*, kill: Kill = os.kill, sleep: Sleep = time.sleep) -> None:
    """Stop both the bridge and MCP daemons."""
    stop(config.bridge_pidfile(), kill=kill, sleep=sleep)
    stop(config.mcp_pidfile(), kill=kill, sleep=sleep)


def statuses(*, kill: Kill = os.kill) -> Statuses:
    """Aggregate status for both daemons.

    Bridge status also probes REST reachability via api.bridge_alive; MCP
    status is pidfile-only (no network probe exists for it).
    """
    bridge_pidfile = config.bridge_pidfile()
    bridge_alive_flag = is_alive(bridge_pidfile, kill=kill)
    bridge_status = ServiceStatus(
        name="bridge",
        pid=pid_of(bridge_pidfile) if bridge_alive_flag else None,
        alive=bridge_alive_flag,
        rest_reachable=api.bridge_alive(config.bridge_url()),
    )

    mcp_pidfile = config.mcp_pidfile()
    mcp_alive_flag = is_alive(mcp_pidfile, kill=kill)
    mcp_status = ServiceStatus(
        name="mcp",
        pid=pid_of(mcp_pidfile) if mcp_alive_flag else None,
        alive=mcp_alive_flag,
        rest_reachable=None,
    )

    return Statuses(bridge=bridge_status, mcp=mcp_status)
