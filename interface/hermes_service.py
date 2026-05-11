from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pwd
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

from interface.mapping import HermesTarget, resolve_env_placeholders
from interface.model_options import DEFAULT_REASONING_EFFORT


ROOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ROOT_DIR.parent
DEFAULT_HERMES_BIN = "/usr/local/bin/hermes"
DEFAULT_SERVICE_RESTART = "always"
DEFAULT_SERVICE_RESTART_SEC = 3
DEFAULT_TERMINAL_TIMEOUT = 180
DEFAULT_RUNTIME_READY_TIMEOUT = 45
DEFAULT_RUNTIME_LOCK_DIR = Path("/run/potato-agent/runtime-start")
DEFAULT_SOUL_TEMPLATE_PATH = REPO_ROOT / "soul_settings" / "SOUL.md"
DEFAULT_INACCESSIBLE_PATHS = (
    "/srv/potato_agent",
    "/var/lib/potato-agent",
    "/etc/potato-agent",
    "/opt/interface-env",
)
MANAGED_BIOINFORMATICS_SKILLS_DIR_NAME = "potato-knowledge-bioinformatics"
DEFAULT_BIOINFORMATICS_SKILLS_PATH = (
    REPO_ROOT / "skills" / MANAGED_BIOINFORMATICS_SKILLS_DIR_NAME
)


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


def _run_command_result(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


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


def _apply_fallback_config(data: dict[str, Any], hermes_cfg: dict[str, Any]) -> None:
    if "fallback_providers" in hermes_cfg:
        data["fallback_providers"] = deepcopy(hermes_cfg["fallback_providers"])
    if "fallback_model" in hermes_cfg:
        data["fallback_model"] = deepcopy(hermes_cfg["fallback_model"])


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


def _set_owner_preserving_mode(path: Path, uid: int, gid: int) -> None:
    _set_owner_and_mode(path, uid, gid, stat.S_IMODE(path.stat().st_mode))


def _set_owner_recursive(path: Path, uid: int, gid: int) -> None:
    _set_owner_preserving_mode(path, uid, gid)
    for child in path.rglob("*"):
        _set_owner_preserving_mode(child, uid, gid)


def install_soul_file(user: HermesTarget, *, uid: int, gid: int) -> None:
    if not DEFAULT_SOUL_TEMPLATE_PATH.is_file():
        raise RuntimeError(f"SOUL template not found: {DEFAULT_SOUL_TEMPLATE_PATH}")

    soul_path = user.hermes_home / "SOUL.md"
    shutil.copyfile(DEFAULT_SOUL_TEMPLATE_PATH, soul_path)
    _set_owner_and_mode(soul_path, uid, gid, 0o600)


def install_bioinformatics_skills(user: HermesTarget, *, uid: int, gid: int) -> None:
    if not DEFAULT_BIOINFORMATICS_SKILLS_PATH.is_dir():
        raise RuntimeError(
            f"Bioinformatics skills not found: {DEFAULT_BIOINFORMATICS_SKILLS_PATH}"
        )

    skills_root = user.hermes_home / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _set_owner_and_mode(skills_root, uid, gid, 0o700)

    target_path = skills_root / MANAGED_BIOINFORMATICS_SKILLS_DIR_NAME
    if target_path.is_dir() and not target_path.is_symlink():
        shutil.rmtree(target_path)
    elif target_path.exists() or target_path.is_symlink():
        target_path.unlink()

    shutil.copytree(DEFAULT_BIOINFORMATICS_SKILLS_PATH, target_path)
    _set_owner_recursive(target_path, uid, gid)


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
    model_cfg = deepcopy(hermes_cfg.get("model") or {})
    global_overrides = deepcopy(hermes_cfg.get("config_overrides") or {})

    data: dict[str, Any] = {}
    if model_cfg:
        data["model"] = model_cfg
    _apply_fallback_config(data, hermes_cfg)
    data["terminal"] = terminal_cfg
    data["agent"] = {"reasoning_effort": DEFAULT_REASONING_EFFORT}

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
    inaccessible_paths = service_cfg.get("inaccessible_paths")
    if inaccessible_paths is None:
        inaccessible_paths = DEFAULT_INACCESSIBLE_PATHS
    if not isinstance(inaccessible_paths, (list, tuple)):
        inaccessible_paths = DEFAULT_INACCESSIBLE_PATHS

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
            "PrivateTmp=yes",
            "NoNewPrivileges=yes",
            *[
                f"InaccessiblePaths=-{path}"
                for path in inaccessible_paths
                if str(path).strip()
            ],
            f"Restart={restart}",
            f"RestartSec={restart_sec}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def install_user_runtime_files(config: dict[str, Any], user: HermesTarget) -> None:
    try:
        pw = pwd.getpwnam(user.linux_user)
    except KeyError as exc:
        raise RuntimeError(f"Linux user {user.linux_user!r} does not exist.") from exc

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
    install_soul_file(user, uid=pw.pw_uid, gid=gid)
    install_bioinformatics_skills(user, uid=pw.pw_uid, gid=gid)


def install_user_files(config: dict[str, Any], user: HermesTarget) -> None:
    ensure_linux_user(user.linux_user)
    install_user_runtime_files(config, user)

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
    )


def restart_service(service_name: str) -> None:
    _run_command(["systemctl", "restart", service_name])


def start_service(service_name: str) -> None:
    _run_command(["systemctl", "start", service_name])


def stop_service(service_name: str) -> None:
    _run_command(["systemctl", "stop", service_name])
    _run_command_result(["systemctl", "reset-failed", service_name])


def is_service_active(service_name: str) -> bool:
    result = _run_command_result(["systemctl", "is-active", service_name])
    return result.returncode == 0 and result.stdout.strip() == "active"


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
        if not candidate.exists():
            continue
        os.chown(candidate, pw.pw_uid, pw.pw_gid)
        os.chmod(candidate, 0o600)


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
