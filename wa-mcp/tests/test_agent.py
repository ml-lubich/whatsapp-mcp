"""Agent schema/guide unit tests and CLI wiring."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from wa_cli import agent, main

runner = CliRunner()


def test_build_schema_has_expected_keys():
    schema = agent.build_schema()
    assert schema["tool"] == "wa"
    assert schema["mcp"] == "wa-mcp"
    names = {c["name"] for c in schema["commands"]}
    assert {"doctor", "send", "agent schema", "agent guide"} <= names


def test_build_guide_mentions_cli_and_mcp():
    guide = agent.build_guide()
    assert "wa agent schema" in guide
    assert "wa-mcp" in guide


def test_agent_schema_cli():
    result = runner.invoke(main.app, ["agent", "schema"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["tool"] == "wa"
    assert payload["mcp"] == "wa-mcp"


def test_agent_guide_cli():
    result = runner.invoke(main.app, ["agent", "guide"])
    assert result.exit_code == 0
    assert "wa agent guide" in result.stdout


# --- schema structure / shape ---------------------------------------------

EXPECTED_COMMAND_NAMES = {
    "up",
    "down",
    "status",
    "logs",
    "send",
    "contacts",
    "chats",
    "download",
    "doctor",
    "agent schema",
    "agent guide",
}


def test_build_schema_version_is_a_string():
    schema = agent.build_schema()
    assert isinstance(schema["version"], str)
    assert schema["version"] != ""


def test_build_schema_help_key_present_and_nonempty():
    schema = agent.build_schema()
    assert isinstance(schema["help"], str)
    assert schema["help"] != ""


def test_build_schema_exact_command_names():
    schema = agent.build_schema()
    names = {c["name"] for c in schema["commands"]}
    assert names == EXPECTED_COMMAND_NAMES


def test_build_schema_no_duplicate_command_names():
    schema = agent.build_schema()
    names = [c["name"] for c in schema["commands"]]
    assert len(names) == len(set(names))


def test_build_schema_every_command_has_nonempty_help():
    schema = agent.build_schema()
    for cmd in schema["commands"]:
        assert isinstance(cmd["help"], str)
        assert cmd["help"].strip() != "", f"{cmd['name']} has blank help"


def test_build_schema_every_command_has_params_list():
    schema = agent.build_schema()
    for cmd in schema["commands"]:
        assert isinstance(cmd["params"], list), f"{cmd['name']}.params must be a list"


@pytest.mark.parametrize(
    "no_param_command",
    ["up", "down", "status", "doctor", "agent schema", "agent guide"],
)
def test_build_schema_zero_arg_commands_have_no_params(no_param_command):
    schema = agent.build_schema()
    cmd = next(c for c in schema["commands"] if c["name"] == no_param_command)
    assert cmd["params"] == []


@pytest.mark.parametrize(
    "command_name,expected_param_names",
    [
        ("logs", {"follow"}),
        ("send", {"recipient", "message", "force"}),
        ("contacts", {"query"}),
        ("chats", {"limit"}),
        ("download", {"message_id", "chat_jid"}),
    ],
)
def test_build_schema_command_has_exact_param_names(command_name, expected_param_names):
    schema = agent.build_schema()
    cmd = next(c for c in schema["commands"] if c["name"] == command_name)
    names = {p["name"] for p in cmd["params"]}
    assert names == expected_param_names


def test_build_schema_every_param_has_required_keys():
    schema = agent.build_schema()
    for cmd in schema["commands"]:
        for p in cmd["params"]:
            assert set(p.keys()) == {"name", "type", "required", "flags"}, cmd["name"]
            assert isinstance(p["name"], str) and p["name"] != ""
            assert isinstance(p["type"], str) and p["type"] != ""
            assert isinstance(p["required"], bool)
            assert isinstance(p["flags"], list)


def test_build_schema_send_params_marked_required_correctly():
    schema = agent.build_schema()
    send = next(c for c in schema["commands"] if c["name"] == "send")
    required = {p["name"] for p in send["params"] if p["required"]}
    optional = {p["name"] for p in send["params"] if not p["required"]}
    assert required == {"recipient", "message"}
    assert optional == {"force"}


def test_build_schema_send_force_flag_is_double_dash_force():
    schema = agent.build_schema()
    send = next(c for c in schema["commands"] if c["name"] == "send")
    force = next(p for p in send["params"] if p["name"] == "force")
    assert force["flags"] == ["--force"]


def test_build_schema_logs_follow_has_both_short_and_long_flags():
    schema = agent.build_schema()
    logs_cmd = next(c for c in schema["commands"] if c["name"] == "logs")
    follow = next(p for p in logs_cmd["params"] if p["name"] == "follow")
    assert set(follow["flags"]) == {"-f", "--follow"}


def test_build_schema_positional_params_have_no_flags():
    schema = agent.build_schema()
    send = next(c for c in schema["commands"] if c["name"] == "send")
    for name in ("recipient", "message"):
        p = next(x for x in send["params"] if x["name"] == name)
        assert p["flags"] == []


def test_build_schema_is_json_serializable():
    schema = agent.build_schema()
    dumped = json.dumps(schema)
    assert json.loads(dumped) == schema


def test_build_schema_is_deterministic_across_calls():
    assert agent.build_schema() == agent.build_schema()


def test_build_schema_returns_a_fresh_dict_each_call():
    # Mutating one call's result must never affect a later call.
    first = agent.build_schema()
    first["tool"] = "mutated"
    second = agent.build_schema()
    assert second["tool"] == "wa"


def test_build_schema_send_help_mentions_duplicate_guard_language():
    schema = agent.build_schema()
    send = next(c for c in schema["commands"] if c["name"] == "send")
    assert "force" in send["help"].lower()
    assert "duplicate" in send["help"].lower()


# --- guide structure --------------------------------------------------------


def test_build_guide_is_deterministic_across_calls():
    assert agent.build_guide() == agent.build_guide()


def test_build_guide_returns_str():
    assert isinstance(agent.build_guide(), str)


def test_build_guide_starts_with_markdown_h1():
    guide = agent.build_guide()
    assert guide.lstrip().startswith("# ")


def test_build_guide_mentions_force_and_no_digests_policy():
    guide = agent.build_guide()
    assert "--force" in guide
    assert "No unsolicited digests" in guide


def test_build_guide_mentions_every_top_level_command_verb():
    # Every zero/positional-arg top-level command name should appear as a
    # `wa <name>` invocation example somewhere in the guide text.
    guide = agent.build_guide()
    for name in ("doctor", "up", "send", "contacts"):
        assert f"wa {name}" in guide


def test_build_guide_nonempty_and_reasonably_sized():
    guide = agent.build_guide()
    assert len(guide) > 100


# --- CLI-level exactness: stdout must equal the pure-function output -------


def test_agent_schema_cli_output_equals_build_schema_exactly():
    result = runner.invoke(main.app, ["agent", "schema"])
    assert json.loads(result.stdout) == agent.build_schema()


def test_agent_guide_cli_output_equals_build_guide_exactly():
    result = runner.invoke(main.app, ["agent", "guide"])
    assert result.stdout == agent.build_guide()


def test_agent_guide_cli_has_no_extra_trailing_newline():
    # main.py calls typer.echo(agent.build_guide(), nl=False) — the guide
    # text's own trailing newline (if any) must not be doubled by echo.
    result = runner.invoke(main.app, ["agent", "guide"])
    guide = agent.build_guide()
    assert result.stdout.count("\n") == guide.count("\n")


# --- agent sub-app CLI wiring edge cases ------------------------------------


def test_agent_no_subcommand_shows_help_and_nonzero_exit():
    result = runner.invoke(main.app, ["agent"])
    assert result.exit_code != 0
    assert "schema" in result.output
    assert "guide" in result.output


def test_agent_help_flag_lists_both_subcommands():
    result = runner.invoke(main.app, ["agent", "--help"])
    assert result.exit_code == 0
    assert "schema" in result.output
    assert "guide" in result.output


def test_agent_unknown_subcommand_nonzero_exit():
    result = runner.invoke(main.app, ["agent", "bogus-subcommand"])
    assert result.exit_code != 0


def test_agent_schema_help_flag():
    result = runner.invoke(main.app, ["agent", "schema", "--help"])
    assert result.exit_code == 0


def test_agent_guide_help_flag():
    result = runner.invoke(main.app, ["agent", "guide", "--help"])
    assert result.exit_code == 0


def test_agent_schema_rejects_unexpected_extra_arg():
    result = runner.invoke(main.app, ["agent", "schema", "extra-arg"])
    assert result.exit_code != 0
