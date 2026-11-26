from __future__ import annotations

import json
from pathlib import Path

import pytest

from kronos import config


def write_lock(tmp_path: Path, **ids: str) -> Path:
    lock = tmp_path / "claude-lock.json"
    resources = {f"./{path}": {"id": value, "type": "x"} for path, value in ids.items()}
    lock.write_text(json.dumps({"resources": resources}))
    return lock


def test_lookup_tolerates_dot_slash_prefix() -> None:
    ids = {"./agents/jarvis.md": "agent_1", "/abs/repo/environments/kronos.yaml": "env_1"}
    assert config.lookup_lock_id(ids, "agents/jarvis.md") == "agent_1"
    assert config.lookup_lock_id(ids, "environments/kronos.yaml") == "env_1"
    assert config.lookup_lock_id(ids, "vaults/jarvis.yaml") is None


def test_settings_from_lock_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_lock(
        tmp_path,
        **{
            "agents/jarvis.md": "agent_abc",
            "environments/kronos.yaml": "env_abc",
            "memory_stores/jarvis.yaml": "memstore_abc",
            "vaults/jarvis.yaml": "vlt_abc",
        },
    )
    for var in ("KRONOS_AGENT_ID", "KRONOS_ENV_ID", "KRONOS_SESSION_BUDGET_USD", "KRONOS_LOCKFILE", "KRONOS_SHELL_POLICY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    settings = config.load_settings(tmp_path)
    assert settings.agent_id == "agent_abc"
    assert settings.environment_id == "env_abc"
    assert settings.memory_store_id == "memstore_abc"
    assert settings.vault_id == "vlt_abc"
    assert settings.budget_param == {"type": "limit", "max_list_cost": {"amount": "1000", "currency": "USD"}}
    assert settings.console_url("sesn_1") == "https://platform.claude.com/workspaces/default/sessions/sesn_1"


def test_env_overrides_lock_and_budget_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_lock(tmp_path, **{"agents/jarvis.md": "agent_lock", "environments/kronos.yaml": "env_lock"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRONOS_AGENT_ID", "agent_env")
    monkeypatch.setenv("KRONOS_SESSION_BUDGET_USD", "12.5")
    monkeypatch.setenv("KRONOS_SHELL_POLICY", "deny")
    monkeypatch.setenv("KRONOS_WORKSPACE", "wrkspc_01")
    settings = config.load_settings(tmp_path)
    assert settings.agent_id == "agent_env"
    assert settings.environment_id == "env_lock"
    assert settings.budget_minor_units == "1250"
    assert settings.shell_policy == "deny"
    assert settings.console_url("s") == "https://platform.claude.com/workspaces/wrkspc_01/sessions/s"


def test_budget_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_lock(tmp_path, **{"agents/jarvis.md": "a", "environments/kronos.yaml": "e"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KRONOS_SESSION_BUDGET_USD", "off")
    assert config.load_settings(tmp_path).budget_param is None


def test_missing_ids_raise_helpful_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for var in ("KRONOS_AGENT_ID", "KRONOS_ENV_ID", "KRONOS_LOCKFILE"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_settings(tmp_path)
    assert "ant apply" in str(excinfo.value)


def test_dotenv_does_not_override_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text('HA_URL="http://ha.local:8123"\nexport HA_TOKEN=abc\n# comment\nKRONOS_X=1\n')
    monkeypatch.setenv("HA_TOKEN", "keep-me")
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("KRONOS_X", raising=False)
    config.load_dotenv(tmp_path / ".env")
    import os

    assert os.environ["HA_URL"] == "http://ha.local:8123"
    assert os.environ["HA_TOKEN"] == "keep-me"
    assert os.environ["KRONOS_X"] == "1"
