from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

import yaml

from interface.subprocess_env import interface_subprocess_env

from interface.file_browser_policy import DEFAULT_PUBLIC_DATA_PATH
from interface.hermes_profile import (
    DEFAULT_HERMES_LITE_EXECUTABLE,
    apply_runtime_profile,
    runtime_profile_environment,
)
from interface.mapping import HermesTarget, resolve_env_placeholders
from interface.model_options import (
    DEFAULT_REASONING_EFFORT,
    normalize_model_options,
    strip_openai_api_key_env,
)
from interface.model_proxy_config import (
    get_model_proxy_base_url,
    local_model_proxy_token,
)
from interface.user_private_files import (
    prepare_user_runtime_directories,
    repair_user_private_file,
    replace_user_private_tree,
    write_user_private_text,
)


ROOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ROOT_DIR.parent
DEFAULT_HERMES_BIN = str(DEFAULT_HERMES_LITE_EXECUTABLE)
DEFAULT_SERVICE_RESTART = "always"
DEFAULT_SERVICE_RESTART_SEC = 3
DEFAULT_TERMINAL_TIMEOUT = 180
DEFAULT_APPROVAL_MODE = "smart"
DEFAULT_RUNTIME_READY_TIMEOUT = 45
DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT = 180
DEFAULT_GATEWAY_STOP_GRACE_SECONDS = 30
DEFAULT_ENVIRONMENT_HINT = (
    "When official GitHub URLs are inaccessible or cloning is slow, consider "
    "using a GitHub proxy in mainland China to accelerate access. "
    "Before installing software for a bioinformatics task, check the system "
    "environment (including PATH) and the shared environments under "
    "`/opt/potato-bio/current`; run shared tools with "
    "`potato-bio-run ENV COMMAND ...`, and consider installation only if neither "
    "source provides what is needed. When returning paths to any result files "
    "after completing a task, always provide their full absolute paths."
)
DEFAULT_RUNTIME_LOCK_DIR = Path("/run/potato-agent/runtime-start")
DEFAULT_SOUL_TEMPLATE_PATH = REPO_ROOT / "soul_settings" / "SOUL.md"
REQUIRED_INACCESSIBLE_PATHS = (
    "/srv/potato_agent",
    "/var/lib/potato-agent",
    "/etc/potato-agent",
    "/opt/interface-env",
)
# Kept as a compatibility alias for callers that treated these as defaults.
DEFAULT_INACCESSIBLE_PATHS = REQUIRED_INACCESSIBLE_PATHS
AGENT_SERVICE_PRIORITY_DIRECTIVES = (
    "CPUWeight=1000",
    "IOWeight=10000",
    "Nice=-5",
    "IOSchedulingClass=best-effort",
    "IOSchedulingPriority=0",
)
MANAGED_BIOINFORMATICS_SKILLS_DIR_NAME = "potato-knowledge-bioinformatics"
DEFAULT_BIOINFORMATICS_SKILLS_PATH = (
    REPO_ROOT / "skills" / MANAGED_BIOINFORMATICS_SKILLS_DIR_NAME
)
MANAGED_PLAN_MODE_SKILLS_DIR_NAME = "plan-mode"
DEFAULT_PLAN_MODE_SKILLS_PATH = REPO_ROOT / "skills" / MANAGED_PLAN_MODE_SKILLS_DIR_NAME
PUBLIC_DATA_LINK_NAME = "public_data"
SYSTEMCTL_STOP_TIMEOUT_SECONDS = 120.0
SYSTEMCTL_QUERY_TIMEOUT_SECONDS = 30.0
VALID_SERVICE_RESTART_VALUES = frozenset(
    {
        "no",
        "on-success",
        "on-failure",
        "on-abnormal",
        "on-watchdog",
        "on-abort",
        "always",
    }
)
_SYSTEMD_ACCOUNT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*\$?$")


