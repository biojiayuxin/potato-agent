"""Process-wide runtime capability profile for downstream Hermes builds.

The profile path is captured on the first call to :func:`get_runtime_profile`
and is intentionally immutable for the lifetime of the interpreter.  Runtime
profiles are deployment policy, not per-session configuration; changing one
requires starting a fresh process.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


PROFILE_ENV_VAR = "HERMES_RUNTIME_PROFILE_PATH"
POTATO_ENABLED_TOOLSETS = (
    "terminal",
    "file",
    "vision",
    "browser",
    "skills",
    "code_execution",
    "todo",
    "memory",
    "session_search",
    "delegation",
)
POTATO_EXPECTED_TOOLS = (
    "terminal",
    "process",
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "vision_analyze",
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_scroll",
    "browser_back",
    "browser_press",
    "browser_get_images",
    "browser_vision",
    "browser_console",
    "browser_cdp",
    "browser_dialog",
    "skills_list",
    "skill_view",
    "skill_manage",
    "execute_code",
    "todo",
    "memory",
    "session_search",
    "delegate_task",
)
PROFILE_RESERVED_REQUEST_KEYS = frozenset(
    {
        "model",
        "messages",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "functions",
        "function_call",
        "parallel_tool_calls",
        "plugins",
        "store",
        "stream",
        "stream_options",
        "web_search_options",
    }
)
PROFILE_ALLOWED_REQUEST_OVERRIDE_KEYS = frozenset(
    {
        "extra_body",
        "frequency_penalty",
        "logit_bias",
        "max_completion_tokens",
        "max_output_tokens",
        "max_tokens",
        "metadata",
        "presence_penalty",
        "reasoning_effort",
        "response_format",
        "seed",
        "service_tier",
        "speed",
        "stop",
        "temperature",
        "timeout",
        "top_p",
        "user",
        "verbosity",
    }
)
PROFILE_ALLOWED_EXTRA_BODY_KEYS = frozenset(
    {
        "cache_control",
        "enable_thinking",
        "metadata",
        "options",
        "prompt_cache_key",
        "reasoning",
        "reasoning_effort",
        "service_tier",
        "tags",
        "think",
        "thinking",
        "thinkingConfig",
        "thinking_budget",
        "thinking_config",
        "verbosity",
        "vl_high_resolution_images",
    }
)
PROFILE_ALLOWED_FINAL_REQUEST_KEYS = {
    "chat_completions": frozenset(
        set(PROFILE_ALLOWED_REQUEST_OVERRIDE_KEYS)
        | {
            "extra_body",
            "messages",
            "model",
            "parallel_tool_calls",
            "prompt_cache_key",
            "store",
            "stream",
            "stream_options",
            "tool_choice",
            "tools",
        }
    ),
    "codex_responses": frozenset(
        set(PROFILE_ALLOWED_REQUEST_OVERRIDE_KEYS)
        | {
            "extra_body",
            "extra_headers",
            "include",
            "input",
            "instructions",
            "model",
            "parallel_tool_calls",
            "prompt_cache_key",
            "reasoning",
            "store",
            "stream",
            "stream_options",
            "tool_choice",
            "tools",
        }
    ),
}
_PROFILE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_ROOT_KEYS = {
    "schema_version",
    "name",
    "revision",
    "toolsets",
    "expected_tools",
    "plugins",
    "providers",
    "mcp",
    "runtime",
}


class RuntimeProfileError(RuntimeError):
    """Raised when an explicitly configured runtime profile is invalid."""


def _string_tuple(
    value: Any, field: str, *, allow_empty: bool = True
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise RuntimeProfileError(f"{field} must be a list of non-empty strings")
    items = tuple(item.strip() for item in value)
    if not allow_empty and not items:
        raise RuntimeProfileError(f"{field} must not be empty")
    if len(items) != len(set(items)):
        raise RuntimeProfileError(f"{field} must not contain duplicates")
    return items


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeProfileError(f"{field} must be a mapping")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details = []
    if missing:
        details.append("missing " + ", ".join(missing))
    if unknown:
        details.append("unknown " + ", ".join(unknown))
    raise RuntimeProfileError(f"{field} has invalid keys: {'; '.join(details)}")


def _positive_integer(value: Any, field: str, *, expected: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeProfileError(f"{field} must be a positive integer")
    if expected is not None and value != expected:
        raise RuntimeProfileError(f"{field} must be {expected}")
    return value


@dataclass(frozen=True)
class RuntimeProfile:
    """Validated, immutable subset of the runtime profile schema."""

    path: Path
    schema_version: int
    name: str
    revision: int
    enabled_toolsets: tuple[str, ...]
    disabled_toolsets: frozenset[str]
    expected_tools: tuple[str, ...]
    allowed_general_plugin_keys: frozenset[str]
    forbidden_plugin_kinds: frozenset[str]
    allow_user_plugins: bool
    allow_project_plugins: bool
    allow_entrypoint_plugins: bool
    model_providers: tuple[str, ...]
    api_modes: tuple[str, ...]
    browser_provider: str
    memory_provider: str
    context_engine: str
    web_providers: tuple[str, ...]
    mcp_enabled: bool
    allow_lazy_installs: bool
    lsp_enabled: bool
    lsp_install_strategy: str
    terminal_backend: str
    skills_dependency_strategy: str

    @classmethod
    def from_mapping(cls, path: Path, data: Mapping[str, Any]) -> "RuntimeProfile":
        _exact_keys(data, _ROOT_KEYS, "runtime profile")
        schema_version = _positive_integer(
            data["schema_version"], "schema_version", expected=1
        )
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeProfileError("name must be a non-empty string")
        name = name.strip()
        if _PROFILE_NAME_RE.fullmatch(name) is None:
            raise RuntimeProfileError(
                "name must start with a lowercase letter and contain only "
                "lowercase letters, digits, underscores, or hyphens"
            )
        revision = _positive_integer(data["revision"], "revision")

        toolsets = _mapping(data.get("toolsets"), "toolsets")
        _exact_keys(toolsets, {"enabled", "disabled"}, "toolsets")
        plugins = _mapping(data.get("plugins"), "plugins")
        _exact_keys(
            plugins,
            {
                "allow_user",
                "allow_project",
                "allow_entrypoint",
                "allowed_general_keys",
                "forbidden_kinds",
            },
            "plugins",
        )
        providers = _mapping(data.get("providers"), "providers")
        _exact_keys(
            providers,
            {"model", "api_modes", "browser", "memory", "context_engine", "web"},
            "providers",
        )
        runtime = _mapping(data.get("runtime"), "runtime")
        _exact_keys(
            runtime,
            {
                "allow_lazy_installs",
                "lsp_enabled",
                "lsp_install_strategy",
                "terminal_backend",
                "skills_dependency_strategy",
            },
            "runtime",
        )
        mcp = _mapping(data.get("mcp"), "mcp")
        _exact_keys(mcp, {"enabled"}, "mcp")

        enabled = _string_tuple(
            toolsets.get("enabled"), "toolsets.enabled", allow_empty=False
        )
        disabled = frozenset(
            _string_tuple(toolsets.get("disabled"), "toolsets.disabled")
        )
        overlap = set(enabled) & disabled
        if overlap:
            raise RuntimeProfileError(
                "toolsets.enabled and toolsets.disabled overlap: "
                + ", ".join(sorted(overlap))
            )
        expected = _string_tuple(
            data.get("expected_tools"), "expected_tools", allow_empty=False
        )
        if enabled != POTATO_ENABLED_TOOLSETS:
            raise RuntimeProfileError(
                "toolsets.enabled must match the sealed Potato toolset manifest"
            )
        if expected != POTATO_EXPECTED_TOOLS:
            raise RuntimeProfileError(
                "expected_tools must match the sealed Potato tool manifest"
            )

        model_providers = _string_tuple(
            providers.get("model"), "providers.model", allow_empty=False
        )
        api_modes = _string_tuple(
            providers.get("api_modes"), "providers.api_modes", allow_empty=False
        )

        def _bool(mapping: Mapping[str, Any], key: str, field: str) -> bool:
            value = mapping.get(key)
            if not isinstance(value, bool):
                raise RuntimeProfileError(f"{field} must be a boolean")
            return value

        def _string(mapping: Mapping[str, Any], key: str, field: str) -> str:
            value = mapping.get(key)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeProfileError(f"{field} must be a non-empty string")
            return value.strip()

        allowed_general_plugin_keys = frozenset(
            _string_tuple(
                plugins.get("allowed_general_keys"),
                "plugins.allowed_general_keys",
            )
        )
        forbidden_plugin_kinds = frozenset(
            _string_tuple(
                plugins.get("forbidden_kinds"),
                "plugins.forbidden_kinds",
                allow_empty=False,
            )
        )
        allow_user_plugins = _bool(plugins, "allow_user", "plugins.allow_user")
        allow_project_plugins = _bool(
            plugins, "allow_project", "plugins.allow_project"
        )
        allow_entrypoint_plugins = _bool(
            plugins, "allow_entrypoint", "plugins.allow_entrypoint"
        )
        browser_provider = _string(providers, "browser", "providers.browser")
        memory_provider = _string(providers, "memory", "providers.memory")
        context_engine = _string(
            providers, "context_engine", "providers.context_engine"
        )
        web_providers = _string_tuple(providers.get("web"), "providers.web")
        mcp_enabled = _bool(mcp, "enabled", "mcp.enabled")
        allow_lazy_installs = _bool(
            runtime, "allow_lazy_installs", "runtime.allow_lazy_installs"
        )
        lsp_enabled = _bool(runtime, "lsp_enabled", "runtime.lsp_enabled")
        lsp_install_strategy = _string(
            runtime, "lsp_install_strategy", "runtime.lsp_install_strategy"
        )
        terminal_backend = _string(
            runtime, "terminal_backend", "runtime.terminal_backend"
        )
        skills_dependency_strategy = _string(
            runtime,
            "skills_dependency_strategy",
            "runtime.skills_dependency_strategy",
        )

        if model_providers != ("custom",):
            raise RuntimeProfileError("providers.model must be ('custom',)")
        if not set(api_modes).issubset({"codex_responses", "chat_completions"}):
            raise RuntimeProfileError("providers.api_modes contains an unsupported transport")
        fixed_values = (
            (browser_provider, "local", "providers.browser"),
            (memory_provider, "builtin", "providers.memory"),
            (context_engine, "compressor", "providers.context_engine"),
            (web_providers, (), "providers.web"),
            (mcp_enabled, False, "mcp.enabled"),
            (allow_lazy_installs, False, "runtime.allow_lazy_installs"),
            (lsp_install_strategy, "manual", "runtime.lsp_install_strategy"),
            (terminal_backend, "local", "runtime.terminal_backend"),
            (
                skills_dependency_strategy,
                "user_managed",
                "runtime.skills_dependency_strategy",
            ),
            (allow_user_plugins, False, "plugins.allow_user"),
            (allow_project_plugins, False, "plugins.allow_project"),
            (allow_entrypoint_plugins, False, "plugins.allow_entrypoint"),
            (allowed_general_plugin_keys, frozenset(), "plugins.allowed_general_keys"),
        )
        for actual, required, field in fixed_values:
            if actual != required:
                raise RuntimeProfileError(f"{field} must be {required!r}")
        if "platform" not in forbidden_plugin_kinds:
            raise RuntimeProfileError("plugins.forbidden_kinds must include 'platform'")

        return cls(
            path=path,
            schema_version=schema_version,
            name=name,
            revision=revision,
            enabled_toolsets=enabled,
            disabled_toolsets=disabled,
            expected_tools=expected,
            allowed_general_plugin_keys=allowed_general_plugin_keys,
            forbidden_plugin_kinds=forbidden_plugin_kinds,
            allow_user_plugins=allow_user_plugins,
            allow_project_plugins=allow_project_plugins,
            allow_entrypoint_plugins=allow_entrypoint_plugins,
            model_providers=model_providers,
            api_modes=api_modes,
            browser_provider=browser_provider,
            memory_provider=memory_provider,
            context_engine=context_engine,
            web_providers=web_providers,
            mcp_enabled=mcp_enabled,
            allow_lazy_installs=allow_lazy_installs,
            lsp_enabled=lsp_enabled,
            lsp_install_strategy=lsp_install_strategy,
            terminal_backend=terminal_backend,
            skills_dependency_strategy=skills_dependency_strategy,
        )

    @property
    def expected_tool_set(self) -> frozenset[str]:
        return frozenset(self.expected_tools)

    def constrain_enabled_toolsets(
        self, requested: Iterable[str] | None
    ) -> list[str]:
        """Return the profile maximum, preserving intentional narrow agents."""
        if requested is None:
            return list(self.enabled_toolsets)
        requested_set = {str(item) for item in requested}
        if requested_set & {"all", "*"} or any(
            item.startswith("hermes-") for item in requested_set
        ):
            return list(self.enabled_toolsets)
        return [name for name in self.enabled_toolsets if name in requested_set]

    def merge_disabled_toolsets(
        self, requested: Iterable[str] | None
    ) -> list[str]:
        disabled = set(self.disabled_toolsets)
        disabled.update(str(item) for item in requested or ())
        return sorted(disabled)

    def allows_plugin(self, *, source: str, kind: str, key: str) -> bool:
        """Apply fail-closed plugin policy before a plugin module is imported."""
        if source not in {"bundled", "user", "project", "entrypoint"}:
            return False
        if kind not in {"standalone", "backend", "exclusive", "platform", "model-provider"}:
            return False
        if kind in self.forbidden_plugin_kinds:
            return False
        if source == "user" and not self.allow_user_plugins:
            return False
        if source == "project" and not self.allow_project_plugins:
            return False
        if source == "entrypoint" and not self.allow_entrypoint_plugins:
            return False
        if source != "bundled":
            return True
        if kind == "model-provider":
            return key.rsplit("/", 1)[-1] in self.model_providers
        return key in self.allowed_general_plugin_keys


_UNSET = object()
_profile: RuntimeProfile | None | object = _UNSET
_profile_lock = threading.Lock()


def get_runtime_profile() -> RuntimeProfile | None:
    """Load the configured profile once, or return ``None`` for upstream mode."""
    global _profile
    if _profile is not _UNSET:
        return _profile  # type: ignore[return-value]
    with _profile_lock:
        if _profile is not _UNSET:
            return _profile  # type: ignore[return-value]
        raw_path = os.environ.get(PROFILE_ENV_VAR, "").strip()
        if not raw_path:
            _profile = None
            return None
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise RuntimeProfileError(
                f"{PROFILE_ENV_VAR} points to a missing file: {path}"
            )
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeProfileError(f"cannot parse runtime profile {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeProfileError("runtime profile root must be a mapping")
        loaded = RuntimeProfile.from_mapping(path, data)
        if not loaded.allow_lazy_installs:
            os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
        _profile = loaded
        return loaded


def runtime_profile_active() -> bool:
    return get_runtime_profile() is not None


def runtime_profile_tool_error(tool_name: str) -> str | None:
    """Return the canonical denial result when a profile hides a tool."""
    profile = get_runtime_profile()
    if profile is None or tool_name in profile.expected_tool_set:
        return None
    return json.dumps(
        {"error": f"Tool disabled by runtime profile: {tool_name}"},
        ensure_ascii=False,
    )


def validate_profile_request_overrides(
    overrides: Mapping[str, Any] | None,
    *,
    source: str = "request_overrides",
) -> None:
    """Reject user-controlled fields that can replace Hermes request state."""
    profile = get_runtime_profile()
    if profile is None or overrides is None:
        return
    if not isinstance(overrides, Mapping):
        raise RuntimeProfileError(
            f"{source} must be a mapping under runtime profile {profile.name!r}"
        )

    conflicts = {
        f"{source}.{key}"
        for key in overrides
        if key in PROFILE_RESERVED_REQUEST_KEYS
    }
    extra_body = overrides.get("extra_body")
    if extra_body is not None and not isinstance(extra_body, Mapping):
        raise RuntimeProfileError(
            f"{source}.extra_body must be a mapping under runtime profile "
            f"{profile.name!r}"
        )
    if isinstance(extra_body, Mapping):
        conflicts.update(
            f"{source}.extra_body.{key}"
            for key in extra_body
            if key in PROFILE_RESERVED_REQUEST_KEYS
        )
    if conflicts:
        raise RuntimeProfileError(
            "Reserved request fields cannot be overridden under runtime profile "
            f"{profile.name!r}: {', '.join(sorted(conflicts))}"
        )

    unsupported = {
        f"{source}.{key}"
        for key in overrides
        if key not in PROFILE_ALLOWED_REQUEST_OVERRIDE_KEYS
    }
    if isinstance(extra_body, Mapping):
        unsupported.update(
            f"{source}.extra_body.{key}"
            for key in extra_body
            if key not in PROFILE_ALLOWED_EXTRA_BODY_KEYS
        )
    if unsupported:
        raise RuntimeProfileError(
            "Request override fields are not allowed under runtime profile "
            f"{profile.name!r}: {', '.join(sorted(unsupported))}"
        )


def validate_profile_request_boundary(
    request: Mapping[str, Any],
    *,
    api_mode: str,
) -> None:
    """Validate the final SDK kwargs before a profiled model request is sent."""
    profile = get_runtime_profile()
    if profile is None:
        return
    if not isinstance(request, Mapping):
        raise RuntimeProfileError("Final model request must be a mapping")
    if api_mode not in profile.api_modes:
        raise RuntimeProfileError(
            f"Final model request API mode {api_mode!r} is not allowed by "
            f"runtime profile {profile.name!r}"
        )

    capability_conflicts = sorted(
        {"plugins", "web_search_options"}.intersection(request)
    )
    if capability_conflicts:
        raise RuntimeProfileError(
            "Final model request contains server-side capability fields: "
            + ", ".join(capability_conflicts)
        )

    allowed_final_keys = PROFILE_ALLOWED_FINAL_REQUEST_KEYS.get(api_mode, frozenset())
    unsupported_final_keys = sorted(set(request) - allowed_final_keys)
    if unsupported_final_keys:
        raise RuntimeProfileError(
            f"Final {api_mode} request contains fields outside the runtime "
            "profile allowlist: " + ", ".join(unsupported_final_keys)
        )

    extra_body = request.get("extra_body")
    if extra_body is not None and not isinstance(extra_body, Mapping):
        raise RuntimeProfileError("Final model request extra_body must be a mapping")
    if isinstance(extra_body, Mapping):
        conflicts = sorted(PROFILE_RESERVED_REQUEST_KEYS.intersection(extra_body))
        if conflicts:
            raise RuntimeProfileError(
                "Final model request extra_body contains reserved fields: "
                + ", ".join(conflicts)
            )
        unsupported = sorted(set(extra_body) - PROFILE_ALLOWED_EXTRA_BODY_KEYS)
        if unsupported:
            raise RuntimeProfileError(
                "Final model request extra_body contains fields outside the "
                "runtime profile allowlist: " + ", ".join(unsupported)
            )

    mode_forbidden = {
        "chat_completions": {"input", "instructions", "functions", "function_call", "store"},
        "codex_responses": {"messages", "functions", "function_call"},
    }.get(api_mode, set())
    conflicts = sorted(mode_forbidden.intersection(request))
    if conflicts:
        raise RuntimeProfileError(
            f"Final {api_mode} request contains fields owned by another transport: "
            + ", ".join(conflicts)
        )

    selected_tool_name: str | None = None
    tool_choice = request.get("tool_choice")
    if isinstance(tool_choice, str):
        if tool_choice not in {"auto", "none", "required"}:
            raise RuntimeProfileError(
                f"Final model request selects a disabled tool: {tool_choice}"
            )
    elif isinstance(tool_choice, Mapping):
        if api_mode == "chat_completions":
            function = tool_choice.get("function")
            valid_shape = (
                set(tool_choice) == {"type", "function"}
                and tool_choice.get("type") == "function"
                and isinstance(function, Mapping)
                and set(function) == {"name"}
            )
            selected_name = function.get("name") if valid_shape else None
        else:
            valid_shape = (
                set(tool_choice) == {"type", "name"}
                and tool_choice.get("type") == "function"
            )
            selected_name = tool_choice.get("name") if valid_shape else None
        if not isinstance(selected_name, str) or not selected_name.strip():
            raise RuntimeProfileError(
                "Final model request contains a malformed tool_choice"
            )
        selected_tool_name = selected_name.strip()
        if selected_tool_name not in profile.expected_tool_set:
            raise RuntimeProfileError(
                f"Final model request selects a disabled tool: {selected_tool_name}"
            )
    elif tool_choice is not None:
        raise RuntimeProfileError("Final model request contains a malformed tool_choice")

    tools = request.get("tools")
    if tools is None:
        if selected_tool_name is not None or tool_choice == "required":
            raise RuntimeProfileError(
                "Final model request tool_choice requires a tool that is not present"
            )
        return
    if not isinstance(tools, list):
        raise RuntimeProfileError("Final model request tools must be a list")

    tool_names: list[str] = []
    for item in tools:
        name = None
        if isinstance(item, Mapping):
            if api_mode == "chat_completions":
                function = item.get("function")
                if (
                    set(item) == {"type", "function"}
                    and item.get("type") == "function"
                    and isinstance(function, Mapping)
                    and set(function).issubset(
                        {"description", "name", "parameters", "strict"}
                    )
                ):
                    name = function.get("name")
            elif api_mode == "codex_responses":
                if (
                    item.get("type") == "function"
                    and set(item).issubset(
                        {"description", "name", "parameters", "strict", "type"}
                    )
                ):
                    name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeProfileError(
                f"Final {api_mode} request contains a malformed tool definition"
            )
        tool_names.append(name.strip())

    duplicates = sorted(
        name for name in set(tool_names) if tool_names.count(name) > 1
    )
    unexpected = sorted(set(tool_names) - profile.expected_tool_set)
    if duplicates or unexpected:
        details = []
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        if duplicates:
            details.append("duplicates: " + ", ".join(duplicates))
        raise RuntimeProfileError(
            "Final model request violates the runtime profile tool ceiling ("
            + "; ".join(details)
            + ")"
        )
    if tool_choice == "required" and not tool_names:
        raise RuntimeProfileError(
            "Final model request tool_choice requires a tool that is not present"
        )
    if selected_tool_name is not None and selected_tool_name not in tool_names:
        raise RuntimeProfileError(
            "Final model request tool_choice selects a tool that is not present: "
            + selected_tool_name
        )

def validate_model_runtime(
    provider: str | None,
    api_mode: str | None = None,
    *,
    label: str = "Model runtime",
) -> None:
    """Reject a provider/transport pair outside the process profile.

    ``api_mode`` may be omitted while a caller is still resolving its
    transport. Callers must validate again once the effective mode is known.
    """
    profile = get_runtime_profile()
    if profile is None:
        return

    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in profile.model_providers:
        raise RuntimeProfileError(
            f"{label} provider {normalized_provider!r} is not allowed by "
            f"runtime profile {profile.name!r}"
        )

    normalized_mode = str(api_mode or "").strip()
    if normalized_mode and normalized_mode not in profile.api_modes:
        raise RuntimeProfileError(
            f"{label} API mode {normalized_mode!r} is not allowed by "
            f"runtime profile {profile.name!r}"
        )


def automatic_installs_disabled() -> bool:
    """Return the process-wide automatic-install policy."""
    if os.environ.get("HERMES_DISABLE_LAZY_INSTALLS") == "1":
        return True
    profile = get_runtime_profile()
    return profile is not None and not profile.allow_lazy_installs
