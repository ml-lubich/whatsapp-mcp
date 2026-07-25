"""Agent discovery for `wa` CLI."""
from __future__ import annotations

from typing import Any


def build_schema() -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "tool": "wa",
        "mcp": "wa-mcp",
        "help": "wa -h | wa <cmd> -h | wa agent schema",
        "commands": [
            {"name": "up", "help": "Start bridge + MCP daemons", "params": []},
            {"name": "down", "help": "Stop daemons", "params": []},
            {"name": "status", "help": "Daemon status", "params": []},
            {
                "name": "logs",
                "help": "Tail daemon logs",
                "params": [
                    {"name": "follow", "type": "bool", "required": False, "flags": ["-f", "--follow"]}
                ],
            },
            {
                "name": "send",
                "help": (
                    "Send WhatsApp message. Review recent_context/printed thread before "
                    "composing; never resend a message already in the thread or re-answer "
                    "something already replied to. Near-duplicate outbound messages are "
                    "blocked unless force=true."
                ),
                "params": [
                    {"name": "recipient", "type": "str", "required": True, "flags": []},
                    {"name": "message", "type": "str", "required": True, "flags": []},
                    {"name": "force", "type": "bool", "required": False, "flags": ["--force"]},
                ],
            },
            {
                "name": "contacts",
                "help": "Search contacts (positional query)",
                "params": [{"name": "query", "type": "str", "required": True, "flags": []}],
            },
            {
                "name": "chats",
                "help": "List recent chats",
                "params": [
                    {"name": "limit", "type": "int", "required": False, "flags": ["--limit"]}
                ],
            },
            {"name": "doctor", "help": "Health report", "params": []},
            {"name": "agent schema", "help": "This JSON schema", "params": []},
            {"name": "agent guide", "help": "Markdown playbook", "params": []},
        ],
    }


def build_guide() -> str:
    return """# wa agent guide

```
wa -h
wa <command> -h
wa agent schema
wa doctor
wa up
wa send <jid|phone> "text"   # only when user asks
wa send <jid|phone> "text" --force   # override the duplicate guard
wa contacts <query>
```

Prefer CLI for simple ops; MCP (`wa-mcp`) for complex tool-calling.
No unsolicited digests.

`wa send` prints the recent thread before sending. Review recent_context
(MCP) / the printed thread (CLI) before composing: never resend a message
already in the thread or re-answer something the other party already
replied to. Near-duplicate outbound messages are blocked unless
force=true / --force.
"""