def _systemd_scalar(value: Any, field: str, *, allow_whitespace: bool = False) -> str:
    rendered = str(value)
    if not rendered:
        raise RuntimeError(f"{field} must not be empty.")
    if (
        len(rendered.splitlines()) != 1
        or any(ord(character) < 32 or ord(character) == 127 for character in rendered)
    ):
        raise RuntimeError(f"{field} must not contain control characters.")
    if not allow_whitespace and any(character.isspace() for character in rendered):
        raise RuntimeError(f"{field} must not contain whitespace.")
    return rendered


def _systemd_absolute_path(value: Any, field: str) -> str:
    rendered = _systemd_scalar(value, field)
    if not Path(rendered).is_absolute():
        raise RuntimeError(f"{field} must be an absolute path.")
    return rendered


def _run_command(
    command: list[str], *, timeout_seconds: float | None = None
) -> str:
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "check": False,
        "env": interface_subprocess_env(),
    }
    if timeout_seconds is not None:
        run_kwargs["timeout"] = timeout_seconds
    result = subprocess.run(command, **run_kwargs)
    if result.returncode != 0:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit code {result.returncode}"
        )
        raise RuntimeError(f"Command failed ({' '.join(command)}): {detail}")
    return result.stdout


def _run_command_result(
    command: list[str], *, timeout_seconds: float | None = None
) -> subprocess.CompletedProcess[str]:
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "check": False,
        "env": interface_subprocess_env(),
    }
    if timeout_seconds is not None:
        run_kwargs["timeout"] = timeout_seconds
    return subprocess.run(command, **run_kwargs)


def require_root() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("This script must be run as root.")


def user_runtime_temp_dir(user: HermesTarget) -> Path:
    return user.hermes_home / "tmp"


def ensure_user_runtime_temp_dir(user: HermesTarget) -> Path:
    """Create and verify the gateway temp root as the target Linux user.

    Running ``install`` after dropping privileges avoids a root-level symlink
    traversal if an existing user has replaced the expected path.  The
    read-only lstat checks then make the ownership and mode contract explicit.
    """
    temp_dir = user_runtime_temp_dir(user)
    _run_command(
        [
            "runuser",
            "-u",
            user.linux_user,
            "--",
            "install",
            "-d",
            "-m",
            "0700",
            "--",
            str(temp_dir),
        ]
    )
    try:
        pw = pwd.getpwnam(user.linux_user)
        temp_stat = temp_dir.lstat()
    except (KeyError, OSError) as exc:
        raise RuntimeError("Failed to verify the user runtime temp directory.") from exc
    if (
        not stat.S_ISDIR(temp_stat.st_mode)
        or temp_stat.st_uid != pw.pw_uid
        or temp_stat.st_gid != pw.pw_gid
        or stat.S_IMODE(temp_stat.st_mode) != 0o700
    ):
        raise RuntimeError("User runtime temp directory has unsafe ownership or mode.")
    return temp_dir


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def _strip_api_keys_outside_model(
    value: Any, *, protected_model: dict[str, Any] | None = None
) -> None:
    if isinstance(value, dict):
        if value is not protected_model:
            value.pop("api_key", None)
        for child in list(value.values()):
            _strip_api_keys_outside_model(child, protected_model=protected_model)
    elif isinstance(value, list):
        for child in value:
            _strip_api_keys_outside_model(child, protected_model=protected_model)


def ensure_linux_user(username: str) -> None:
    result = subprocess.run(
        ["id", "-u", username],
        capture_output=True,
        text=True,
        env=interface_subprocess_env(),
    )
    if result.returncode == 0:
        return
    _run_command(["useradd", "-m", "-s", "/bin/bash", username])


def get_linux_user_info(username: str) -> dict[str, Any]:
    import pwd

    try:
        pw = pwd.getpwnam(username)
    except KeyError as exc:
        raise RuntimeError(f"Linux user {username!r} does not exist.") from exc
    return {
        "username": pw.pw_name,
        "uid": pw.pw_uid,
        "gid": pw.pw_gid,
        "home_dir": Path(pw.pw_dir).resolve(),
        "shell": pw.pw_shell,
    }


