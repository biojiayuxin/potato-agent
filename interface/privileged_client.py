from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from interface.background_jobs import has_active_background_processes
from interface.hermes_service import (
    ensure_service_ready,
    install_user_files,
    is_service_active,
    remove_linux_user,
    service_operation_lock,
    stop_and_remove_service,
    stop_service,
)
from interface.mapping import DEFAULT_MAPPING_PATH, HermesTarget, MappingStore, load_mapping
from interface.mapping import remove_user_mapping_entry
from interface.mapping import upsert_user_mapping_entry, write_mapping
from interface.model_options import (
    get_active_model_option_id,
    normalize_model_options,
    patch_user_active_model,
)
from interface.runtime_state import (
    cleanup_expired_runtime_leases,
    has_active_runtime_leases,
    mark_background_activity,
    revoke_runtime_session,
)


class PrivilegedClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileDownload:
    filename: str
    content: bytes


def build_direct_tui_gateway_command(target: HermesTarget) -> list[str]:
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


class PrivilegedClient:
    def __init__(self, *, helper_python: str | None = None) -> None:
        self.helper_python = helper_python or os.getenv("INTERFACE_HELPER_PYTHON") or sys.executable
        self.force_helper = os.getenv("INTERFACE_FORCE_PRIVILEGED_HELPER", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

    def _can_call_directly(self) -> bool:
        return os.geteuid() == 0 and not self.force_helper

    def _helper_command(self, args: list[str]) -> list[str]:
        configured = os.getenv("INTERFACE_PRIVILEGED_HELPER", "").strip()
        if configured:
            return ["sudo", "-n", configured, *args]
        return [
            "sudo",
            "-n",
            self.helper_python,
            "-m",
            "interface.privileged_helper",
            *args,
        ]

    def helper_exec_command(self, args: list[str]) -> list[str]:
        return self._helper_command(args)

    def _call_helper(self, args: list[str], *, input_text: str | None = None) -> dict[str, Any]:
        result = subprocess.run(
            self._helper_command(args),
            capture_output=True,
            text=True,
            input=input_text,
            check=False,
        )
        stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
        raw_payload = stdout_lines[-1] if stdout_lines else ""
        if not raw_payload:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise PrivilegedClientError(detail)
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            detail = result.stderr.strip() or raw_payload
            raise PrivilegedClientError(detail) from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise PrivilegedClientError(str(payload.get("error") or "privileged helper failed"))
        return payload

    def provision_user(
        self,
        username: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> None:
        if self._can_call_directly():
            if email:
                config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=False)
                upsert_user_mapping_entry(
                    config,
                    username=username,
                    email=email,
                    display_name=display_name or username,
                )
                write_mapping(DEFAULT_MAPPING_PATH, config)
            config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True)
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
            if target is None:
                raise PrivilegedClientError(f"Unknown mapping user: {username}")
            install_user_files(config, target)
            return
        args = ["provision-user", "--username", username]
        if email:
            args.extend(["--email", email])
        if display_name:
            args.extend(["--display-name", display_name])
        self._call_helper(args)

    def ensure_runtime(self, target: HermesTarget) -> dict[str, Any]:
        if self._can_call_directly():
            return ensure_service_ready(target)
        payload = self._call_helper(["ensure-runtime", "--username", target.username])
        result = payload.get("result")
        return result if isinstance(result, dict) else {"status": "ready"}

    def stop_runtime(self, username: str) -> None:
        if self._can_call_directly():
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
            if target is None:
                raise PrivilegedClientError(f"Unknown mapping user: {username}")
            stop_service(target.systemd_service)
            return
        self._call_helper(["stop-runtime", "--username", username])

    def deprovision_user(self, username: str, *, delete_home: bool = False) -> None:
        if self._can_call_directly():
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
            if target is None:
                raise PrivilegedClientError(f"Unknown mapping user: {username}")
            stop_and_remove_service(target.systemd_service)
            remove_linux_user(target.linux_user, delete_home=delete_home)
            return
        args = ["deprovision-user", "--username", username]
        if delete_home:
            args.append("--delete-home")
        self._call_helper(args)

    def remove_runtime(self, username: str) -> None:
        if self._can_call_directly():
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
            if target is None:
                raise PrivilegedClientError(f"Unknown mapping user: {username}")
            stop_and_remove_service(target.systemd_service)
            return
        self._call_helper(["remove-runtime", "--username", username])

    def remove_mapping(self, username: str) -> None:
        if self._can_call_directly():
            config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=False)
            if remove_user_mapping_entry(config, username):
                write_mapping(DEFAULT_MAPPING_PATH, config)
            return
        self._call_helper(["remove-mapping", "--username", username])

    def has_active_background_processes(self, username: str) -> bool:
        if self._can_call_directly():
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
            if target is None:
                raise PrivilegedClientError(f"Unknown mapping user: {username}")
            return has_active_background_processes(target)

        payload = self._call_helper(["has-background-jobs", "--username", username])
        return bool(payload.get("active"))

    def stop_idle_runtime(self, username: str, user_id: str) -> dict[str, Any]:
        if self._can_call_directly():
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
            if target is None:
                raise PrivilegedClientError(f"Unknown mapping user: {username}")
            with service_operation_lock(target.systemd_service):
                cleanup_expired_runtime_leases()
                if has_active_runtime_leases(user_id):
                    return {"stopped": False, "reason": "active_lease"}
                if has_active_background_processes(target):
                    mark_background_activity(user_id)
                    return {"stopped": False, "reason": "background_jobs"}
                if not is_service_active(target.systemd_service):
                    return {"stopped": False, "reason": "service_inactive"}
                stop_service(target.systemd_service)
                revoke_runtime_session(user_id, reason="idle_timeout")
                return {"stopped": True}

        payload = self._call_helper(
            ["stop-idle-runtime", "--username", username, "--user-id", user_id]
        )
        return {
            "stopped": bool(payload.get("stopped")),
            "reason": str(payload.get("reason") or ""),
        }

    def patch_active_model(self, username: str, model_id: str) -> None:
        if self._can_call_directly():
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
            if target is None:
                raise PrivilegedClientError(f"Unknown mapping user: {username}")
            options = normalize_model_options(load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True))
            option = options.get(model_id)
            if option is None:
                raise PrivilegedClientError("Model is not allowed")
            patch_user_active_model(target, option)
            return
        self._call_helper(["patch-active-model", "--username", username, "--model-id", model_id])

    def get_active_model_id(self, username: str) -> str:
        if self._can_call_directly():
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
            if target is None:
                raise PrivilegedClientError(f"Unknown mapping user: {username}")
            options = normalize_model_options(load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True))
            return get_active_model_option_id(target, options)

        payload = self._call_helper(["get-active-model", "--username", username])
        active_id = str(payload.get("active_id") or "").strip()
        if not active_id:
            raise PrivilegedClientError("privileged helper returned no active model")
        return active_id

    def session_db_call(self, username: str, method: str, kwargs: dict[str, Any]) -> Any:
        payload = self._call_helper(
            [
                "session-db",
                "--username",
                username,
                "--method",
                method,
                "--kwargs-json",
                json.dumps(kwargs, ensure_ascii=False),
            ]
        )
        return payload.get("result")

    def file_tree(
        self,
        username: str,
        *,
        mode: str,
        root: str | None,
        path: str | None,
    ) -> dict[str, Any]:
        return self._call_helper(
            [
                "file-tree",
                "--username",
                username,
                "--mode",
                mode,
                "--root",
                root or "",
                "--path",
                path or "",
            ]
        )

    def file_download(
        self,
        username: str,
        *,
        mode: str,
        root: str | None,
        path: str,
    ) -> FileDownload:
        payload = self._call_helper(
            [
                "file-download",
                "--username",
                username,
                "--mode",
                mode,
                "--root",
                root or "",
                "--path",
                path,
            ]
        )
        return FileDownload(
            filename=str(payload.get("filename") or Path(path).name or "download.bin"),
            content=base64.b64decode(str(payload.get("content_b64") or "")),
        )

    def file_info(
        self,
        username: str,
        *,
        mode: str,
        root: str | None,
        path: str,
    ) -> dict[str, Any]:
        return self._call_helper(
            [
                "file-info",
                "--username",
                username,
                "--mode",
                mode,
                "--root",
                root or "",
                "--path",
                path,
            ]
        )

    def file_stream_command(
        self,
        username: str,
        *,
        mode: str,
        root: str | None,
        path: str,
    ) -> list[str]:
        return self.helper_exec_command(
            [
                "file-stream",
                "--username",
                username,
                "--mode",
                mode,
                "--root",
                root or "",
                "--path",
                path,
            ]
        )

    def file_upload(
        self,
        username: str,
        *,
        filename: str,
        content: bytes,
        upload_dir_name: str,
    ) -> dict[str, Any]:
        return self._call_helper(
            [
                "file-upload",
                "--username",
                username,
                "--filename",
                filename,
                "--upload-dir-name",
                upload_dir_name,
            ],
            input_text=base64.b64encode(content).decode("ascii"),
        )

    def tui_gateway_command(self, target: HermesTarget) -> list[str]:
        if self._can_call_directly():
            return build_direct_tui_gateway_command(target)
        else:
            return self.helper_exec_command(["tui-gateway", "--username", target.username])


privileged_client = PrivilegedClient()
