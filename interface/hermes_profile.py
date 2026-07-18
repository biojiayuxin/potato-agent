from __future__ import annotations

import os
import ipaddress
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME_PROFILE_PATH = REPO_ROOT / "hermes-lite" / "runtime-profile.yaml"
DEFAULT_ACTIVATION_RUNTIME_PROFILE_PATH = Path(
    "/opt/potato-hermes-lite/current/config/runtime-profile.yaml"
)
DEFAULT_HERMES_LITE_CURRENT = Path("/opt/potato-hermes-lite/current")
DEFAULT_HERMES_LITE_PYTHON = DEFAULT_HERMES_LITE_CURRENT / "venv/bin/python3"
DEFAULT_HERMES_LITE_EXECUTABLE = DEFAULT_HERMES_LITE_CURRENT / "venv/bin/hermes"
DEFAULT_BUNDLED_SKILLS_PATH = DEFAULT_HERMES_LITE_CURRENT / "share/hermes/skills"
DEFAULT_OPTIONAL_SKILLS_PATH = (
    DEFAULT_HERMES_LITE_CURRENT / "share/hermes/optional-skills"
)
DEFAULT_AGENT_BROWSER_BIN_DIR = DEFAULT_HERMES_LITE_CURRENT / "browser/bin"
DEFAULT_AGENT_BROWSER_EXECUTABLE = (
    DEFAULT_HERMES_LITE_CURRENT / "browser/chrome/chrome-linux64/chrome"
)
DEFAULT_BUNDLED_PLUGINS_DIR = REPO_ROOT / "hermes-lite" / "plugins"
RUNTIME_PROFILE_PATH_ENV = "HERMES_RUNTIME_PROFILE_PATH"
BUNDLED_SKILLS_ENV = "HERMES_BUNDLED_SKILLS"
OPTIONAL_SKILLS_ENV = "HERMES_OPTIONAL_SKILLS"
DISABLE_LAZY_INSTALLS_ENV = "HERMES_DISABLE_LAZY_INSTALLS"
SKIP_NODE_BOOTSTRAP_ENV = "HERMES_SKIP_NODE_BOOTSTRAP"
DISABLE_GATEWAY_PLATFORMS_ENV = "HERMES_DISABLE_GATEWAY_PLATFORMS"
DISABLE_MCP_ENV = "HERMES_DISABLE_MCP"
DISABLE_CRON_ENV = "HERMES_DISABLE_CRON"
DISABLE_KANBAN_ENV = "HERMES_DISABLE_KANBAN"
TERMINAL_BACKEND_ENV = "TERMINAL_ENV"
BROWSER_ENGINE_ENV = "AGENT_BROWSER_ENGINE"
BROWSER_CDP_URL_ENV = "BROWSER_CDP_URL"
CAMOFOX_URL_ENV = "CAMOFOX_URL"
AGENT_BROWSER_BIN_DIR_ENV = "HERMES_AGENT_BROWSER_BIN_DIR"
AGENT_BROWSER_EXECUTABLE_PATH_ENV = "AGENT_BROWSER_EXECUTABLE_PATH"

