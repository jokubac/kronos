"""Resolve the IDs and settings the runtime needs.

Control plane (agents, environment, memory store, vault) is managed by `ant apply`,
which records every resource ID in `claude-lock.json`. The runtime reads those IDs
from the lock file, or from environment variables that override it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

LOCK_FILE_NAME = "claude-lock.json"

# Resource file paths as `ant apply` records them (relative to the repo root).
AGENT_FILE = "agents/jarvis.md"
ENVIRONMENT_FILE = "environments/kronos.yaml"
MEMORY_STORE_FILE = "memory_stores/jarvis.yaml"
VAULT_FILE = "vaults/jarvis.yaml"


class ConfigError(RuntimeError):
    """Raised when a required setting cannot be resolved."""


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from a .env file without overriding existing variables."""
    path = path or Path.cwd() / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def find_lock_file(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default cwd) looking for claude-lock.json."""
    explicit = os.environ.get("KRONOS_LOCKFILE")
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / LOCK_FILE_NAME
        if candidate.is_file():
            return candidate
    return None


def read_lock_ids(lock_path: Path) -> dict[str, str]:
    """Map resource file path (as recorded) -> resource ID."""
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    resources = data.get("resources", {})
    out: dict[str, str] = {}
    for key, value in resources.items():
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            out[key] = value["id"]
    return out


def lookup_lock_id(ids: dict[str, str], resource_file: str) -> str | None:
    """Find an ID by resource path, tolerating a leading `./` or an absolute prefix."""
    for key, value in ids.items():
        normalized = key.replace("\\", "/")
        if normalized == resource_file or normalized.endswith("/" + resource_file):
            return value
    return None


def _usd_to_minor_units(value: str) -> str:
    """'10' or '12.5' -> '1250' (integer cents as a string, no leading zeros)."""
    try:
        cents = round(float(value) * 100)
    except ValueError as exc:
        raise ConfigError(f"KRONOS_SESSION_BUDGET_USD must be a number, got {value!r}") from exc
    if cents <= 0:
        raise ConfigError("KRONOS_SESSION_BUDGET_USD must be greater than zero")
    return str(cents)


@dataclass
class Settings:
    agent_id: str
    environment_id: str
    memory_store_id: str | None = None
    vault_id: str | None = None
    workspace: str = "default"
    budget_minor_units: str | None = "1000"  # $10.00 per session by default
    shell_policy: str = "ask"  # ask | deny | allow
    language: str = "lt"
    state_dir: Path = field(default_factory=lambda: Path.home() / ".kronos")
    lock_file: Path | None = None

    @property
    def budget_param(self) -> dict | None:
        if not self.budget_minor_units:
            return None
        return {
            "type": "limit",
            "max_list_cost": {"amount": self.budget_minor_units, "currency": "USD"},
        }

    def console_url(self, session_id: str) -> str:
        return f"https://platform.claude.com/workspaces/{self.workspace}/sessions/{session_id}"


def load_settings(start: Path | None = None) -> Settings:
    """Resolve settings from environment variables, falling back to claude-lock.json."""
    load_dotenv()
    lock_path = find_lock_file(start)
    ids = read_lock_ids(lock_path) if lock_path else {}

    def resolve(env_name: str, resource_file: str) -> str | None:
        return os.environ.get(env_name) or lookup_lock_id(ids, resource_file)

    agent_id = resolve("KRONOS_AGENT_ID", AGENT_FILE)
    environment_id = resolve("KRONOS_ENV_ID", ENVIRONMENT_FILE)
    missing = [
        name
        for name, value in (("agent", agent_id), ("environment", environment_id))
        if not value
    ]
    if missing:
        where = f" (lock file: {lock_path})" if lock_path else " (no claude-lock.json found)"
        raise ConfigError(
            "Missing resource IDs for: " + ", ".join(missing) + where + ".\n"
            "Run `ant apply agents/jarvis.md environments/kronos.yaml memory_stores/jarvis.yaml vaults/jarvis.yaml` "
            "from the repository root, or set KRONOS_AGENT_ID and KRONOS_ENV_ID."
        )

    budget_env = os.environ.get("KRONOS_SESSION_BUDGET_USD")
    if budget_env is None:
        budget = "1000"
    elif budget_env.strip().lower() in ("", "0", "none", "off"):
        budget = None
    else:
        budget = _usd_to_minor_units(budget_env)

    shell_policy = os.environ.get("KRONOS_SHELL_POLICY", "ask").strip().lower()
    if shell_policy not in ("ask", "deny", "allow"):
        raise ConfigError("KRONOS_SHELL_POLICY must be one of: ask, deny, allow")

    state_dir = Path(os.environ.get("KRONOS_STATE_DIR", str(Path.home() / ".kronos"))).expanduser()

    return Settings(
        agent_id=agent_id,  # type: ignore[arg-type]
        environment_id=environment_id,  # type: ignore[arg-type]
        memory_store_id=resolve("KRONOS_MEMORY_STORE_ID", MEMORY_STORE_FILE),
        vault_id=resolve("KRONOS_VAULT_ID", VAULT_FILE),
        workspace=os.environ.get("KRONOS_WORKSPACE", "default"),
        budget_minor_units=budget,
        shell_policy=shell_policy,
        language=os.environ.get("KRONOS_LANGUAGE", "lt"),
        state_dir=state_dir,
        lock_file=lock_path,
    )
