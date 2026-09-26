---
name: wa
description: Operate the local WhatsApp bridge and MCP stack via the `wa` CLI or the `wa-mcp` MCP server — start/stop the Go bridge daemon, check health, search contacts, list chats, send messages, and download media. Use when the user asks to check WhatsApp status ("is the bridge up", "wa doctor"), send or draft a WhatsApp message, look up a WhatsApp contact or chat, download WhatsApp media, or wire an agent's WhatsApp tool-calling to Claude/Cursor via MCP. Read-only by default; sending is a deliberate, reviewed action.
---

# wa — WhatsApp CLI + MCP

Two surfaces over one Go bridge (`whatsapp-bridge/`, whatsmeow WhatsApp-Web client,
`localhost:8080`, local SQLite in `whatsapp-bridge/store/`):

- **CLI** `wa` (package `wa-mcp`, subdir `wa-mcp/`) — operator/agent convenience layer.
- **MCP** `wa-mcp` (package `mac-wa-mcp`, subdir `whatsapp-mcp-server/`) — stdio tools for Claude/Cursor.

Both need the bridge reachable on port 8080 for sends/reads that hit the REST API;
`wa contacts`/`wa chats` read the SQLite stores directly (read-only, `mode=ro`), no
bridge required for those two.

## Install

```bash
# CLI (wa)
git clone https://github.com/ml-lubich/whatsapp-mcp && uv tool install -e ./whatsapp-mcp/wa-mcp
# or, already cloned:
uv tool install -e ~/dev/whatsapp-mcp/wa-mcp

# MCP server (wa-mcp / mac-wa-mcp) — separate subdir, separate package
uv tool install -e ~/dev/whatsapp-mcp/whatsapp-mcp-server
```

### First run (new user)

1. Install Go, then build the bridge binary once: `cd whatsapp-bridge && go build -o whatsapp-bridge`.
   (`wa up` execs this pre-built binary directly — it does not `go run` for you.)
2. `wa up` — starts the bridge + MCP daemons as detached background processes, or run
   the bridge manually with `go run main.go` for the QR prompt to show inline.
3. Scan the QR code shown in the bridge's terminal/log with WhatsApp mobile
   (Linked Devices). Re-auth needed after ~20 days.
4. `wa doctor` — confirms binary, `uv`, daemons, REST reachability, and the two
   store DBs are all readable.

## CLI commands (`wa --help`)

| Command | Flags |
|---|---|
| `wa up` | — |
| `wa down` | — |
| `wa status` | — |
| `wa logs` | `-f/--follow` |
| `wa send RECIPIENT MESSAGE` | `--force` (bypass duplicate guard) |
| `wa contacts QUERY` | — |
| `wa chats` | `--limit INTEGER` (default 20) |
| `wa download MESSAGE_ID CHAT_JID` | — |
| `wa doctor` | — |
| `wa agent schema` / `wa agent guide` | — machine-readable schema / LLM playbook |

`wa send` prints the recent thread first and blocks near-duplicate sends unless
`--force` — review that context before composing.

## MCP

Launch (stdio): `wa-mcp` (after installing `whatsapp-mcp-server`), or
`uv --directory ~/dev/whatsapp-mcp/whatsapp-mcp-server run main.py`.

```bash
claude mcp add wa-mcp -- wa-mcp
```

12 tools: `search_contacts`, `list_messages`, `list_chats`, `get_chat`,
`get_direct_chat_by_contact`, `get_contact_chats`, `get_last_interaction`,
`get_message_context`, `send_message`, `send_file`, `send_audio_message`,
`download_media`.

## macOS permissions

None of the standard TCC prompts (no Contacts/Calendar/Automation/Full Disk
Access — the bridge talks to WhatsApp over the network and SQLite directly, no
Apple frameworks). The one popup a user may see: macOS Application Firewall's
"accept incoming connections?" prompt the first time the bridge binary binds
port 8080, if the firewall is on. Trigger it with a harmless start (no QR
scan, no send): `cd whatsapp-bridge && go build -o whatsapp-bridge && ./whatsapp-bridge` (Ctrl-C once the QR/prompt appears).

## Safety rules

- Read-only by default: `contacts`/`chats`/`status`/`doctor`/`logs` never write.
- `send`/`send_message`/`send_file`/`send_audio_message` only on the user's explicit
  ask — never as a side effect of a check, test, or "just verifying" command.
- `send` shows recent thread context and refuses a near-duplicate message unless
  `--force`/explicit override is given — don't resend or re-answer what's already there.
- Never commit `whatsapp-bridge/store/` (session + message DBs) or downloaded media —
  it's real personal WhatsApp data; already gitignored.