_PROFILE_TOP_LEVEL_KEYS = {
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
_GENERAL_PLUGIN_SCAN_SKIP = {
    "memory",
    "context_engine",
    "platforms",
    "model-providers",
}
_PROFILE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
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


class HermesRuntimeProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class HermesRuntimeProfile:
    schema_version: int
    name: str
    revision: int
    enabled_toolsets: tuple[str, ...]
    disabled_toolsets: tuple[str, ...]
    expected_tools: tuple[str, ...]
    allow_user_plugins: bool
    allow_project_plugins: bool
    allow_entrypoint_plugins: bool
    allowed_general_plugin_keys: tuple[str, ...]
    forbidden_plugin_kinds: tuple[str, ...]
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


def runtime_profile_path() -> Path:
    configured = os.getenv(RUNTIME_PROFILE_PATH_ENV, "").strip()
    if not configured:
        return DEFAULT_RUNTIME_PROFILE_PATH
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _absolute_profile_path(value: str | Path) -> Path:
    rendered = str(value).strip()
    if not rendered or any(character.isspace() for character in rendered):
        raise HermesRuntimeProfileError(
            "Hermes runtime activation profile path must be non-empty and contain no whitespace."
        )
    path = Path(rendered).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    absolute = Path(os.path.abspath(path))
    if Path("/opt") not in absolute.parents:
        raise HermesRuntimeProfileError(
            "Hermes runtime activation profile must be under /opt."
        )
    return absolute


def activation_runtime_profile_path(
    configured: str | Path | None = None,
) -> Path:
    """Resolve the required profile used by every Potato Hermes launcher."""
    if configured is None:
        configured = DEFAULT_ACTIVATION_RUNTIME_PROFILE_PATH
    return _absolute_profile_path(configured)


def local_browser_cdp_url(value: Any) -> str:
    """Validate an already-resolved loopback DevTools WebSocket URL."""
    rendered = str(value or "").strip()
    if not rendered:
        return ""
    if any(character.isspace() for character in rendered):
        raise HermesRuntimeProfileError(
            "hermes.browser_cdp_url must not contain whitespace."
        )
    try:
        parsed = urlsplit(rendered)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme.lower() not in {"ws", "wss"}:
            raise ValueError
        if not hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.port is None:
            raise ValueError
        if not parsed.path.startswith("/devtools/"):
            raise ValueError
        if parsed.query or parsed.fragment:
            raise ValueError
        # The endpoint must already be resolved.  Accepting ``localhost``
        # would defer resolution to the WebSocket client and let hosts/DNS
        # policy change the destination after this validation step.
        if not ipaddress.ip_address(hostname).is_loopback:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise HermesRuntimeProfileError(
            "hermes.browser_cdp_url must be an already-resolved loopback "
            "ws:// or wss:// DevTools endpoint."
        ) from exc
    return rendered


def runtime_profile_environment(
    *,
    profile_path: str | Path | None = None,
    browser_cdp_url: str = "",
    bundled_skills_path: str | Path = DEFAULT_BUNDLED_SKILLS_PATH,
    optional_skills_path: str | Path = DEFAULT_OPTIONAL_SKILLS_PATH,
    agent_browser_bin_dir: str | Path = DEFAULT_AGENT_BROWSER_BIN_DIR,
    agent_browser_executable: str | Path = DEFAULT_AGENT_BROWSER_EXECUTABLE,
) -> dict[str, str]:
    env = {
        DISABLE_LAZY_INSTALLS_ENV: "1",
        SKIP_NODE_BOOTSTRAP_ENV: "1",
        DISABLE_GATEWAY_PLATFORMS_ENV: "1",
        DISABLE_MCP_ENV: "1",
        DISABLE_CRON_ENV: "1",
        DISABLE_KANBAN_ENV: "1",
        TERMINAL_BACKEND_ENV: "local",
        BROWSER_ENGINE_ENV: "chrome",
        BROWSER_CDP_URL_ENV: local_browser_cdp_url(browser_cdp_url),
        CAMOFOX_URL_ENV: "",
        BUNDLED_SKILLS_ENV: str(bundled_skills_path),
        OPTIONAL_SKILLS_ENV: str(optional_skills_path),
        AGENT_BROWSER_BIN_DIR_ENV: str(agent_browser_bin_dir),
        AGENT_BROWSER_EXECUTABLE_PATH_ENV: str(agent_browser_executable),
    }
    env[RUNTIME_PROFILE_PATH_ENV] = str(
        activation_runtime_profile_path(profile_path)
    )
    return env


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HermesRuntimeProfileError(f"{field} must be a mapping.")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if unknown:
        details.append(f"unknown {', '.join(unknown)}")
    raise HermesRuntimeProfileError(f"{field} has invalid keys: {'; '.join(details)}.")


def _integer(value: Any, field: str, *, expected: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HermesRuntimeProfileError(f"{field} must be an integer.")
    if expected is not None and value != expected:
        raise HermesRuntimeProfileError(f"{field} must be {expected}.")
    if value < 1:
        raise HermesRuntimeProfileError(f"{field} must be positive.")
    return value


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise HermesRuntimeProfileError(f"{field} must be a boolean.")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HermesRuntimeProfileError(f"{field} must be a non-empty string.")
    return value.strip()


def _string_list(value: Any, field: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise HermesRuntimeProfileError(f"{field} must be a list.")
    items = tuple(_string(item, f"{field}[{index}]") for index, item in enumerate(value))
    if not allow_empty and not items:
        raise HermesRuntimeProfileError(f"{field} must not be empty.")
    if len(set(items)) != len(items):
        raise HermesRuntimeProfileError(f"{field} must not contain duplicates.")
    return items


def _require_value(value: Any, expected: Any, field: str) -> None:
    if value != expected:
        raise HermesRuntimeProfileError(f"{field} must be {expected!r}.")


def parse_runtime_profile(data: Any) -> HermesRuntimeProfile:
    root = _mapping(data, "runtime profile")
    _exact_keys(root, _PROFILE_TOP_LEVEL_KEYS, "runtime profile")

    schema_version = _integer(root["schema_version"], "schema_version", expected=1)
    name = _string(root["name"], "name")
    if _PROFILE_NAME_RE.fullmatch(name) is None:
        raise HermesRuntimeProfileError(
            "name must start with a lowercase letter and contain only lowercase "
            "letters, digits, underscores, or hyphens."
        )
    revision = _integer(root["revision"], "revision")

    toolsets = _mapping(root["toolsets"], "toolsets")
    _exact_keys(toolsets, {"enabled", "disabled"}, "toolsets")
    enabled_toolsets = _string_list(toolsets["enabled"], "toolsets.enabled", allow_empty=False)
    disabled_toolsets = _string_list(toolsets["disabled"], "toolsets.disabled")
    overlap = sorted(set(enabled_toolsets) & set(disabled_toolsets))
    if overlap:
        raise HermesRuntimeProfileError(
            f"toolsets.enabled and toolsets.disabled overlap: {', '.join(overlap)}."
        )
    expected_tools = _string_list(root["expected_tools"], "expected_tools", allow_empty=False)
    _require_value(
        enabled_toolsets,
        POTATO_ENABLED_TOOLSETS,
        "toolsets.enabled",
    )
    _require_value(
        expected_tools,
        POTATO_EXPECTED_TOOLS,
        "expected_tools",
    )

    plugins = _mapping(root["plugins"], "plugins")
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
    allow_user_plugins = _boolean(plugins["allow_user"], "plugins.allow_user")
    allow_project_plugins = _boolean(plugins["allow_project"], "plugins.allow_project")
    allow_entrypoint_plugins = _boolean(
        plugins["allow_entrypoint"], "plugins.allow_entrypoint"
    )
    allowed_general_plugin_keys = _string_list(
        plugins["allowed_general_keys"], "plugins.allowed_general_keys"
    )
    forbidden_plugin_kinds = _string_list(
        plugins["forbidden_kinds"], "plugins.forbidden_kinds", allow_empty=False
    )

    providers = _mapping(root["providers"], "providers")
    _exact_keys(
        providers,
        {"model", "api_modes", "browser", "memory", "context_engine", "web"},
        "providers",
    )
    model_providers = _string_list(providers["model"], "providers.model", allow_empty=False)
    api_modes = _string_list(providers["api_modes"], "providers.api_modes", allow_empty=False)
    browser_provider = _string(providers["browser"], "providers.browser")
    memory_provider = _string(providers["memory"], "providers.memory")
    context_engine = _string(providers["context_engine"], "providers.context_engine")
    web_providers = _string_list(providers["web"], "providers.web")

    mcp = _mapping(root["mcp"], "mcp")
    _exact_keys(mcp, {"enabled"}, "mcp")
    mcp_enabled = _boolean(mcp["enabled"], "mcp.enabled")

    runtime = _mapping(root["runtime"], "runtime")
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
    allow_lazy_installs = _boolean(
        runtime["allow_lazy_installs"], "runtime.allow_lazy_installs"
    )
    lsp_enabled = _boolean(runtime["lsp_enabled"], "runtime.lsp_enabled")
    lsp_install_strategy = _string(
        runtime["lsp_install_strategy"], "runtime.lsp_install_strategy"
    )
    terminal_backend = _string(runtime["terminal_backend"], "runtime.terminal_backend")
    skills_dependency_strategy = _string(
        runtime["skills_dependency_strategy"], "runtime.skills_dependency_strategy"
    )

    # Version 1 deliberately describes the sealed Potato policy. Rejecting
    # looser values makes a profile edit fail closed instead of silently
    # turning a deployment guard into documentation only.
    _require_value(model_providers, ("custom",), "providers.model")
    if not set(api_modes).issubset({"codex_responses", "chat_completions"}):
        raise HermesRuntimeProfileError(
            "providers.api_modes contains an unsupported transport."
        )
    _require_value(browser_provider, "local", "providers.browser")
    _require_value(memory_provider, "builtin", "providers.memory")
    _require_value(context_engine, "compressor", "providers.context_engine")
    _require_value(web_providers, (), "providers.web")
    _require_value(mcp_enabled, False, "mcp.enabled")
    _require_value(allow_lazy_installs, False, "runtime.allow_lazy_installs")
    _require_value(lsp_install_strategy, "manual", "runtime.lsp_install_strategy")
    _require_value(terminal_backend, "local", "runtime.terminal_backend")
    _require_value(
        skills_dependency_strategy,
        "user_managed",
        "runtime.skills_dependency_strategy",
    )
    _require_value(allow_user_plugins, False, "plugins.allow_user")
    _require_value(allow_project_plugins, False, "plugins.allow_project")
    _require_value(allow_entrypoint_plugins, False, "plugins.allow_entrypoint")
    _require_value(allowed_general_plugin_keys, (), "plugins.allowed_general_keys")
    if "platform" not in forbidden_plugin_kinds:
        raise HermesRuntimeProfileError(
            "plugins.forbidden_kinds must include 'platform'."
        )

    return HermesRuntimeProfile(
        schema_version=schema_version,
        name=name,
        revision=revision,
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
        expected_tools=expected_tools,
        allow_user_plugins=allow_user_plugins,
        allow_project_plugins=allow_project_plugins,
        allow_entrypoint_plugins=allow_entrypoint_plugins,
        allowed_general_plugin_keys=allowed_general_plugin_keys,
        forbidden_plugin_kinds=forbidden_plugin_kinds,
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


def load_runtime_profile(path: Path | None = None) -> HermesRuntimeProfile:
    profile_path = Path(path) if path is not None else runtime_profile_path()
    try:
        data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HermesRuntimeProfileError(
            f"Hermes runtime profile not found: {profile_path}"
        ) from exc
    except (OSError, yaml.YAMLError) as exc:
        raise HermesRuntimeProfileError(
            f"Unable to read Hermes runtime profile {profile_path}: {exc}"
        ) from exc
    return parse_runtime_profile(data)


def _read_plugin_manifest(path: Path, *, prefix: str) -> tuple[str, str]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise HermesRuntimeProfileError(f"Unable to read plugin manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise HermesRuntimeProfileError(f"Plugin manifest {path} must be a mapping.")
    plugin_dir = path.parent
    name = _string(data.get("name", plugin_dir.name), f"{path}: name")
    kind = str(data.get("kind", "standalone")).strip().lower()
    key = f"{prefix}/{plugin_dir.name}" if prefix else name
    return key, kind


def _scan_plugin_directory(
    root: Path,
    *,
    prefix: str = "",
    skip_names: set[str] | None = None,
    depth: int = 0,
) -> list[tuple[str, str]]:
    if not root.is_dir():
        raise HermesRuntimeProfileError(f"Bundled plugin directory not found: {root}")
    found: list[tuple[str, str]] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        if depth == 0 and skip_names and child.name in skip_names:
            continue
        manifest = child / "plugin.yaml"
        if not manifest.is_file():
            manifest = child / "plugin.yml"
        if manifest.is_file():
            found.append(_read_plugin_manifest(manifest, prefix=prefix))
            continue
        if depth >= 1:
            continue
        child_prefix = f"{prefix}/{child.name}" if prefix else child.name
        found.extend(
            _scan_plugin_directory(child, prefix=child_prefix, depth=depth + 1)
        )
    return found


def bundled_plugin_disable_keys(
    profile: HermesRuntimeProfile,
    plugins_dir: Path | None = None,
) -> tuple[str, ...]:
    root = Path(plugins_dir) if plugins_dir is not None else DEFAULT_BUNDLED_PLUGINS_DIR
    manifests = _scan_plugin_directory(root, skip_names=_GENERAL_PLUGIN_SCAN_SKIP)
    platforms_root = root / "platforms"
    if platforms_root.is_dir():
        manifests.extend(_scan_plugin_directory(platforms_root))

    seen: dict[str, str] = {}
    for key, kind in manifests:
        previous = seen.get(key)
        if previous is not None:
            raise HermesRuntimeProfileError(
                f"Bundled plugin key {key!r} is duplicated ({previous}, {kind})."
            )
        seen[key] = kind

    allowed = set(profile.allowed_general_plugin_keys)
    forbidden_kinds = set(profile.forbidden_plugin_kinds)
    return tuple(
        sorted(
            key
            for key, kind in seen.items()
            if kind in forbidden_kinds or (kind == "backend" and key not in allowed)
        )
    )


def _managed_section(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if value is None:
        value = {}
        config[key] = value
    if not isinstance(value, dict):
        raise HermesRuntimeProfileError(f"Hermes config field {key} must be a mapping.")
    return value


def _config_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise HermesRuntimeProfileError(f"Hermes config field {field} must be a string list.")
    return [item.strip() for item in value]


def _ordered_union(*values: list[str] | tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(item for group in values for item in group))


def apply_runtime_profile(
    config: dict[str, Any],
    *,
    profile: HermesRuntimeProfile | None = None,
    bundled_plugins_dir: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise HermesRuntimeProfileError("Hermes config must be a mapping.")
    active_profile = profile or load_runtime_profile()
    result = deepcopy(config)

    platform_toolsets = _managed_section(result, "platform_toolsets")
    platform_toolsets["cli"] = [*active_profile.enabled_toolsets, "no_mcp"]

    agent = _managed_section(result, "agent")
    existing_disabled = _config_string_list(
        agent.get("disabled_toolsets"), "agent.disabled_toolsets"
    )
    agent["disabled_toolsets"] = _ordered_union(
        existing_disabled, active_profile.disabled_toolsets
    )

    security = _managed_section(result, "security")
    security["allow_lazy_installs"] = active_profile.allow_lazy_installs

    lsp = _managed_section(result, "lsp")
    existing_lsp_enabled = lsp.get("enabled", active_profile.lsp_enabled)
    if not isinstance(existing_lsp_enabled, bool):
        raise HermesRuntimeProfileError("Hermes config field lsp.enabled must be a boolean.")
    # A user may close LSP, but cannot reopen it if a future profile closes it.
    lsp["enabled"] = active_profile.lsp_enabled and existing_lsp_enabled
    lsp["install_strategy"] = active_profile.lsp_install_strategy

    terminal = _managed_section(result, "terminal")
    terminal["backend"] = active_profile.terminal_backend

    browser = _managed_section(result, "browser")
    browser["cloud_provider"] = active_profile.browser_provider
    browser["cdp_url"] = ""
    browser["engine"] = "chrome"

    memory = _managed_section(result, "memory")
    memory["provider"] = "" if active_profile.memory_provider == "builtin" else active_profile.memory_provider

    context = _managed_section(result, "context")
    context["engine"] = active_profile.context_engine

    gateway = _managed_section(result, "gateway")
    gateway["platforms"] = {}

    web = _managed_section(result, "web")
    if not active_profile.web_providers:
        web["backend"] = ""
        web["search_backend"] = ""
        web["extract_backend"] = ""

    auxiliary_api_mode = active_profile.api_modes[0]
    model = result.get("model")
    if model is not None:
        if not isinstance(model, dict):
            raise HermesRuntimeProfileError("Hermes config field model must be a mapping.")
        model["provider"] = active_profile.model_providers[0]
        if model.get("api_mode") not in active_profile.api_modes:
            model["api_mode"] = active_profile.api_modes[0]
        auxiliary_api_mode = model["api_mode"]

    result.pop("fallback_providers", None)
    result.pop("fallback_model", None)

    auxiliary = result.get("auxiliary")
    if auxiliary is not None:
        if not isinstance(auxiliary, dict):
            raise HermesRuntimeProfileError(
                "Hermes config field auxiliary must be a mapping."
            )
        for task_name, task_config in auxiliary.items():
            if not isinstance(task_config, dict):
                raise HermesRuntimeProfileError(
                    f"Hermes config field auxiliary.{task_name} must be a mapping."
                )
            task_config["provider"] = active_profile.model_providers[0]
            task_config["base_url"] = ""
            task_config["api_key"] = ""
            task_config["fallback_chain"] = []
            if task_config.get("api_mode") not in active_profile.api_modes:
                task_config["api_mode"] = auxiliary_api_mode

    result["mcp_servers"] = {}

    plugins = _managed_section(result, "plugins")
    existing_plugin_disabled = _config_string_list(
        plugins.get("disabled"), "plugins.disabled"
    )
    generated_plugin_disabled = bundled_plugin_disable_keys(
        active_profile, plugins_dir=bundled_plugins_dir
    )
    plugins["enabled"] = []
    plugins["disabled"] = _ordered_union(
        existing_plugin_disabled, generated_plugin_disabled
    )
    return result
