"""Apply trusted Interface model selections at the Lite session boundary.

Snapshots contain routing aliases and model settings, never endpoints or keys.
The existing agent owns client switching; this adapter supplies the per-model
settings that its public switch method does not accept.
"""

from __future__ import annotations

from typing import Any
from types import MethodType
import json
import time
import uuid

from hermes_constants import parse_reasoning_effort
from runtime_profile import RuntimeProfileError, validate_model_runtime


_FIELDS = frozenset({"id", "model", "provider", "api_mode", "context_length", "reasoning_effort"})
_IDENTITY_FIELDS = frozenset({"upstream_model", "display_name", "config_revision"})
_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})


def normalize_model_config(value: Any) -> dict[str, Any]:
    """Validate the narrow, server-to-server snapshot contract."""
    if not isinstance(value, dict) or set(value) not in (_FIELDS, _FIELDS | _IDENTITY_FIELDS):
        raise ValueError("model_config must contain only the supported model settings")
    result = {}
    for key in ("id", "model", "provider", "api_mode", "reasoning_effort"):
        raw = value[key]
        if not isinstance(raw, str) or not raw.strip() or len(raw) > (4096 if key == "model" else 256):
            raise ValueError(f"Invalid model_config {key}")
        result[key] = raw.strip()
    if result["provider"] != "custom":
        raise ValueError("Session models must use the local model proxy")
    if result["api_mode"] not in {"chat_completions", "codex_responses"}:
        raise ValueError("Invalid model_config api_mode")
    if result["reasoning_effort"] not in _EFFORTS:
        raise ValueError("Invalid model_config reasoning_effort")
    context_length = value["context_length"]
    if context_length is not None and (
        isinstance(context_length, bool) or not isinstance(context_length, int)
        or context_length <= 0
    ):
        raise ValueError("Invalid model_config context_length")
    result["context_length"] = context_length
    for key in _IDENTITY_FIELDS & set(value):
        raw = value[key]
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 256 or any(ord(c) < 32 for c in raw):
            raise ValueError(f"Invalid model_config {key}")
        result[key] = raw
    try:
        validate_model_runtime(result["provider"], result["api_mode"], label="Session model")
    except RuntimeProfileError as exc:
        raise ValueError(str(exc)) from exc
    return result


def model_identity(agent: Any) -> str:
    return (getattr(agent, "_potato_model_config", None) or {}).get("upstream_model") or getattr(agent, "model", "")


def public_model_snapshot(config: dict) -> dict:
    return {key: config[key] for key in ("id", "upstream_model", "display_name", "config_revision", "api_mode", "reasoning_effort", "context_length")}


