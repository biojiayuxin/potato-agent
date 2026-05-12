#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
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
from interface.hermes_service import (
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
from interface.runtime_state import (
    cleanup_expired_runtime_leases,
    has_active_runtime_leases,
    mark_background_activity,
    revoke_runtime_session,
)


DEFAULT_SESSION_DB_PYTHON = (
    os.getenv("INTERFACE_TUI_GATEWAY_PYTHON") or "/opt/hermes-agent-venv/bin/python3"
)

USER_SESSION_DB_RPC_SCRIPT = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from hermes_state import SessionDB\n"
    "db = None\n"
    "db_path = Path(sys.argv[1])\n"
    "method = sys.argv[2]\n"
    "kwargs = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}\n"
    "try:\n"
    "    db = SessionDB(db_path=db_path)\n"
    "    if method == 'list_sessions_rich':\n"
    "        result = db.list_sessions_rich(**kwargs)\n"
    "    elif method == 'get_session':\n"
    "        result = db.get_session(str(kwargs.get('session_id') or '').strip())\n"
    "    elif method == 'resolve_session_id':\n"
    "        result = db.resolve_session_id(str(kwargs.get('session_id_or_prefix') or '').strip())\n"
    "    elif method == 'get_compression_tip':\n"
    "        result = db.get_compression_tip(str(kwargs.get('session_id') or '').strip())\n"
    "    elif method == 'get_messages':\n"
    "        result = db.get_messages(str(kwargs.get('session_id') or '').strip())\n"
    "    elif method == 'set_session_title':\n"
    "        result = db.set_session_title(\n"
    "            str(kwargs.get('session_id') or '').strip(),\n"
    "            str(kwargs.get('title') or ''),\n"
    "        )\n"
    "    elif method == 'delete_session':\n"
    "        result = db.delete_session(str(kwargs.get('session_id') or '').strip())\n"
    "    else:\n"
    "        raise RuntimeError(f'Unsupported session DB method: {method}')\n"
    "    print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))\n"
    "except Exception as exc:\n"
    "    print(json.dumps({'ok': False, 'error': str(exc), 'type': type(exc).__name__}, ensure_ascii=False))\n"
    "    raise SystemExit(1)\n"
    "finally:\n"
    "    if db is not None:\n"
    "        db.close()\n"
)


def _emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok", True) else 1


def _load_target(username: str) -> HermesTarget:
    target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
    if target is None:
        raise RuntimeError(f"Unknown mapping user: {username}")
    return target


def _run_as_user(target: HermesTarget, command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "runuser",
            "-u",
            target.linux_user,
            "--",
            "env",
            f"HOME={target.home_dir}",
            f"HERMES_HOME={target.hermes_home}",
            f"TERMINAL_CWD={target.workdir}",
            f"PATH={os.environ.get('PATH', '')}",
            "PYTHONUNBUFFERED=1",
            *command,
        ],
        capture_output=True,
        text=True,
        cwd=str(cwd or target.workdir),
        check=False,
    )


