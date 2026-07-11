# WP6: typer app wiring all commands (up, down, status, logs, send, contacts,
# chats, doctor). This stub only provides `app` so the `wa` entry point and
# `--help` are exercisable before WP6 lands.
import typer

app = typer.Typer(
    name="wa",
    help="Operate the local WhatsApp bridge + MCP stack.",
    no_args_is_help=True,
)


@app.command()
def doctor() -> None:
    """Placeholder — replaced by the full implementation in WP6."""
    raise NotImplementedError("wa doctor: implemented in WP6")