def _set_owner_and_mode(path: Path, uid: int, gid: int, mode: int) -> None:
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def install_soul_file(user: HermesTarget, *, password_entry: Any) -> None:
    if not DEFAULT_SOUL_TEMPLATE_PATH.is_file():
        raise RuntimeError(f"SOUL template not found: {DEFAULT_SOUL_TEMPLATE_PATH}")

    soul_path = user.hermes_home / "SOUL.md"
    write_user_private_text(
        user,
        soul_path,
        DEFAULT_SOUL_TEMPLATE_PATH.read_text(encoding="utf-8"),
    )


def _install_managed_skill_dir(
    user: HermesTarget,
    *,
    password_entry: Any,
    source_path: Path,
    target_dir_name: str,
) -> None:
    if not source_path.is_dir():
        raise RuntimeError(f"Managed skills not found: {source_path}")
    replace_user_private_tree(
        user,
        password_entry,
        source=source_path,
        destination=user.hermes_home / "skills" / target_dir_name,
    )


def install_managed_skills(user: HermesTarget, *, password_entry: Any) -> None:
    for target_dir_name, source_path in (
        (MANAGED_BIOINFORMATICS_SKILLS_DIR_NAME, DEFAULT_BIOINFORMATICS_SKILLS_PATH),
        (MANAGED_PLAN_MODE_SKILLS_DIR_NAME, DEFAULT_PLAN_MODE_SKILLS_PATH),
    ):
        _install_managed_skill_dir(
            user,
            password_entry=password_entry,
            source_path=source_path,
            target_dir_name=target_dir_name,
        )


def install_public_data_link(user: HermesTarget, *, uid: int, gid: int) -> None:
    if not DEFAULT_PUBLIC_DATA_PATH.exists():
        raise RuntimeError(f"Public data path not found: {DEFAULT_PUBLIC_DATA_PATH}")
    if not DEFAULT_PUBLIC_DATA_PATH.is_dir():
        raise RuntimeError(
            f"Public data path is not a directory: {DEFAULT_PUBLIC_DATA_PATH}"
        )

    link_path = user.home_dir / PUBLIC_DATA_LINK_NAME
    expected_target = DEFAULT_PUBLIC_DATA_PATH.resolve()
    if link_path.is_symlink():
        if link_path.resolve() != expected_target:
            raise RuntimeError(
                f"Public data link already exists but points elsewhere: {link_path}"
            )
    elif link_path.exists():
        raise RuntimeError(
            f"Cannot create public data link because path already exists: {link_path}"
        )
    else:
        link_path.symlink_to(DEFAULT_PUBLIC_DATA_PATH)

    try:
        os.lchown(link_path, uid, gid)
    except OSError:
        pass


def build_env_content(user: HermesTarget) -> str:
    lines = [
        f"# Interface user: {user.email or user.display_name or user.username}",
    ]
    for key, value in user.extra_env.items():
        rendered = (
            repr(value)
            if not str(value)
            .replace("_", "")
            .replace("/", "")
            .replace(".", "")
            .replace(":", "")
            .replace("-", "")
            .isalnum()
            else str(value)
        )
        lines.append(f"{key}={rendered}")
    return "\n".join(lines).rstrip() + "\n"


