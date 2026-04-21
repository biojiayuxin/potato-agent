from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from interface.mapping import HermesTarget, resolve_env_placeholders


DEFAULT_HERMES_BIN = "/usr/local/bin/hermes"
DEFAULT_SERVICE_RESTART = "always"
DEFAULT_SERVICE_RESTART_SEC = 3
DEFAULT_TERMINAL_TIMEOUT = 180


def _run_command(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit code {result.returncode}"
        )
        raise RuntimeError(f"Command failed ({' '.join(command)}): {detail}")
    return result.stdout


def require_root() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("This script must be run as root.")


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def ensure_linux_user(username: str) -> None:
    result = subprocess.run(["id", "-u", username], capture_output=True, text=True)
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


def build_env_content(user: HermesTarget) -> str:
    lines = [
        f"# Interface user: {user.email or user.display_name or user.username}",
        "API_SERVER_ENABLED=true",
        f"API_SERVER_HOST={user.api_server_host}",
        f"API_SERVER_PORT={user.api_port}",
        f"API_SERVER_KEY={json.dumps(user.api_key) if ' ' in user.api_key else user.api_key}",
        f"API_SERVER_MODEL_NAME={user.api_server_model_name}",
    ]
    for key, value in user.extra_env.items():
        rendered = (
            json.dumps(value)
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
    model_cfg = deepcopy(hermes_cfg.get("model") or {})
    global_overrides = deepcopy(hermes_cfg.get("config_overrides") or {})

    data: dict[str, Any] = {}
    if model_cfg:
        data["model"] = model_cfg
    data["terminal"] = terminal_cfg
    data["agent"] = {"reasoning_effort": "high"}

    deep_merge(data, global_overrides)
    deep_merge(data, deepcopy(user.config_overrides))

    terminal = data.setdefault("terminal", {})
    if not isinstance(terminal, dict):
        raise RuntimeError(
            f"terminal config for {user.username} must remain a mapping."
        )

    terminal.setdefault("backend", "local")
    terminal.setdefault("timeout", DEFAULT_TERMINAL_TIMEOUT)
    terminal["cwd"] = str(user.workdir)
    return data


def build_systemd_unit(config: dict[str, Any], user: HermesTarget) -> str:
    hermes_cfg = config.get("hermes") or {}
    service_cfg = hermes_cfg.get("service") or {}
    description_template = str(
        service_cfg.get("description_template") or "Hermes Agent for {display_name}"
    )
    restart = str(service_cfg.get("restart") or DEFAULT_SERVICE_RESTART)
    restart_sec = int(service_cfg.get("restart_sec") or DEFAULT_SERVICE_RESTART_SEC)
    hermes_bin = str(hermes_cfg.get("executable") or DEFAULT_HERMES_BIN)

    rendered_description = description_template.format(
        username=user.username,
        display_name=user.display_name,
        linux_user=user.linux_user,
    )

    return "\n".join(
        [
            "[Unit]",
            f"Description={rendered_description}",
            "After=network.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={user.linux_user}",
            f"Group={user.linux_user}",
            f"WorkingDirectory={user.home_dir}",
            f"Environment=HOME={user.home_dir}",
            f"Environment=HERMES_HOME={user.hermes_home}",
            f"ExecStart={hermes_bin} gateway run --replace",
            f"Restart={restart}",
            f"RestartSec={restart_sec}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def install_user_files(config: dict[str, Any], user: HermesTarget) -> None:
    import pwd

    ensure_linux_user(user.linux_user)
    pw = pwd.getpwnam(user.linux_user)
    gid = pw.pw_gid

    for directory in [
        user.home_dir,
        user.workdir,
        user.hermes_home,
        user.hermes_home / "home",
    ]:
        directory.mkdir(parents=True, exist_ok=True)
        _set_owner_and_mode(directory, pw.pw_uid, gid, 0o700)

    env_path = user.hermes_home / ".env"
    env_path.write_text(build_env_content(user), encoding="utf-8")
    _set_owner_and_mode(env_path, pw.pw_uid, gid, 0o600)

    config_path = user.hermes_home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            build_config_data(config, user), sort_keys=False, allow_unicode=False
        ),
        encoding="utf-8",
    )
    _set_owner_and_mode(config_path, pw.pw_uid, gid, 0o600)

    service_path = Path("/etc/systemd/system") / user.systemd_service
    service_path.write_text(build_systemd_unit(config, user), encoding="utf-8")
    os.chmod(service_path, 0o644)

    _run_command(["systemctl", "daemon-reload"])
    _run_command(["systemctl", "enable", "--now", user.systemd_service])


def restart_service(service_name: str) -> None:
    _run_command(["systemctl", "restart", service_name])


def stop_and_remove_service(service_name: str) -> None:
    subprocess.run(
        ["systemctl", "disable", "--now", service_name], capture_output=True, text=True
    )
    service_path = Path("/etc/systemd/system") / service_name
    if service_path.exists():
        service_path.unlink()
    _run_command(["systemctl", "daemon-reload"])


def remove_linux_user(linux_user: str, *, delete_home: bool) -> None:
    result = subprocess.run(["id", "-u", linux_user], capture_output=True, text=True)
    if result.returncode != 0:
        return
    command = ["userdel"]
    if delete_home:
        command.append("-r")
    command.append(linux_user)
    _run_command(command)


def verify_hermes_models(api_key: str, host: str, port: int) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://{host}:{port}/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_hermes_models(
    api_key: str,
    host: str,
    port: int,
    *,
    timeout_seconds: int = 45,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_error = "unknown error"
    while time.time() < deadline:
        try:
            return verify_hermes_models(api_key, host, port)
        except Exception as exc:
            last_error = str(exc)
            time.sleep(2)
    raise RuntimeError(f"Hermes API did not become ready in time: {last_error}")


def require_binary(name: str) -> None:
    if not shutil.which(name):
        raise RuntimeError(f"{name} is required.")
