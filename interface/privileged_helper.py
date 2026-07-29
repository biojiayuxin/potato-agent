#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(os.getenv("POTATO_AGENT_REPO_ROOT") or "/srv/potato_agent")
if REPO_ROOT.is_dir() and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface.background_jobs import has_active_background_processes
from interface import file_browser_policy
from interface.file_browser_policy import (
    FileBrowserAccessError,
    authorize_file_browser_path,
)
from interface.file_stream_worker import build_file_stream_worker_command
from interface.file_upload_worker import build_file_upload_worker_command
from interface.hermes_profile import (
    DEFAULT_HERMES_LITE_PYTHON,
    runtime_profile_environment,
)
from interface.hermes_service import (
    ensure_user_runtime_temp_dir,
    ensure_service_ready,
    install_user_files,
    is_service_active,
    remove_linux_user,
    require_binary,
    require_root,
    service_operation_lock,
    stop_and_remove_service,
    stop_service,
)
from interface.mapping import (
    DEFAULT_MAPPING_PATH,
    HermesTarget,
    MappingStore,
    build_targets_from_config,
    load_mapping,
    remove_user_mapping_entry,
    upsert_user_mapping_entry,
    write_mapping,
)
from interface.model_options import (
    ModelOptionsError,
    get_active_model_option_id,
    patch_user_active_model,
)
from interface.model_options import normalize_model_options
from interface.model_proxy_config import get_model_proxy_base_url
from interface.process_utils import SESSION_DB_INNER_TIMEOUT_SECONDS, run_process_group
from interface.runtime_state import (
    DEFAULT_RUNTIME_IDLE_TIMEOUT_SECONDS,
    claim_runtime_sleep,
    get_runtime_idle_eligibility,
    mark_background_activity,
    release_runtime_sleep_claim,
    revoke_runtime_session,
    runtime_sleep_claim_is_valid,
)
from interface.subprocess_env import interface_subprocess_env


DEFAULT_SESSION_DB_PYTHON = (
    os.getenv("INTERFACE_TUI_GATEWAY_PYTHON") or str(DEFAULT_HERMES_LITE_PYTHON)
)
USER_SESSION_DB_RPC_PATH = Path(__file__).with_name("session_db_rpc.py")
USER_SESSION_DB_RPC_SOURCE = USER_SESSION_DB_RPC_PATH.read_text(encoding="utf-8")


def _emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok", True) else 1


def _load_target(username: str) -> HermesTarget:
    target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
    if target is None:
        raise RuntimeError(f"Unknown mapping user: {username}")
    return target