def build_config_data(config: dict[str, Any], user: HermesTarget) -> dict[str, Any]:
    config = resolve_env_placeholders(deepcopy(config), "interface.hermes_config")
    hermes_cfg = config.get("hermes") or {}
    terminal_cfg = deepcopy(hermes_cfg.get("terminal") or {})
    global_overrides = deepcopy(hermes_cfg.get("config_overrides") or {})

    data: dict[str, Any] = {}
    try:
        active_option = normalize_model_options(config).primary
    except Exception:
        model_cfg = deepcopy(hermes_cfg.get("model") or {})
        active_option = None
    if active_option is not None:
        data["model"] = {
            "default": active_option.name,
            "provider": active_option.provider,
            "base_url": get_model_proxy_base_url(config),
            "api_key": local_model_proxy_token(user.username, user.model_proxy_token),
        }
        if active_option.api_mode:
            data["model"]["api_mode"] = active_option.api_mode
        if active_option.context_length is not None:
            data["model"]["context_length"] = active_option.context_length
    elif model_cfg:
        data["model"] = {
            **model_cfg,
            "base_url": get_model_proxy_base_url(config),
            "api_key": local_model_proxy_token(user.username, user.model_proxy_token),
        }
    data["terminal"] = terminal_cfg
    data["agent"] = {
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "environment_hint": DEFAULT_ENVIRONMENT_HINT,
    }
    data["approvals"] = {"mode": DEFAULT_APPROVAL_MODE}

    deep_merge(data, global_overrides)
    deep_merge(data, deepcopy(user.config_overrides))

    model = data.get("model")
    if isinstance(model, dict):
        model["base_url"] = get_model_proxy_base_url(config)
        model["api_key"] = local_model_proxy_token(user.username, user.model_proxy_token)

    terminal = data.setdefault("terminal", {})
    if not isinstance(terminal, dict):
        raise RuntimeError(
            f"terminal config for {user.username} must remain a mapping."
        )

    terminal.setdefault("backend", "local")
    terminal.setdefault("timeout", DEFAULT_TERMINAL_TIMEOUT)
    terminal["cwd"] = str(user.workdir)
    data.pop("fallback_providers", None)
    data.pop("fallback_model", None)
    _strip_api_keys_outside_model(data, protected_model=model if isinstance(model, dict) else None)
    return apply_runtime_profile(data)


