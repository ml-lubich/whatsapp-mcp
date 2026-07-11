"""Filesystem paths: repo-root discovery, state-dir resolution, bridge/store paths.

State-dir contents (pidfiles/logs) always live outside the repo working tree
(under XDG_STATE_HOME or ~/.wa-cli), never inside the repo clone.
"""

from __future__ import annotations

import os
from pathlib import Path

BRIDGE_URL = "http://localhost:8080"


def repo_root() -> Path:
    """Resolve the whatsapp-mcp repo root.

    Honors the WA_REPO env override first; otherwise walks up from this
    file's location until a directory containing whatsapp-bridge/ is found.
    """
    override = os.environ.get("WA_REPO")
    if override:
        return Path(override)

    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "whatsapp-bridge").is_dir():
            return candidate

    raise RuntimeError(
        "could not locate repo root: no whatsapp-bridge/ directory found "
        "above wa_cli/config.py; set WA_REPO to override"
    )


def state_dir() -> Path:
    """Resolve (and create) the wa-cli runtime state directory.

    Uses ${XDG_STATE_HOME}/wa-cli if XDG_STATE_HOME is set, else ~/.wa-cli.
    Always outside the repo tree.
    """
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        path = Path(xdg_state_home) / "wa-cli"
    else:
        path = Path(os.environ["HOME"]) / ".wa-cli"

    path.mkdir(parents=True, exist_ok=True)
    return path


def bridge_pidfile() -> Path:
    return state_dir() / "bridge.pid"


def bridge_log() -> Path:
    return state_dir() / "bridge.log"


def mcp_pidfile() -> Path:
    return state_dir() / "mcp.pid"


def mcp_log() -> Path:
    return state_dir() / "mcp.log"


def bridge_binary() -> Path:
    return repo_root() / "whatsapp-bridge" / "whatsapp-bridge"


def contacts_db() -> Path:
    return repo_root() / "whatsapp-bridge" / "store" / "whatsapp.db"


def messages_db() -> Path:
    return repo_root() / "whatsapp-bridge" / "store" / "messages.db"


def bridge_url() -> str:
    return BRIDGE_URL