def _run_as_user(
    target: HermesTarget,
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    full_command = [
        "runuser",
        "-u",
        target.linux_user,
        "--",
        "env",
        "-i",
        f"HOME={target.home_dir}",
        f"HERMES_HOME={target.hermes_home}",
        f"TERMINAL_CWD={target.workdir}",
        f"PATH={os.environ.get('PATH', '')}",
        "PYTHONUNBUFFERED=1",
        *command,
    ]
    if timeout_seconds is not None:
        return run_process_group(
            full_command,
            timeout_seconds=timeout_seconds,
            cwd=cwd or target.workdir,
            input_text=input_text,
        )
    return subprocess.run(
        full_command,
        capture_output=True,
        text=True,
        input=input_text,
        cwd=str(cwd or target.workdir),
        check=False,
        env=interface_subprocess_env(),
    )


def _session_db_call(target: HermesTarget, method: str, kwargs: dict[str, Any]) -> Any:
    result = _run_as_user(
        target,
        [
            DEFAULT_SESSION_DB_PYTHON,
            "-c",
            USER_SESSION_DB_RPC_SOURCE,
            str(target.state_db_path),
            method,
        ],
        timeout_seconds=SESSION_DB_INNER_TIMEOUT_SECONDS,
        input_text=json.dumps(kwargs, ensure_ascii=False),
    )
    stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
    raw_payload = stdout_lines[-1] if stdout_lines else ""
    if not raw_payload:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise RuntimeError(detail)
    payload = json.loads(raw_payload)
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = str(payload.get("error") or result.stderr.strip() or "unknown error")
        raise RuntimeError(error)
    return payload.get("result")


def _normalize_relative_path(path: str | None) -> str:
    raw = str(path or "").strip().replace("\\", "/").lstrip("/")
    parts: list[str] = []
    for part in Path(raw).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise RuntimeError("Invalid path")
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _resolve_browser_root(target: HermesTarget, root: str | None, *, mode: str) -> Path:
    home = target.home_dir.resolve()
    if not root:
        candidate = home
    else:
        raw = str(root or "").strip()
        if raw == "~":
            candidate = home
        elif raw.startswith("~/"):
            candidate = home / raw[2:]
        elif raw.startswith("/"):
            candidate = Path(raw)
        else:
            candidate = home / raw
    return _authorize_browser_path(target, candidate, mode=mode)


def _probe_path(target: HermesTarget, path: Path) -> dict[str, Any]:
    script = (
        "import json, os, pathlib, sys\n"
        "target = pathlib.Path(sys.argv[1]).resolve()\n"
        "payload = {'exists': target.exists()}\n"
        "if payload['exists']:\n"
        "    payload['is_dir'] = target.is_dir()\n"
        "    payload['is_file'] = target.is_file()\n"
        "    payload['readable'] = os.access(target, os.R_OK)\n"
        "    payload['enterable'] = os.access(target, os.X_OK)\n"
        "else:\n"
        "    payload.update({'is_dir': False, 'is_file': False, 'readable': False, 'enterable': False})\n"
        "print(json.dumps(payload))\n"
    )
    result = _run_as_user(target, ["python3", "-c", script, str(path)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "path probe failed")
    return json.loads(result.stdout.strip() or "{}")


def _list_directory(
    target: HermesTarget,
    path: Path,
    relative_path: str,
    *,
    mode: str,
) -> list[dict[str, Any]]:
    script = (
        "import json, os, pathlib, sys\n"
        "target = pathlib.Path(sys.argv[1]).resolve()\n"
        "logical_base = pathlib.PurePosixPath(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else pathlib.PurePosixPath()\n"
        "if not target.exists():\n"
        "    print(json.dumps({'error': 'not_found'})); raise SystemExit(0)\n"
        "if not target.is_dir():\n"
        "    print(json.dumps({'error': 'not_directory'})); raise SystemExit(0)\n"
        "if not os.access(target, os.R_OK | os.X_OK):\n"
        "    print(json.dumps({'error': 'permission_denied'})); raise SystemExit(0)\n"
        "entries = []\n"
        "for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):\n"
        "    if child.name.startswith('.'):\n"
        "        continue\n"
        "    try:\n"
        "        child_stat = child.stat()\n"
        "    except PermissionError:\n"
        "        continue\n"
        "    entries.append({'name': child.name, 'path': (logical_base / child.name).as_posix(), 'type': 'directory' if child.is_dir() else 'file', 'size': int(child_stat.st_size), 'modified': int(child_stat.st_mtime)})\n"
        "print(json.dumps({'entries': entries}))\n"
    )
    result = _run_as_user(target, ["python3", "-c", script, str(path), relative_path])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "directory access failed")
    payload = json.loads(result.stdout.strip() or "{}")
    error = str(payload.get("error") or "").strip()
    if error:
        raise RuntimeError(error)
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    allowed_entries: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        try:
            _authorize_browser_path(target, path / name, mode=mode)
        except RuntimeError:
            continue
        allowed_entries.append(item)
    return allowed_entries


def _authorize_browser_path(target: HermesTarget, path: Path, *, mode: str) -> Path:
    try:
        return authorize_file_browser_path(path, home=target.home_dir, mode=mode)
    except FileBrowserAccessError as exc:
        raise RuntimeError(str(exc)) from exc


def _tui_gateway_command(target: HermesTarget) -> list[str]:
    python_bin = os.getenv("INTERFACE_TUI_GATEWAY_PYTHON") or str(
        DEFAULT_HERMES_LITE_PYTHON
    )
    runtime_env = runtime_profile_environment(
        profile_path=target.runtime_profile_path,
        browser_cdp_url=target.browser_cdp_url,
    )
    runtime_env_args = [
        f"HERMES_DISABLE_LAZY_INSTALLS={runtime_env['HERMES_DISABLE_LAZY_INSTALLS']}",
        f"HERMES_SKIP_NODE_BOOTSTRAP={runtime_env['HERMES_SKIP_NODE_BOOTSTRAP']}",
        f"HERMES_DISABLE_GATEWAY_PLATFORMS={runtime_env['HERMES_DISABLE_GATEWAY_PLATFORMS']}",
        f"HERMES_DISABLE_MCP={runtime_env['HERMES_DISABLE_MCP']}",
        f"HERMES_DISABLE_CRON={runtime_env['HERMES_DISABLE_CRON']}",
        f"HERMES_DISABLE_KANBAN={runtime_env['HERMES_DISABLE_KANBAN']}",
        f"TERMINAL_ENV={runtime_env['TERMINAL_ENV']}",
        f"AGENT_BROWSER_ENGINE={runtime_env['AGENT_BROWSER_ENGINE']}",
        f"BROWSER_CDP_URL={runtime_env['BROWSER_CDP_URL']}",
        f"CAMOFOX_URL={runtime_env['CAMOFOX_URL']}",
        f"HERMES_BUNDLED_SKILLS={runtime_env['HERMES_BUNDLED_SKILLS']}",
        f"HERMES_OPTIONAL_SKILLS={runtime_env['HERMES_OPTIONAL_SKILLS']}",
        f"HERMES_AGENT_BROWSER_BIN_DIR={runtime_env['HERMES_AGENT_BROWSER_BIN_DIR']}",
        f"AGENT_BROWSER_EXECUTABLE_PATH={runtime_env['AGENT_BROWSER_EXECUTABLE_PATH']}",
    ]
    if "HERMES_RUNTIME_PROFILE_PATH" in runtime_env:
        runtime_env_args.append(
            f"HERMES_RUNTIME_PROFILE_PATH={runtime_env['HERMES_RUNTIME_PROFILE_PATH']}"
        )
    return [
        "runuser",
        "-u",
        target.linux_user,
        "--",
        "env",
        "-i",
        f"HOME={target.home_dir}",
        f"HERMES_HOME={target.hermes_home}",
        f"TMPDIR={target.hermes_home / 'tmp'}",
        f"TERMINAL_CWD={target.workdir}",
        f"PATH={os.environ.get('PATH', '')}",
        "PYTHONUNBUFFERED=1",
        *runtime_env_args,
        python_bin,
        "-m",
        "tui_gateway.entry",
    ]


def _exec_tui_gateway(target: HermesTarget) -> None:
    ensure_user_runtime_temp_dir(target)
    os.chdir(target.workdir)
    command = _tui_gateway_command(target)
    os.execvp(command[0], command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Potato Agent root helper")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in (
        "ensure-runtime",
        "stop-runtime",
        "remove-runtime",
        "remove-mapping",
        "has-background-jobs",
        "get-active-model",
        "tui-gateway-command",
        "tui-gateway",
    ):
        p = sub.add_parser(name)
        p.add_argument("--username", required=True)

    p = sub.add_parser("provision-user")
    p.add_argument("--username", required=True)
    p.add_argument("--email", default="")
    p.add_argument("--display-name", default="")

    p = sub.add_parser("deprovision-user")
    p.add_argument("--username", required=True)
    p.add_argument("--delete-home", action="store_true")

    p = sub.add_parser("stop-idle-runtime")
    p.add_argument("--username", required=True)
    p.add_argument("--user-id", required=True)
    p.add_argument(
        "--idle-timeout-seconds",
        type=int,
        default=DEFAULT_RUNTIME_IDLE_TIMEOUT_SECONDS,
    )

    p = sub.add_parser("patch-active-model")
    p.add_argument("--username", required=True)
    p.add_argument("--model-id", required=True)

    p = sub.add_parser("session-db")
    p.add_argument("--username", required=True)
    p.add_argument("--method", required=True)

    p = sub.add_parser("file-tree")
    p.add_argument("--username", required=True)
    p.add_argument("--mode", default="home_only")
    p.add_argument("--root", default="")
    p.add_argument("--path", default="")

    p = sub.add_parser("file-stream-v2")
    p.add_argument("--username", required=True)
    p.add_argument("--mode", default="home_only")
    p.add_argument("--root", default="")
    p.add_argument("--path", required=True)

    p = sub.add_parser("file-upload")
    p.add_argument("--username", required=True)
    p.add_argument("--filename", required=True)
    p.add_argument("--upload-dir-name", required=True)
    p.add_argument("--max-bytes", type=int, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    require_root()
    require_binary("runuser")

    try:
        if args.command == "tui-gateway":
            _exec_tui_gateway(_load_target(args.username))
            raise RuntimeError("failed to exec tui_gateway")

        if args.command == "provision-user":
            config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=False)
            if args.email:
                upsert_user_mapping_entry(
                    config,
                    username=args.username,
                    email=args.email,
                    display_name=args.display_name or args.username,
                )
                build_targets_from_config(config)
                write_mapping(DEFAULT_MAPPING_PATH, config)
            config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True)
            target = _load_target(args.username)
            install_user_files(config, target)
            return _emit({"ok": True, "target": {"username": target.username}})

        if args.command == "ensure-runtime":
            return _emit({"ok": True, "result": ensure_service_ready(_load_target(args.username))})

        if args.command == "stop-runtime":
            target = _load_target(args.username)
            stop_service(target.systemd_service)
            return _emit({"ok": True})

        if args.command == "remove-runtime":
            target = _load_target(args.username)
            stop_and_remove_service(target.systemd_service)
            return _emit({"ok": True})

        if args.command == "remove-mapping":
            config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=False)
            removed = remove_user_mapping_entry(config, args.username)
            if removed:
                write_mapping(DEFAULT_MAPPING_PATH, config)
            return _emit({"ok": True, "removed": removed})

        if args.command == "has-background-jobs":
            target = _load_target(args.username)
            return _emit({"ok": True, "active": has_active_background_processes(target)})

        if args.command == "deprovision-user":
            target = _load_target(args.username)
            stop_and_remove_service(target.systemd_service)
            remove_linux_user(target.linux_user, delete_home=bool(args.delete_home))
            return _emit({"ok": True})

        if args.command == "stop-idle-runtime":
            target = _load_target(args.username)
            with service_operation_lock(target.systemd_service):
                eligibility = get_runtime_idle_eligibility(
                    args.user_id,
                    idle_timeout_seconds=max(int(args.idle_timeout_seconds), 1),
                )
                if not eligibility or not bool(eligibility.get("eligible")):
                    reason = str((eligibility or {}).get("reason") or "recent_activity")
                    return _emit({"ok": True, "stopped": False, "reason": reason})
                if has_active_background_processes(target):
                    mark_background_activity(args.user_id)
                    return _emit({"ok": True, "stopped": False, "reason": "background_jobs"})
                claim_id = claim_runtime_sleep(
                    args.user_id,
                    idle_timeout_seconds=max(int(args.idle_timeout_seconds), 1),
                )
                if claim_id is None:
                    eligibility = get_runtime_idle_eligibility(
                        args.user_id,
                        idle_timeout_seconds=max(int(args.idle_timeout_seconds), 1),
                    )
                    reason = str((eligibility or {}).get("reason") or "recent_activity")
                    return _emit({"ok": True, "stopped": False, "reason": reason})
                try:
                    if has_active_background_processes(target):
                        release_runtime_sleep_claim(args.user_id, claim_id=claim_id)
                        claim_id = ""
                        mark_background_activity(args.user_id)
                        return _emit(
                            {"ok": True, "stopped": False, "reason": "background_jobs"}
                        )
                    service_active = is_service_active(target.systemd_service)
                    if not runtime_sleep_claim_is_valid(
                        args.user_id,
                        claim_id=claim_id,
                        idle_timeout_seconds=max(int(args.idle_timeout_seconds), 1),
                    ):
                        return _emit(
                            {"ok": True, "stopped": False, "reason": "recent_activity"}
                        )
                    if not service_active:
                        revoke_runtime_session(args.user_id, reason="idle_timeout")
                        claim_id = ""
                        return _emit(
                            {
                                "ok": True,
                                "stopped": True,
                                "reason": "service_inactive",
                            }
                        )
                    stop_service(target.systemd_service)
                    revoke_runtime_session(args.user_id, reason="idle_timeout")
                    claim_id = ""
                    return _emit({"ok": True, "stopped": True})
                finally:
                    if claim_id:
                        release_runtime_sleep_claim(args.user_id, claim_id=claim_id)

        if args.command == "patch-active-model":
            target = _load_target(args.username)
            config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True)
            model_options = normalize_model_options(config)
            option = model_options.get(args.model_id)
            if option is None:
                raise ModelOptionsError("Model is not allowed")
            patch_user_active_model(
                target, option, proxy_base_url=get_model_proxy_base_url(config)
            )
            return _emit({"ok": True})

        if args.command == "get-active-model":
            target = _load_target(args.username)
            config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True)
            model_options = normalize_model_options(config)
            active_id = get_active_model_option_id(
                target, model_options, proxy_base_url=get_model_proxy_base_url(config)
            )
            return _emit({"ok": True, "active_id": active_id})

        if args.command == "session-db":
            target = _load_target(args.username)
            kwargs = json.loads(sys.stdin.read() or "{}")
            if not isinstance(kwargs, dict):
                raise RuntimeError("Session DB kwargs must be a JSON object")
            result = _session_db_call(target, args.method, kwargs)
            return _emit({"ok": True, "result": result})

        if args.command == "file-tree":
            target = _load_target(args.username)
            root = _resolve_browser_root(target, args.root, mode=args.mode)
            relative = _normalize_relative_path(args.path)
            path = (root / relative).resolve()
            path = _authorize_browser_path(target, path, mode=args.mode)
            probe = _probe_path(target, path)
            if not probe.get("exists"):
                raise RuntimeError("Requested path does not exist")
            if not probe.get("is_dir"):
                raise RuntimeError("Requested path is not a directory")
            if not (probe.get("readable") and probe.get("enterable")):
                raise RuntimeError("Permission denied for this directory")
            return _emit(
                {
                    "ok": True,
                    "root": str(root),
                    "path": relative,
                    "entries": _list_directory(
                        target,
                        path,
                        relative,
                        mode=args.mode,
                    ),
                }
            )

        if args.command == "file-stream-v2":
            target = _load_target(args.username)
            root = _resolve_browser_root(target, args.root, mode=args.mode)
            relative = _normalize_relative_path(args.path)
            file_path = root / relative
            _authorize_browser_path(target, file_path, mode=args.mode)
            command = build_file_stream_worker_command(
                linux_user=target.linux_user,
                home=target.home_dir,
                browser_root=root,
                requested_path=file_path,
                mode=args.mode,
                public_data_root=file_browser_policy.DEFAULT_PUBLIC_DATA_PATH,
            )
            os.execvp(command[0], command)
            raise RuntimeError("failed to exec file stream")

        if args.command == "file-upload":
            target = _load_target(args.username)
            command = build_file_upload_worker_command(
                linux_user=target.linux_user,
                home=target.home_dir,
                mapping_username=target.username,
                filename=args.filename,
                upload_dir_name=args.upload_dir_name,
                max_bytes=args.max_bytes,
            )
            os.execvp(command[0], command)
            raise RuntimeError("failed to exec file upload")

        if args.command == "tui-gateway-command":
            return _emit(
                {
                    "ok": True,
                    "command": _tui_gateway_command(_load_target(args.username)),
                }
            )

        raise RuntimeError(f"Unsupported command: {args.command}")
    except Exception as exc:
        if getattr(args, "command", "") in {"file-stream-v2", "file-upload"}:
            print(str(exc), file=sys.stderr)
            return 1
        return _emit({"ok": False, "error": str(exc), "type": type(exc).__name__})


if __name__ == "__main__":
    raise SystemExit(main())
