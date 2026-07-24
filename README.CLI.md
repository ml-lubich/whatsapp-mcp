# wa — WhatsApp CLI + MCP (imsg pattern)

Same idea as [`imsg`](https://github.com/ml-lubich/imsg): **CLI first** (cheap for agents), **MCP when needed**.

| Surface | Command | Role |
|---------|---------|------|
| **CLI** | `wa` | doctor, up/down, send, contacts, chats, status, logs |
| **MCP** | `wa-mcp` | Cursor/Claude tools (stdio) |

Bridge REST must be up (`wa up` / `wa doctor`).

```bash
wa doctor
wa contacts -q name
wa chats
wa send "+1…" "hello"
wa-mcp   # MCP stdio server
```

Repo layout: `wa-cli/` (Typer CLI), `whatsapp-mcp-server/` (MCP), `whatsapp-bridge/` (Go).  
Optional rename later: this repo → `wa` or `whatsapp-client` — binaries already match the imsg pattern (`wa` / `wa-mcp`).
