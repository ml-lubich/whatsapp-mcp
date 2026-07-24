# wa — WhatsApp CLI + MCP (imsg pattern)

Product family **wa-mcp**: CLI package `wa-mcp` (script `wa`) + MCP server package `mac-wa-mcp` (script `wa-mcp`).

Same idea as [`imsg`](https://github.com/ml-lubich/imsg): **CLI first** (cheap for agents), **MCP when needed**.

| Surface | Command | PyPI package | Role |
|---------|---------|--------------|------|
| **CLI** | `wa` | `wa-mcp` | doctor, up/down, send, contacts, chats, status, logs |
| **MCP** | `wa-mcp` | `mac-wa-mcp` | Cursor/Claude tools (stdio) |

Bridge REST must be up (`wa up` / `wa doctor`).

```bash
pip install wa-mcp mac-wa-mcp   # or: brew install ml-lubich/tap/wa

wa -h
wa agent schema
wa doctor
wa contacts name                 # positional QUERY (not -q)
wa chats
wa send "+1…" "hello"
wa-mcp                           # MCP stdio (mac-wa-mcp)
```

Repo layout: `wa-cli/` (Typer → PyPI `wa-mcp`), `whatsapp-mcp-server/` (MCP → PyPI `mac-wa-mcp`), `whatsapp-bridge/` (Go).