def build_systemd_unit(config: dict[str, Any], user: HermesTarget) -> str:
    hermes_cfg = config.get("hermes") or {}
    service_cfg = hermes_cfg.get("service") or {}
    description_template = _systemd_scalar(
        service_cfg.get("description_template") or "Hermes Agent for {display_name}",
        "hermes.service.description_template",
        allow_whitespace=True,
    )
    restart = _systemd_scalar(
        service_cfg.get("restart") or DEFAULT_SERVICE_RESTART,
        "hermes.service.restart",
    )
    if restart not in VALID_SERVICE_RESTART_VALUES:
        raise RuntimeError(
            "hermes.service.restart must be one of: "
            + ", ".join(sorted(VALID_SERVICE_RESTART_VALUES))
        )
    restart_sec = int(service_cfg.get("restart_sec") or DEFAULT_SERVICE_RESTART_SEC)
    timeout_stop_sec = _systemd_timeout_stop_sec(config, user)
    hermes_bin = _systemd_absolute_path(
        hermes_cfg.get("executable") or DEFAULT_HERMES_BIN,
        "hermes.executable",
    )
    configured_inaccessible_paths = service_cfg.get("inaccessible_paths")
    if not isinstance(configured_inaccessible_paths, (list, tuple)):
        configured_inaccessible_paths = ()

    rendered_description = _systemd_scalar(
        description_template.format(
            username=user.username,
            display_name=user.display_name,
            linux_user=user.linux_user,
        ),
        "rendered service description",
        allow_whitespace=True,
    )
    linux_user = _systemd_scalar(user.linux_user, "target.linux_user")
    if _SYSTEMD_ACCOUNT_RE.fullmatch(linux_user) is None:
        raise RuntimeError("target.linux_user is not a valid systemd account name.")
    home_dir = _systemd_absolute_path(user.home_dir, "target.home_dir")
    hermes_home = _systemd_absolute_path(user.hermes_home, "target.hermes_home")
    runtime_temp_dir = _systemd_absolute_path(
        user_runtime_temp_dir(user), "target.runtime_temp_dir"
    )
    inaccessible_path_values = list(
        dict.fromkeys(
            [
                *REQUIRED_INACCESSIBLE_PATHS,
                *[
                    _systemd_absolute_path(
                        path, "hermes.service.inaccessible_paths[]"
                    )
                    for path in configured_inaccessible_paths
                    if str(path).strip()
                ],
            ]
        )
    )
    configured_profile_path = hermes_cfg.get("runtime_profile_path")
    if configured_profile_path is not None and not isinstance(
        configured_profile_path, (str, os.PathLike)
    ):
        raise RuntimeError("hermes.runtime_profile_path must be a filesystem path.")
    runtime_env = runtime_profile_environment(
        profile_path=configured_profile_path,
        browser_cdp_url=str(hermes_cfg.get("browser_cdp_url") or ""),
    )
    runtime_profile_lines = []
    if "HERMES_RUNTIME_PROFILE_PATH" in runtime_env:
        runtime_profile_lines.append(
            "Environment=HERMES_RUNTIME_PROFILE_PATH="
            f"{runtime_env['HERMES_RUNTIME_PROFILE_PATH']}"
        )
    return "\n".join(
        [
            "[Unit]",
            f"Description={rendered_description}",
            "After=network.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={linux_user}",
            f"Group={linux_user}",
            f"WorkingDirectory={home_dir}",
            f"Environment=HOME={home_dir}",
            f"Environment=HERMES_HOME={hermes_home}",
            f"Environment=TMPDIR={runtime_temp_dir}",
            f"Environment=HERMES_DISABLE_LAZY_INSTALLS={runtime_env['HERMES_DISABLE_LAZY_INSTALLS']}",
            f"Environment=HERMES_SKIP_NODE_BOOTSTRAP={runtime_env['HERMES_SKIP_NODE_BOOTSTRAP']}",
            f"Environment=HERMES_DISABLE_GATEWAY_PLATFORMS={runtime_env['HERMES_DISABLE_GATEWAY_PLATFORMS']}",
            f"Environment=HERMES_DISABLE_MCP={runtime_env['HERMES_DISABLE_MCP']}",
            f"Environment=HERMES_DISABLE_CRON={runtime_env['HERMES_DISABLE_CRON']}",
            f"Environment=HERMES_DISABLE_KANBAN={runtime_env['HERMES_DISABLE_KANBAN']}",
            f"Environment=TERMINAL_ENV={runtime_env['TERMINAL_ENV']}",
            f"Environment=AGENT_BROWSER_ENGINE={runtime_env['AGENT_BROWSER_ENGINE']}",
            f"Environment=BROWSER_CDP_URL={runtime_env['BROWSER_CDP_URL']}",
            f"Environment=CAMOFOX_URL={runtime_env['CAMOFOX_URL']}",
            f"Environment=HERMES_BUNDLED_SKILLS={runtime_env['HERMES_BUNDLED_SKILLS']}",
            f"Environment=HERMES_OPTIONAL_SKILLS={runtime_env['HERMES_OPTIONAL_SKILLS']}",
            f"Environment=HERMES_AGENT_BROWSER_BIN_DIR={runtime_env['HERMES_AGENT_BROWSER_BIN_DIR']}",
            f"Environment=AGENT_BROWSER_EXECUTABLE_PATH={runtime_env['AGENT_BROWSER_EXECUTABLE_PATH']}",
            *runtime_profile_lines,
            f"ExecStartPre=/usr/bin/install -d -m 0700 -- {runtime_temp_dir}",
            f"ExecStart={hermes_bin} gateway run --replace",
            *AGENT_SERVICE_PRIORITY_DIRECTIVES,
            "UMask=0077",
            "PrivateTmp=yes",
            "ProtectProc=invisible",
            "ProcSubset=pid",
            "NoNewPrivileges=yes",
            *[
                f"InaccessiblePaths=-{path}"
                for path in inaccessible_path_values
            ],
            f"Restart={restart}",
            f"RestartSec={restart_sec}",
            f"TimeoutStopSec={timeout_stop_sec}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _configured_restart_drain_timeout(config: dict[str, Any], user: HermesTarget) -> int:
    hermes_cfg = config.get("hermes") or {}
    result = DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    for overrides in (hermes_cfg.get("config_overrides"), user.config_overrides):
        if not isinstance(overrides, dict):
            continue
        agent_cfg = overrides.get("agent")
        if not isinstance(agent_cfg, dict):
            continue
        if "restart_drain_timeout" in agent_cfg:
            result = _positive_int(
                agent_cfg.get("restart_drain_timeout"),
                DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT,
            )
    return result


def _systemd_timeout_stop_sec(config: dict[str, Any], user: HermesTarget) -> int:
    hermes_cfg = config.get("hermes") or {}
    service_cfg = hermes_cfg.get("service") or {}
    explicit = service_cfg.get("timeout_stop_sec") if isinstance(service_cfg, dict) else None
    if explicit is not None:
        return _positive_int(explicit, DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT)
    return _configured_restart_drain_timeout(
        config, user
    ) + DEFAULT_GATEWAY_STOP_GRACE_SECONDS


def install_user_runtime_files(config: dict[str, Any], user: HermesTarget) -> None:
    try:
        pw = pwd.getpwnam(user.linux_user)
    except KeyError as exc:
        raise RuntimeError(f"Linux user {user.linux_user!r} does not exist.") from exc

    prepare_user_runtime_directories(user, pw)

    env_path = user.hermes_home / ".env"
    write_user_private_text(
        user,
        env_path,
        strip_openai_api_key_env(build_env_content(user)),
    )

    config_path = user.hermes_home / "config.yaml"
    write_user_private_text(
        user,
        config_path,
        yaml.safe_dump(
            build_config_data(config, user), sort_keys=False, allow_unicode=False
        ),
    )
    install_soul_file(user, password_entry=pw)
    install_managed_skills(user, password_entry=pw)
    install_public_data_link(user, uid=pw.pw_uid, gid=pw.pw_gid)


def install_user_files(config: dict[str, Any], user: HermesTarget) -> None:
    ensure_linux_user(user.linux_user)
    install_user_runtime_files(config, user)
    ensure_user_runtime_temp_dir(user)

    service_path = Path("/etc/systemd/system") / user.systemd_service
    service_path.write_text(build_systemd_unit(config, user), encoding="utf-8")
    os.chmod(service_path, 0o644)

    _run_command(["systemctl", "daemon-reload"])
    # Keep per-user Hermes services disabled by default so the interface can
    # wake them on demand when a user enters the workspace.
    subprocess.run(
        ["systemctl", "disable", user.systemd_service],
        capture_output=True,
        text=True,
        check=False,
        env=interface_subprocess_env(),
    )


def restart_service(service_name: str) -> None:
    _run_command(["systemctl", "restart", service_name])


def start_service(service_name: str) -> None:
    _run_command(["systemctl", "start", service_name])


def stop_service(service_name: str) -> None:
    _run_command(
        ["systemctl", "stop", service_name],
        timeout_seconds=SYSTEMCTL_STOP_TIMEOUT_SECONDS,
    )
    _run_command_result(
        ["systemctl", "reset-failed", service_name],
        timeout_seconds=SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
    )


def is_service_active(service_name: str) -> bool:
    result = _run_command_result(
        ["systemctl", "is-active", service_name],
        timeout_seconds=SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
    )
    return result.returncode == 0 and result.stdout.strip() == "active"


def stop_and_remove_service(service_name: str) -> None:
    subprocess.run(
        ["systemctl", "disable", "--now", service_name],
        capture_output=True,
        text=True,
        env=interface_subprocess_env(),
    )
    service_path = Path("/etc/systemd/system") / service_name
    if service_path.exists():
        service_path.unlink()
    _run_command(["systemctl", "daemon-reload"])


def remove_linux_user(linux_user: str, *, delete_home: bool) -> None:
    result = subprocess.run(
        ["id", "-u", linux_user],
        capture_output=True,
        text=True,
        env=interface_subprocess_env(),
    )
    if result.returncode != 0:
        return
    command = ["userdel"]
    if delete_home:
        command.append("-r")
    command.append(linux_user)
    _run_command(command)


def wait_for_service_active(
    service_name: str,
    *,
    timeout_seconds: int = DEFAULT_RUNTIME_READY_TIMEOUT,
) -> None:
    deadline = time.time() + timeout_seconds
    last_error = "unknown error"
    while time.time() < deadline:
        try:
            if is_service_active(service_name):
                return
            last_error = f"service {service_name} is not active"
        except Exception as exc:
            last_error = str(exc)
            time.sleep(2)
            continue
        time.sleep(1)
    raise RuntimeError(f"Hermes runtime did not become ready in time: {last_error}")


def require_binary(name: str) -> None:
    if not shutil.which(name):
        raise RuntimeError(f"{name} is required.")


def repair_session_db_permissions(user: HermesTarget) -> None:
    try:
        pw = pwd.getpwnam(user.linux_user)
    except KeyError as exc:
        raise RuntimeError(f"Linux user {user.linux_user!r} does not exist.") from exc

    # Ensure the SQLite files, if already created by root-side readers, are
    # returned to the target Linux user before Hermes tries to write again.
    for candidate in (
        user.state_db_path,
        user.state_db_path.with_name(f"{user.state_db_path.name}-wal"),
        user.state_db_path.with_name(f"{user.state_db_path.name}-shm"),
    ):
        repair_user_private_file(user, pw, candidate)


def wait_for_hermes_models(
    api_key: str,
    host: str,
    port: int,
    *,
    timeout_seconds: int = DEFAULT_RUNTIME_READY_TIMEOUT,
) -> dict[str, Any]:
    url = f"http://{host}:{int(port)}/v1/models"
    deadline = time.time() + timeout_seconds
    last_error = "unknown error"

    while time.time() < deadline:
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib_request.Request(url, headers=headers)
        try:
            with urllib_request.urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8", errors="replace")
                if response.status != 200:
                    last_error = f"status {response.status}"
                    time.sleep(1)
                    continue
                payload = json.loads(body or "{}")
                if isinstance(payload.get("data"), list):
                    return payload
                last_error = "response missing models list"
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            last_error = detail or f"HTTP {exc.code}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)

    raise RuntimeError(
        f"Hermes models endpoint did not become ready in time for {url}: {last_error}"
    )


def _collect_service_debug_info(service_name: str) -> str:
    sections: list[str] = []
    commands = [
        (["systemctl", "is-active", service_name], "systemctl is-active"),
        (
            ["systemctl", "status", "--no-pager", "--full", service_name],
            "systemctl status",
        ),
        (
            ["journalctl", "-u", service_name, "-n", "60", "--no-pager"],
            "journalctl -u",
        ),
    ]
    for command, label in commands:
        result = _run_command_result(command)
        output = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        body = output or stderr or f"exit code {result.returncode}"
        sections.append(f"[{label}]\n{body}")
    return "\n\n".join(sections).strip()


@contextlib.contextmanager
def service_operation_lock(service_name: str):
    DEFAULT_RUNTIME_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DEFAULT_RUNTIME_LOCK_DIR, 0o700)
    lock_path = DEFAULT_RUNTIME_LOCK_DIR / f"{service_name}.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def ensure_service_ready(
    user: HermesTarget,
    *,
    timeout_seconds: int = DEFAULT_RUNTIME_READY_TIMEOUT,
) -> dict[str, Any]:
    require_binary("systemctl")
    with service_operation_lock(user.systemd_service):
        repair_session_db_permissions(user)
        was_active = is_service_active(user.systemd_service)
        if not was_active:
            start_service(user.systemd_service)
        try:
            wait_for_service_active(user.systemd_service, timeout_seconds=timeout_seconds)
        except Exception as exc:
            debug_info = _collect_service_debug_info(user.systemd_service)
            detail = (
                f"Failed to start Hermes runtime for {user.username}.\n"
                f"Service: {user.systemd_service}\n"
                f"Error: {exc}"
            )
            if debug_info:
                detail = f"{detail}\n\n{debug_info}"
            raise RuntimeError(detail) from exc

    return {
        "status": "ready",
        "started": not was_active,
        "service_name": user.systemd_service,
    }
