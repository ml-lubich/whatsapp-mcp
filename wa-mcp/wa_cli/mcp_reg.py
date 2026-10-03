"""On-demand registration of the whatsapp MCP server with Claude Code.

The stdio MCP server is only useful while a client holds it, and Claude Code
spawns every registered server at session start. Register it only when
needed (`wa mcp enable`), drop it after (`wa mcp disable`). The Go bridge
stays a launchd service: it must run to record incoming messages.
"""

from __future__ import annotations

import subprocess

from wa_cli import config

NAME = "whatsapp"


def enable_cmd() -> list[str]:
    server_dir = str(config.repo_root() / "whatsapp-mcp-server")
    return ["claude", "mcp", "add", "--scope", "user", NAME, "--",
            "uv", "--directory", server_dir, "run", "main.py"]


def disable_cmd() -> list[str]:
    return ["claude", "mcp", "remove", "--scope", "user", NAME]


def status_cmd() -> list[str]:
    return ["claude", "mcp", "get", NAME]


def run(cmd: list[str]) -> tuple[int, str]:
    """Run `cmd`; returns (returncode, combined output). 127 if the binary is missing."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found on PATH"
    return proc.returncode, (proc.stdout + proc.stderr).strip()
