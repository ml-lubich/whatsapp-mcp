# mac-wa-mcp (`wa-mcp`)

WhatsApp MCP server (stdio). Pair with the `wa` CLI (`pip install wa-mcp`).

```bash
pip install mac-wa-mcp
wa-mcp
```

## Testing

```bash
uv sync --group dev
uv run pytest   # runs tests + coverage gate (--cov-fail-under=85, see pyproject.toml)
```
