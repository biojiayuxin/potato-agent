from __future__ import annotations

import os
from collections.abc import Mapping


_NON_SECRET_PASSTHROUGH = frozenset(
    {
        "INTERFACE_AUTH_DB",
        "INTERFACE_HELPER_PYTHON",
        "INTERFACE_TUI_GATEWAY_PYTHON",
        "LANG",
        "LC_ALL",
        "POTATO_AGENT_MAPPING_PATH",
        "POTATO_AGENT_REPO_ROOT",
        "TZ",
    }
)

_ALLOWED_EXTRA = frozenset(
    {
        "AGENT_BROWSER_ENGINE",
        "AGENT_BROWSER_EXECUTABLE_PATH",
        "BROWSER_CDP_URL",
        "CAMOFOX_URL",
        "HERMES_AGENT_BROWSER_BIN_DIR",
        "HERMES_BUNDLED_SKILLS",
        "HERMES_DISABLE_CRON",
        "HERMES_DISABLE_GATEWAY_PLATFORMS",
        "HERMES_DISABLE_KANBAN",
        "HERMES_DISABLE_LAZY_INSTALLS",
        "HERMES_DISABLE_MCP",
        "HERMES_HOME",
        "HERMES_OPTIONAL_SKILLS",
        "HERMES_RUNTIME_PROFILE_PATH",
        "HERMES_SKIP_NODE_BOOTSTRAP",
        "HOME",
        "TERMINAL_CWD",
        "TERMINAL_ENV",
    }
)


def interface_subprocess_env(
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONUNBUFFERED": "1",
    }
    for name in _NON_SECRET_PASSTHROUGH:
        value = os.environ.get(name)
        if value:
            env[name] = value
    if extra:
        env.update(
            {
                str(key): str(value)
                for key, value in extra.items()
                if str(key) in _ALLOWED_EXTRA
            }
        )
    return env
