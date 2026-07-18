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
from interface.hermes_profile import (
    DEFAULT_HERMES_LITE_PYTHON,
    runtime_profile_environment,
)
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
from interface.model_proxy_config import get_model_proxy_base_url
from interface.process_utils import SESSION_DB_HELPER_TIMEOUT_SECONDS, run_process_group
from interface.runtime_state import (
    DEFAULT_RUNTIME_IDLE_TIMEOUT_SECONDS,
    claim_runtime_sleep,
    get_runtime_idle_eligibility,
    mark_background_activity,
    release_runtime_sleep_claim,
    revoke_runtime_session,
    runtime_sleep_claim_is_valid,
)


class PrivilegedClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileDownload:
    filename: str
    content: bytes


def build_direct_tui_gateway_command(target: HermesTarget) -> list[str]:
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
        f"HOME={target.home_dir}",
        f"HERMES_HOME={target.hermes_home}",
        f"TERMINAL_CWD={target.workdir}",
        f"PATH={os.environ.get('PATH', '')}",
        "PYTHONUNBUFFERED=1",
        *runtime_env_args,
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

    def _call_helper(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        try:
            if timeout_seconds is None:
                result = subprocess.run(
                    self._helper_command(args),
                    capture_output=True,
                    text=True,
                    input=input_text,
                    check=False,
                )
            else:
                result = run_process_group(
                    self._helper_command(args),
                    timeout_seconds=timeout_seconds,
                    input_text=input_text,
                )
        except subprocess.TimeoutExpired as exc:
            raise PrivilegedClientError(
                f"privileged helper timed out after {float(timeout_seconds or 0):.0f} seconds"
            ) from exc
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

    def stop_idle_runtime(
        self,
        username: str,
        user_id: str,
        idle_timeout_seconds: int = DEFAULT_RUNTIME_IDLE_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        normalized_timeout = max(int(idle_timeout_seconds), 1)
        if self._can_call_directly():
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
            if target is None:
                raise PrivilegedClientError(f"Unknown mapping user: {username}")
            with service_operation_lock(target.systemd_service):
                eligibility = get_runtime_idle_eligibility(
                    user_id,
                    idle_timeout_seconds=normalized_timeout,
                )
                if not eligibility or not bool(eligibility.get("eligible")):
                    reason = str((eligibility or {}).get("reason") or "recent_activity")
                    return {"stopped": False, "reason": reason}
                if has_active_background_processes(target):
                    mark_background_activity(user_id)
                    return {"stopped": False, "reason": "background_jobs"}
                claim_id = claim_runtime_sleep(
                    user_id,
                    idle_timeout_seconds=normalized_timeout,
                )
                if claim_id is None:
                    eligibility = get_runtime_idle_eligibility(
                        user_id,
                        idle_timeout_seconds=normalized_timeout,
                    )
                    reason = str((eligibility or {}).get("reason") or "recent_activity")
                    return {"stopped": False, "reason": reason}
                try:
                    if has_active_background_processes(target):
                        release_runtime_sleep_claim(user_id, claim_id=claim_id)
                        claim_id = ""
                        mark_background_activity(user_id)
                        return {"stopped": False, "reason": "background_jobs"}
                    service_active = is_service_active(target.systemd_service)
                    if not runtime_sleep_claim_is_valid(
                        user_id,
                        claim_id=claim_id,
                        idle_timeout_seconds=normalized_timeout,
                    ):
                        return {"stopped": False, "reason": "recent_activity"}
                    if not service_active:
                        revoke_runtime_session(user_id, reason="idle_timeout")
                        claim_id = ""
                        return {"stopped": True, "reason": "service_inactive"}
                    stop_service(target.systemd_service)
                    revoke_runtime_session(user_id, reason="idle_timeout")
                    claim_id = ""
                    return {"stopped": True}
                finally:
                    if claim_id:
                        release_runtime_sleep_claim(user_id, claim_id=claim_id)

        payload = self._call_helper(
            [
                "stop-idle-runtime",
                "--username",
                username,
                "--user-id",
                user_id,
                "--idle-timeout-seconds",
                str(normalized_timeout),
            ]
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
            config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True)
            options = normalize_model_options(config)
            option = options.get(model_id)
            if option is None:
                raise PrivilegedClientError("Model is not allowed")
            patch_user_active_model(
                target, option, proxy_base_url=get_model_proxy_base_url(config)
            )
            return
        self._call_helper(["patch-active-model", "--username", username, "--model-id", model_id])

    def get_active_model_id(self, username: str) -> str:
        if self._can_call_directly():
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
            if target is None:
                raise PrivilegedClientError(f"Unknown mapping user: {username}")
            config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True)
            options = normalize_model_options(config)
            return get_active_model_option_id(
                target, options, proxy_base_url=get_model_proxy_base_url(config)
            )

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
            ],
            timeout_seconds=SESSION_DB_HELPER_TIMEOUT_SECONDS,
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
            return self.helper_exec_command(
                ["tui-gateway", "--username", target.username]
            )


privileged_client = PrivilegedClient()