def _session_db_call(target: HermesTarget, method: str, kwargs: dict[str, Any]) -> Any:
    result = _run_as_user(
        target,
        [
            DEFAULT_SESSION_DB_PYTHON,
            "-c",
            USER_SESSION_DB_RPC_SCRIPT,
            str(target.state_db_path),
            method,
            json.dumps(kwargs, ensure_ascii=False),
        ],
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
        return home
    raw = str(root or "").strip()
    if raw == "~":
        resolved = home
    elif raw.startswith("~/"):
        resolved = (home / raw[2:]).resolve()
    elif raw.startswith("/"):
        resolved = Path(raw).resolve()
    else:
        resolved = (home / raw).resolve()
    if mode == "home_only":
        try:
            resolved.relative_to(home)
        except ValueError as exc:
            raise RuntimeError("Opening directories outside ~/ is disabled") from exc
    return resolved


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


def _list_directory(target: HermesTarget, path: Path, relative_path: str) -> list[dict[str, Any]]:
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
    return [item for item in entries if isinstance(item, dict)]


def _cat_file_b64(target: HermesTarget, path: Path, *, mode: str) -> dict[str, Any]:
    _assert_under_home(target, path, mode=mode)
    payload = _probe_path(target, path)
    if not payload.get("exists"):
        raise RuntimeError("Requested file does not exist")
    if not payload.get("is_file"):
        raise RuntimeError("Requested path is not a file")
    if not payload.get("readable"):
        raise RuntimeError("Permission denied for this file")
    script = (
        "import base64, pathlib, sys\n"
        "target = pathlib.Path(sys.argv[1]).resolve()\n"
        "sys.stdout.write(base64.b64encode(target.read_bytes()).decode('ascii'))\n"
    )
    result = _run_as_user(target, ["python3", "-c", script, str(path)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "file read failed")
    return {"content_b64": result.stdout.strip(), "filename": path.name}


def _assert_under_home(target: HermesTarget, path: Path, *, mode: str) -> None:
    if mode != "home_only":
        return
    try:
        path.resolve().relative_to(target.home_dir.resolve())
    except ValueError as exc:
        raise RuntimeError("Opening files outside ~/ is disabled") from exc


def _file_info(target: HermesTarget, path: Path, *, mode: str) -> dict[str, Any]:
    _assert_under_home(target, path, mode=mode)
    payload = _probe_path(target, path)
    if not payload.get("exists"):
        raise RuntimeError("Requested file does not exist")
    if not payload.get("is_file"):
        raise RuntimeError("Requested path is not a file")
    if not payload.get("readable"):
        raise RuntimeError("Permission denied for this file")
    stat = path.stat()
    return {
        "filename": path.name,
        "size": int(stat.st_size),
        "modified": int(stat.st_mtime),
    }


def _file_stream_command(target: HermesTarget, path: Path) -> list[str]:
    script = (
        "import pathlib, shutil, sys\n"
        "target = pathlib.Path(sys.argv[1]).resolve()\n"
        "with target.open('rb') as source:\n"
        "    shutil.copyfileobj(source, sys.stdout.buffer, length=1024 * 1024)\n"
    )
    return [
        "runuser",
        "-u",
        target.linux_user,
        "--",
        "env",
        f"HOME={target.home_dir}",
        f"HERMES_HOME={target.hermes_home}",
        f"TERMINAL_CWD={target.workdir}",
        "PYTHONUNBUFFERED=1",
        "python3",
        "-c",
        script,
        str(path),
    ]


def _write_upload_b64(target: HermesTarget, filename: str, content_b64: str, upload_dir_name: str) -> dict[str, Any]:
    script = (
        "import base64, json, os, pathlib, pwd, sys, uuid, datetime\n"
        "upload_dir_name = pathlib.Path(sys.argv[1])\n"
        "filename = pathlib.Path(sys.argv[2]).name or 'upload.bin'\n"
        "data = base64.b64decode(sys.stdin.read().strip().encode('ascii'))\n"
        "home = pathlib.Path(os.environ['HOME']).resolve()\n"
        "root = (upload_dir_name.joinpath(os.environ.get('POTATO_MAPPING_USERNAME', 'user'))).resolve() if upload_dir_name.is_absolute() else (home / upload_dir_name).resolve()\n"
        "root.mkdir(parents=True, exist_ok=True)\n"
        "root.chmod(0o700)\n"
        "safe = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in filename).strip('._') or 'upload.bin'\n"
        "dest = root / f\"{uuid.uuid4().hex}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{safe}\"\n"
        "dest.write_bytes(data)\n"
        "dest.chmod(0o600)\n"
        "print(json.dumps({'path': str(dest), 'name': safe, 'size': len(data)}))\n"
    )
    result = subprocess.run(
        [
            "runuser",
            "-u",
            target.linux_user,
            "--",
            "env",
            f"HOME={target.home_dir}",
            f"HERMES_HOME={target.hermes_home}",
            f"TERMINAL_CWD={target.workdir}",
            f"POTATO_MAPPING_USERNAME={target.username}",
            "python3",
            "-c",
            script,
            upload_dir_name,
            filename,
        ],
        capture_output=True,
        text=True,
        input=content_b64,
        cwd=str(target.workdir),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "upload write failed")
    return json.loads(result.stdout.strip() or "{}")


def _tui_gateway_command(target: HermesTarget) -> list[str]:
    python_bin = os.getenv("INTERFACE_TUI_GATEWAY_PYTHON") or "/opt/hermes-agent-venv/bin/python3"
    return [
        "runuser",
        "-u",
        target.linux_user,
        "--",
        "env",
        f"HOME={target.home_dir}",
        f"HERMES_HOME={target.hermes_home}",
        f"TERMINAL_CWD={target.workdir}",
        f"PATH={os.environ.get('PATH', '')}",
        "PYTHONUNBUFFERED=1",
        python_bin,
        "-m",
        "tui_gateway.entry",
    ]


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

    p = sub.add_parser("patch-active-model")
    p.add_argument("--username", required=True)
    p.add_argument("--model-id", required=True)

    p = sub.add_parser("session-db")
    p.add_argument("--username", required=True)
    p.add_argument("--method", required=True)
    p.add_argument("--kwargs-json", default="{}")

    p = sub.add_parser("file-tree")
    p.add_argument("--username", required=True)
    p.add_argument("--mode", default="home_only")
    p.add_argument("--root", default="")
    p.add_argument("--path", default="")

    for name in ("file-download", "file-info", "file-stream"):
        p = sub.add_parser(name)
        p.add_argument("--username", required=True)
        p.add_argument("--mode", default="home_only")
        p.add_argument("--root", default="")
        p.add_argument("--path", required=True)

    p = sub.add_parser("file-upload")
    p.add_argument("--username", required=True)
    p.add_argument("--filename", required=True)
    p.add_argument("--upload-dir-name", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    require_root()
    require_binary("runuser")

    try:
        if args.command == "tui-gateway":
            command = _tui_gateway_command(_load_target(args.username))
            os.execvp(command[0], command)
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
                cleanup_expired_runtime_leases()
                if has_active_runtime_leases(args.user_id):
                    return _emit({"ok": True, "stopped": False, "reason": "active_lease"})
                if has_active_background_processes(target):
                    mark_background_activity(args.user_id)
                    return _emit({"ok": True, "stopped": False, "reason": "background_jobs"})
                if not is_service_active(target.systemd_service):
                    revoke_runtime_session(args.user_id, reason="idle_timeout")
                    return _emit({"ok": True, "stopped": True, "reason": "service_inactive"})
                stop_service(target.systemd_service)
                revoke_runtime_session(args.user_id, reason="idle_timeout")
                return _emit({"ok": True, "stopped": True})

        if args.command == "patch-active-model":
            target = _load_target(args.username)
            model_options = normalize_model_options(load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True))
            option = model_options.get(args.model_id)
            if option is None:
                raise ModelOptionsError("Model is not allowed")
            patch_user_active_model(target, option)
            return _emit({"ok": True})

        if args.command == "get-active-model":
            target = _load_target(args.username)
            model_options = normalize_model_options(load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True))
            active_id = get_active_model_option_id(target, model_options)
            return _emit({"ok": True, "active_id": active_id})

        if args.command == "session-db":
            target = _load_target(args.username)
            result = _session_db_call(target, args.method, json.loads(args.kwargs_json or "{}"))
            return _emit({"ok": True, "result": result})

        if args.command == "file-tree":
            target = _load_target(args.username)
            root = _resolve_browser_root(target, args.root, mode=args.mode)
            relative = _normalize_relative_path(args.path)
            path = (root / relative).resolve()
            probe = _probe_path(target, path)
            if not probe.get("exists"):
                raise RuntimeError("Requested path does not exist")
            if not probe.get("is_dir"):
                raise RuntimeError("Requested path is not a directory")
            if not (probe.get("readable") and probe.get("enterable")):
                raise RuntimeError("Permission denied for this directory")
            return _emit({"ok": True, "root": str(root), "path": relative, "entries": _list_directory(target, path, relative)})

        if args.command == "file-download":
            target = _load_target(args.username)
            root = _resolve_browser_root(target, args.root, mode=args.mode)
            relative = _normalize_relative_path(args.path)
            result = _cat_file_b64(target, (root / relative).resolve(), mode=args.mode)
            return _emit({"ok": True, **result})

        if args.command == "file-info":
            target = _load_target(args.username)
            root = _resolve_browser_root(target, args.root, mode=args.mode)
            relative = _normalize_relative_path(args.path)
            result = _file_info(target, (root / relative).resolve(), mode=args.mode)
            return _emit({"ok": True, **result})

        if args.command == "file-stream":
            target = _load_target(args.username)
            root = _resolve_browser_root(target, args.root, mode=args.mode)
            relative = _normalize_relative_path(args.path)
            file_path = (root / relative).resolve()
            _file_info(target, file_path, mode=args.mode)
            command = _file_stream_command(target, file_path)
            os.execvp(command[0], command)
            raise RuntimeError("failed to exec file stream")

        if args.command == "file-upload":
            target = _load_target(args.username)
            result = _write_upload_b64(
                target,
                args.filename,
                sys.stdin.read().strip(),
                args.upload_dir_name,
            )
            return _emit({"ok": True, **result})

        if args.command == "tui-gateway-command":
            return _emit({"ok": True, "command": _tui_gateway_command(_load_target(args.username))})

        raise RuntimeError(f"Unsupported command: {args.command}")
    except Exception as exc:
        if getattr(args, "command", "") == "file-stream":
            print(str(exc), file=sys.stderr)
            return 1
        return _emit({"ok": False, "error": str(exc), "type": type(exc).__name__})


if __name__ == "__main__":
    raise SystemExit(main())