def record_runtime_model(agent: Any, session_id: str, turn_id: str = "") -> None:
    config = getattr(agent, "_potato_model_config", {}) or {}
    db = getattr(agent, "_session_db", None)
    if not config.get("config_revision") or db is None:
        return
    snapshot = public_model_snapshot(config)
    def write(conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS potato_model_runs (
            turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            model_snapshot TEXT NOT NULL, admitted_at REAL NOT NULL)""")
        conn.execute("INSERT OR IGNORE INTO potato_model_runs VALUES (?, ?, ?, ?)",
                     (turn_id or uuid.uuid4().hex, session_id, json.dumps(snapshot, sort_keys=True), time.time()))
    db._execute_write(write)


def _sync_persisted_identity(agent: Any, config: dict) -> None:
    snapshot = public_model_snapshot(config)
    initial = getattr(agent, "_session_init_model_config", None)
    if isinstance(initial, dict):
        initial["potato_model"] = snapshot
    db = getattr(agent, "_session_db", None)
    session_id = getattr(agent, "session_id", "")
    if db is None or not session_id:
        return
    def write(conn):
        row = conn.execute("SELECT model_config FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return
        try:
            metadata = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("potato_model") != snapshot:
            metadata["potato_model"] = snapshot
            # Prevent inherited cold-resume code from restoring an obsolete
            # Model line from SQLite after the instance adapter is installed.
            conn.execute("UPDATE sessions SET model = ?, model_config = ?, system_prompt = NULL WHERE id = ?",
                         (snapshot["upstream_model"], json.dumps(metadata), session_id))
    db._execute_write(write)


def _install_identity_prompt(agent: Any) -> None:
    """Adapt the inherited prompt API on this instance, without changing turns."""
    if getattr(agent, "_potato_identity_prompt_installed", False):
        return

    def rewrite(value: str) -> str:
        config = getattr(agent, "_potato_model_config", {}) or {}
        if not config.get("upstream_model"):
            return value
        marker = "\nModel: " + agent.model
        before, found, after = value.rpartition(marker)
        if not found:
            return value
        identity = (f"\nModel: {config['upstream_model']}"
                    f"\nModel option: {config['display_name']} ({config['id']})"
                    f"\nReasoning effort: {config['reasoning_effort']}"
                    f"\nModel configuration revision: {config['config_revision']}")
        return before + identity + after

    original = getattr(agent, "_build_system_prompt", None)
    if callable(original):
        def build(self, *args, **kwargs):
            return rewrite(original(*args, **kwargs))
        agent._build_system_prompt = MethodType(build, agent)
    original_parts = getattr(agent, "_build_system_prompt_parts", None)
    if callable(original_parts):
        def parts(self, *args, **kwargs):
            result = dict(original_parts(*args, **kwargs))
            result["volatile"] = rewrite(result.get("volatile", ""))
            return result
        agent._build_system_prompt_parts = MethodType(parts, agent)
    agent._potato_identity_prompt_installed = True


def apply_model_config(agent: Any, value: dict[str, Any]) -> bool:
    """Apply a snapshot only while its session exclusively owns turn admission.

    This preserves the agent, history, accounting and delegation ownership. No
    shared environment/config file or upstream orchestration is changed.
    """
    config = normalize_model_config(value)
    runtime_matches = all(
        getattr(agent, key, None) == config[key] for key in ("model", "provider", "api_mode")
    )
    if runtime_matches and getattr(agent, "_potato_model_config", None) == config:
        return False

    from agent.model_metadata import MINIMUM_CONTEXT_LENGTH, get_model_context_length

    context_length = config["context_length"]
    if context_length is None:
        api_key = getattr(agent, "api_key", "")
        context_length = get_model_context_length(
            config["model"], base_url=getattr(agent, "base_url", ""),
            api_key=api_key if isinstance(api_key, str) else "",
            provider=config["provider"], config_context_length=None,
        )
    if context_length < MINIMUM_CONTEXT_LENGTH:
        raise ValueError(f"Session model context must be at least {MINIMUM_CONTEXT_LENGTH} tokens")

    if not runtime_matches:
        agent.switch_model(
            new_model=config["model"], new_provider=config["provider"],
            base_url=getattr(agent, "base_url", ""),
            api_key=getattr(agent, "api_key", ""), api_mode=config["api_mode"],
        )
    # Once runtime settings start changing, retries and rollback must reapply
    # the whole snapshot even when the client already has the desired model.
    agent._potato_model_config = None
    agent.reasoning_config = parse_reasoning_effort(config["reasoning_effort"])
    # Hermes' switch method clears the global context override. Keep the
    # selected option's value and the auxiliary compression hint in sync.
    agent._config_context_length = config["context_length"]
    agent._aux_compression_context_length_config = config["context_length"]
    compressor = getattr(agent, "context_compressor", None)
    if compressor is not None:
        compressor.update_model(
            model=config["model"], context_length=context_length,
            base_url=getattr(agent, "base_url", ""),
            api_key=getattr(agent, "api_key", ""), provider=config["provider"],
            api_mode=config["api_mode"],
        )
        primary = getattr(agent, "_primary_runtime", None)
        if isinstance(primary, dict):
            primary.update({
                "compressor_context_length": compressor.context_length,
                "compressor_threshold_tokens": compressor.threshold_tokens,
                "compressor_api_mode": config["api_mode"],
            })
    initial_meta = getattr(agent, "_session_init_model_config", None)
    if isinstance(initial_meta, dict):
        initial_meta["reasoning_config"] = agent.reasoning_config
    if config.get("upstream_model"):
        _install_identity_prompt(agent)
        _sync_persisted_identity(agent, config)
        agent._cached_system_prompt = None
    agent._potato_model_config = dict(config)
    return True
