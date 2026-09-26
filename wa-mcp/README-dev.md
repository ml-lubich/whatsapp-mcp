# wa-cli (dev)

Developer README for the `wa` CLI package. See the repo root `README.md` for
end-user install/usage docs (added in WP7).

## Dev setup

```
cd wa-mcp
uv sync
uv run pytest              # runs tests + coverage gate (--cov-fail-under=85, see pyproject.toml)
uv run wa --help
```

## Layout

```
wa_cli/
  ui.py       gradient renderer, badges, tables, spinner (WP1)
  config.py   paths, repo-root resolution, state dir, DB paths (WP2)
  api.py      httpx REST client (send + health probe) (WP3)
  db.py       read-only SQLite queries (contacts, chats) (WP4)
  daemon.py   process mgmt (spawn/pidfile/signal/health-gate) (WP5)
  main.py     typer app wiring all commands (WP6)
```
