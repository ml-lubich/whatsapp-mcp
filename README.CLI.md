# wa — WhatsApp CLI + MCP (imsg pattern)

Same idea as [`imsg`](https://github.com/ml-lubich/imsg): **CLI first** (cheap for agents), **MCP when needed**.

| Surface | Command | Role |
|---------|---------|------|
| **CLI** | `wa` | doctor, up/down, send, contacts, chats, status, logs |
| **MCP** | `wa-mcp` | Cursor/Claude tools (stdio) |

Bridge REST must be up (`wa up` / `wa doctor`).

```bash
pip install mac-wa mac-wa-mcp   # or: brew install ml-lubich/tap/wa

wa -h
wa agent schema
wa doctor
wa contacts name                 # positional QUERY (not -q)
wa chats
wa send "+1…" "hello"
wa-mcp                           # MCP stdio (install mac-wa-mcp)
```

Repo layout: `wa-cli/` (Typer → PyPI `mac-wa`), `whatsapp-mcp-server/` (MCP → PyPI `mac-wa-mcp`), `whatsapp-bridge/` (Go).
