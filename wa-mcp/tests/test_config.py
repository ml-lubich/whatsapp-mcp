"""Tests for wa_cli.config: repo-root discovery, state-dir resolution, paths."""

from __future__ import annotations

import importlib
import os

import pytest

from wa_cli import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no ambient WA_REPO/XDG_STATE_HOME/HOME leaks into a test."""
    monkeypatch.delenv("WA_REPO", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)


# --- repo_root() ---


def test_repo_root_resolves_dir_containing_whatsapp_bridge():
    root = config.repo_root()
    assert (root / "whatsapp-bridge").is_dir()


def test_repo_root_honors_wa_repo_env_override(tmp_path, monkeypatch):
    fake_root = tmp_path / "fake-repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    root = config.repo_root()

    assert root == fake_root


def test_repo_root_wa_repo_override_wins_even_if_invalid_path_shape(tmp_path, monkeypatch):
    # WA_REPO always wins per spec, even pointing somewhere unusual, as long as it exists.
    fake_root = tmp_path / "another-repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    assert config.repo_root() == fake_root


# --- state_dir() ---


def test_state_dir_uses_xdg_state_home_when_set(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()

    assert result == xdg / "wa-cli"


def test_state_dir_defaults_to_home_dot_wa_cli(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    result = config.state_dir()

    assert result == tmp_path / ".wa-cli"


def test_state_dir_is_created_on_disk(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()

    assert result.is_dir()


def test_state_dir_creation_is_idempotent(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    first = config.state_dir()
    second = config.state_dir()

    assert first == second
    assert second.is_dir()


def test_state_dir_is_outside_repo_tree(tmp_path, monkeypatch):
    """Critic amendment A3(a): the resolved state dir must never live inside the
    repo working tree (e.g. must not resolve under wa-cli/ or the repo root),
    since it holds runtime pidfiles/logs that must not be committed or confused
    with tracked source."""
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()
    repo_root = config.repo_root()

    assert repo_root not in result.parents
    assert result != repo_root
    # also verify against the real on-disk repo root independent of env fakery
    real_repo_root = importlib.import_module("wa_cli.config").__file__
    assert str(result).startswith(str(tmp_path))
    assert not str(result).startswith(str(os.path.dirname(os.path.dirname(real_repo_root))))


# --- derived state-dir paths ---


def test_bridge_pidfile_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.bridge_pidfile() == config.state_dir() / "bridge.pid"


def test_bridge_log_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.bridge_log() == config.state_dir() / "bridge.log"


def test_mcp_pidfile_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.mcp_pidfile() == config.state_dir() / "mcp.pid"


def test_mcp_log_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.mcp_log() == config.state_dir() / "mcp.log"


# --- bridge_binary() / DB paths / bridge_url() ---


def test_bridge_binary_path(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    result = config.bridge_binary()

    assert result == fake_root / "whatsapp-bridge" / "whatsapp-bridge"


def test_contacts_db_path(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    result = config.contacts_db()

    assert result == fake_root / "whatsapp-bridge" / "store" / "whatsapp.db"


def test_messages_db_path(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    result = config.messages_db()

    assert result == fake_root / "whatsapp-bridge" / "store" / "messages.db"


def test_bridge_url_constant():
    assert config.bridge_url() == "http://localhost:8080"


def test_state_dir_falls_back_to_path_home_when_home_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

    result = config.state_dir()

    assert result == tmp_path / ".wa-cli"
    assert result.is_dir()
