from __future__ import annotations

import contextlib
import asyncio
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import pwd
import sqlite3
import re
import secrets
import select
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from urllib.parse import quote

import jwt
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.responses import Response as FastAPIResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from interface.auth_db import (
    EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET,
    EMAIL_VERIFICATION_PURPOSE_SIGNUP,
    EmailVerificationError,
    activate_signup_user,
    cleanup_expired_agreement_acceptances,
    cleanup_terminal_signup_jobs,
    create_temporary_user,
    create_pending_email_verification,
    create_signup_job_with_email_verification,
    delete_temporary_user_record,
    delete_user_by_mapping_username,
    ensure_auth_db,
    email_exists,
    email_verification_send_stats,
    get_next_pending_signup_job,
    get_signup_job,
    get_user_by_email,
    get_user_by_id,
    get_user_with_password_by_id,
    get_user_with_password_by_login,
    has_agreement_acceptance,
    is_temporary_user,
    list_users,
    mark_email_verification_failed,
    mark_temporary_user_cleanup_attempt,
    record_email_verification_sent,
    record_agreement_acceptance,
    reset_user_password_with_email_verification,
    retire_temporary_user_identity,
    set_signup_job_status,
    TEMPORARY_USER_STATUS_ACTIVE,
    TEMPORARY_USER_STATUS_FAILED,
    update_user_password,
    username_exists,
    verify_password,
)
from interface.archive_store import (
    archived_session_exists,
    archive_session_record,
    cleanup_expired_archived_sessions,
    count_archived_sessions,
    ensure_archive_db,
    finish_archive_run,
    start_archive_run,
)
from interface.background_jobs import has_active_background_processes
from interface.chat_share_store import (
    CHAT_SHARE_MAX_RECIPIENTS,
    CLAIM_STATUS_CLAIMED,
    CLAIM_STATUS_COMPLETED,
    CLAIM_STATUS_IN_PROGRESS,
    CLAIM_STATUS_RATE_LIMITED,
    CLAIM_STATUS_RECIPIENT_LIMIT,
    CLAIM_STATUS_TARGET_DELETED,
    CLAIM_STATUS_UNAVAILABLE,
    ChatShareLimitError,
    ChatShareValidationError,
    chat_share_import_claim_is_valid,
    claim_chat_share_import,
    clear_source_session_invalidation,
    cleanup_expired_chat_shares,
    complete_chat_share_import,
    create_chat_share,
    ensure_chat_share_store,
    fail_chat_share_import,
    finalize_source_session_invalidation,
    heartbeat_source_session_invalidation,
    invalidate_source_session_shares,
    invalidate_user_chat_share_data,
    mark_chat_share_import_target_deleted_by_session,
)
from interface.display_store import (
    cleanup_turn_submission_receipts,
    create_display_messages_if_absent,
    create_turn_submission_receipt,
    delete_display_user_data,
    delete_display_messages,
    delete_live_session_state,
    delete_session_events,
    ensure_display_store,
    fail_turn_submission_receipt,
    find_live_session_id_by_run_id,
    finish_turn_submission_receipt,
    get_live_session_state,
    list_live_session_states,
    mark_active_live_session_states_failed,
    get_display_session_meta,
    get_display_messages,
    get_message_fork_boundary,
    get_live_poll_snapshot,
    get_turn_submission_receipt,
    heartbeat_turn_submission_receipt,
    list_display_session_metas,
    save_display_messages,
    save_message_fork_boundary,
)
from interface import file_browser_policy
from interface.file_browser_policy import (
    FileBrowserAccessError,
    authorize_file_browser_path,
    normalize_file_browser_mode,
)
from interface.file_stream_worker import build_file_stream_worker_command
from interface.file_upload_worker import (
    HARD_MAX_UPLOAD_BYTES,
    UploadMaintenanceError,
    UploadTooLargeError,
    build_file_upload_worker_command,
    run_upload_command,
)
from interface.feedback_store import (
    claim_feedback_submission,
    cleanup_feedback_submissions,
    ensure_feedback_store,
    finish_feedback_submission,
)
from interface.hermes_profile import DEFAULT_HERMES_LITE_PYTHON
from interface.legal import (
    CURRENT_AGREEMENT_PATH,
    CURRENT_AGREEMENT_VERSION,
    agreement_document_path,
    current_agreement_metadata,
)
from interface.mapping import DEFAULT_MAPPING_PATH, HermesTarget, MappingStore
from interface.mailer import (
    MailerConfigurationError,
    MailerDeliveryError,
    send_feedback_email,
    send_password_reset_email,
    send_signup_verification_email,
)
from interface.hermes_service import (
    is_service_active,
    service_operation_lock,
    stop_service,
)
from interface.runtime_state import (
    claim_runtime_sleep,
    claim_temporary_user_cleanup,
    cleanup_expired_runtime_leases,
    clear_session_revocation,
    delete_runtime_state,
    ensure_runtime_state_store,
    get_runtime_state,
    get_runtime_idle_eligibility,
    list_idle_temporary_user_candidates,
    list_idle_runtime_candidates,
    mark_background_activity,
    mark_foreground_activity,
    mark_runtime_started,
    release_runtime_sleep_claim,
    release_temporary_cleanup_claim,
    revoke_runtime_session,
    runtime_sleep_claim_is_valid,
    temporary_cleanup_claim_is_valid,
)
from interface.request_limits import RequestBodyLimitMiddleware
from interface.redaction import force_redact_text, force_redact_value
from interface.subprocess_env import interface_subprocess_env
from interface.secret_config import (
    load_session_cookie_secure,
    load_session_secret,
)
from interface.model_options import (
    ModelOptionsError,
    normalize_model_options,
    get_active_model_option_id,
    patch_user_active_model,
)
from interface.model_proxy_config import get_model_proxy_base_url
from interface.password_policy import (
    PASSWORD_COMPLEXITY_DETAIL,
    password_complexity_error,
)
from interface.process_utils import SESSION_DB_INNER_TIMEOUT_SECONDS, run_process_group
from interface.privileged_client import (
    PrivilegedClientError,
    PrivilegedMaintenanceError,
    privileged_client,
)
from interface.user_lifecycle_lock import (
    MAINTENANCE_ERROR_MARKER,
    MAINTENANCE_ERROR_MESSAGE,
)
from interface.tui_gateway_bridge import (
    TuiGatewayBridge,
    TuiGatewayBridgeError,
    TuiGatewayBridgeRegistry,
)
from interface.session_run_manager import (
    ACTIVE_LIVE_STATUSES,
    ATTACHMENT_BLOCK_END,
    ATTACHMENT_BLOCK_START,
    ATTACHMENT_HINT_LINE,
    SessionRunManager,
    build_hermes_user_content,
)
from interface.bulk_rnaseq_viewer import router as bulk_rnaseq_viewer_router
from interface.daily_updates import router as daily_updates_router
from interface.gene_catalog import router as gene_catalog_router
from interface.genome_browser import router as genome_browser_router
from interface.pan_genome import router as pan_genome_router
from interface.spatial_viewer import router as spatial_viewer_router
from interface.wgcna_viewer import router as wgcna_viewer_router
from interface.admin_api import router as admin_router
from interface.admin_usage_client import (
    AdminUsageUnavailable,
    reconcile_principals_once,
)
from interface.system_resources import SystemResourceSampler


LOGGER = logging.getLogger("potato_interface")
ROOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ROOT_DIR.parent
STATIC_DIR = ROOT_DIR / "static"
LITE_DIR = STATIC_DIR / "lite"
ABOUT_DIR = STATIC_DIR / "about"
SPATIAL_STATIC_DIR = STATIC_DIR / "spatial"
FAVICON_PATH = STATIC_DIR / "favicon.png"
SESSION_COOKIE_NAME = "potato_interface_token"
REQUEST_AUTH_RESOLUTION_STATE_KEY = "potato_auth_resolution"
SESSION_SECRET = load_session_secret()
SESSION_COOKIE_SECURE = load_session_cookie_secure()
MAX_SESSION_TTL_SECONDS = 7 * 24 * 3600
SESSION_TTL_SECONDS = min(
    int(os.getenv("INTERFACE_SESSION_TTL_SECONDS", str(MAX_SESSION_TTL_SECONDS))),
    MAX_SESSION_TTL_SECONDS,
)
MAX_UPLOAD_SIZE_BYTES = min(
    int(os.getenv("INTERFACE_MAX_UPLOAD_BYTES", str(HARD_MAX_UPLOAD_BYTES))),
    HARD_MAX_UPLOAD_BYTES,
)
MAX_PREVIEW_SIZE_BYTES = int(
    os.getenv("INTERFACE_MAX_PREVIEW_BYTES", str(10 * 1024 * 1024))
)
FILE_STREAM_METADATA_MAX_BYTES = 64 * 1024
FILE_STREAM_IDLE_TIMEOUT_SECONDS = 30.0
MAX_DOWNLOADS_PER_USER = 2
MAX_DOWNLOADS_GLOBAL = 16
MAX_WEBSOCKET_RPC_BYTES = 64 * 1024
BROWSER_WEBSOCKET_METHODS = frozenset(
    {"session.create", "session.resume", "session.title"}
)
FILE_BROWSER_MODE = (
    os.getenv("INTERFACE_FILE_BROWSER_MODE", "home_only").strip().lower()
)
UPLOAD_DIR_NAME = os.getenv("INTERFACE_UPLOAD_DIR_NAME", ".potato-interface-uploads")
DEFAULT_ARCHIVE_RETENTION_DAYS = 99999
ARCHIVE_RETENTION_DAYS = int(
    os.getenv(
        "INTERFACE_ARCHIVE_RETENTION_DAYS",
        str(DEFAULT_ARCHIVE_RETENTION_DAYS),
    )
)
ARCHIVE_STORAGE_RETENTION_DAYS = int(
    os.getenv("INTERFACE_ARCHIVE_STORAGE_RETENTION_DAYS", "30")
)
ARCHIVE_SCHEDULE_HOUR = int(os.getenv("INTERFACE_ARCHIVE_SCHEDULE_HOUR", "3"))
RUNTIME_IDLE_TIMEOUT_SECONDS = int(
    os.getenv("INTERFACE_RUNTIME_IDLE_TIMEOUT_SECONDS", str(30 * 60))
)
RUNTIME_IDLE_CHECK_INTERVAL_SECONDS = int(
    os.getenv("INTERFACE_RUNTIME_IDLE_CHECK_INTERVAL_SECONDS", "60")
)
TEMPORARY_USER_CLEANUP_RETRY_SECONDS = int(
    os.getenv("INTERFACE_TEMPORARY_USER_CLEANUP_RETRY_SECONDS", "60")
)
CHAT_SHARING_ENABLED = os.getenv(
    "INTERFACE_CHAT_SHARING_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
CHAT_SHARE_CLEANUP_INTERVAL_SECONDS = 60 * 60
CHAT_SHARE_REQUEST_HEADER = "X-Potato-Request"
CHAT_SHARE_REQUEST_HEADER_VALUE = "1"
CHAT_SHARE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
TURN_SUBMISSION_RECEIPT_HEARTBEAT_SECONDS = 30
TEMPORARY_USER_PREFIX = "temp"
TEMPORARY_USER_EMAIL_DOMAIN = "temporary.potato-agent.local"
FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")
TEXT_PREVIEW_EXTENSIONS = {
    ".bat",
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".diff",
    ".env",
    ".err",
    ".fa",
    ".faa",
    ".fasta",
    ".fastq",
    ".ffn",
    ".fna",
    ".fq",
    ".frn",
    ".gff",
    ".gff3",
    ".go",
    ".gtf",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".log",
    ".lua",
    ".md",
    ".mjs",
    ".ndjson",
    ".out",
    ".patch",
    ".php",
    ".properties",
    ".py",
    ".r",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".stderr",
    ".stdout",
    ".toml",
    ".trace",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
IMAGE_PREVIEW_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}
TEXT_PREVIEW_FILENAMES = {
    "access_log",
    "debug",
    "error_log",
    "messages",
    "nohup.out",
    "stderr",
    "stdout",
    "syslog",
}
INTERFACE_SESSION_SOURCES = ("tui",)
ACTIVITY_REFRESH_EXCLUDED_PATHS = {
    "/api/feedback",
    "/api/auth/session",
    "/api/auth/signin",
    "/api/auth/signout",
    "/api/auth/temporary",
    "/api/auth/password-reset",
    "/api/auth/password-reset/email-verifications",
    "/api/files/revision",
    "/api/files/tree",
}
EMAIL_VERIFICATION_TTL_SECONDS = 10 * 60
EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS = 60
EMAIL_VERIFICATION_EMAIL_HOURLY_LIMIT = 5
EMAIL_VERIFICATION_IP_HOURLY_LIMIT = 20
HERMES_SRC = REPO_ROOT / "hermes-lite"
if str(HERMES_SRC) not in sys.path:
    sys.path.insert(0, str(HERMES_SRC))

DEFAULT_SESSION_DB_PYTHON = os.getenv("INTERFACE_TUI_GATEWAY_PYTHON") or str(
    DEFAULT_HERMES_LITE_PYTHON
)
USER_SESSION_DB_RPC_PATH = ROOT_DIR / "session_db_rpc.py"
USER_SESSION_DB_RPC_SOURCE = USER_SESSION_DB_RPC_PATH.read_text(encoding="utf-8")

mapping_store = MappingStore(DEFAULT_MAPPING_PATH)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    username: str
    name: str
    role: str
    mapping_username: str
    target: HermesTarget
    is_temporary: bool = False


class SigninRequest(BaseModel):
    email: str
    password: str
    agreement_version: str = ""
    agreement_accepted: bool = False


class SignupRequest(BaseModel):
    username: str
    email: str
    password: str
    display_name: str = ""
    email_verification_id: str = ""
    email_verification_code: str = ""
    agreement_version: str = ""
    agreement_accepted: bool = False


class TemporaryAuthRequest(BaseModel):
    agreement_version: str = ""
    agreement_accepted: bool = False


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class PasswordResetRequest(BaseModel):
    email: str
    email_verification_id: str = ""
    email_verification_code: str = ""
    new_password: str


class EmailVerificationRequest(BaseModel):
    email: str


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    contact_email: str = ""
    page_path: str


class ChatShareImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str


class ChatShareCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionDisplaySyncRequest(BaseModel):
    messages: list[dict[str, Any]]
    draft_title: str = ""


class SessionTitleUpdateRequest(BaseModel):
    title: str = ""


class SessionForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fork_cursor: str = ""
    request_id: str = ""


class SessionTurnSubmitRequest(BaseModel):
    prompt: str = ""
    attachments: list[dict[str, Any]] = []
    draft_title: str = ""
    mode: str = "chat"
    request_id: str = ""


class SessionApprovalRequest(BaseModel):
    choice: str
    approval_id: str = ""


class ActiveModelUpdateRequest(BaseModel):
    id: str = ""


def _max_upload_size_mb() -> int:
    return MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)


def _upload_file_too_large_detail() -> str:
    return f"Upload file too large (> {_max_upload_size_mb()} MB)."


def _attachment_total_too_large_detail() -> str:
    return f"Total attachment size too large (> {_max_upload_size_mb()} MB)."


def _attachment_total_size_bytes(attachments: list[dict[str, Any]]) -> int:
    total_size = 0
    for attachment in attachments:
        try:
            size = int(attachment.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        total_size += max(0, size)
    return total_size


def _normalized_file_browser_mode() -> str:
    return normalize_file_browser_mode(FILE_BROWSER_MODE)


def _now_seconds() -> int:
    return int(datetime.now(UTC).timestamp())


def _chat_share_recipient_id(user: CurrentUser) -> str:
    account_kind = "temporary" if user.is_temporary else "formal"
    return f"{account_kind}:{user.id}"


def _chat_share_recipient_id_for_user_id(user_id: str, *, is_temporary: bool) -> str:
    account_kind = "temporary" if is_temporary else "formal"
    return f"{account_kind}:{str(user_id or '').strip()}"


def _require_chat_share_request(request: Request) -> None:
    if not CHAT_SHARING_ENABLED:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "sharing_disabled",
                "message": "Chat sharing is temporarily unavailable.",
            },
        )
    if request.headers.get(CHAT_SHARE_REQUEST_HEADER, "").strip() != (
        CHAT_SHARE_REQUEST_HEADER_VALUE
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "share_request_required",
                "message": "Protected share request header is required.",
            },
        )
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type.strip().lower() != "application/json":
        raise HTTPException(
            status_code=415,
            detail={
                "code": "json_required",
                "message": "Chat sharing requests must use JSON.",
            },
        )


async def _validate_chat_share_json_body(
    request: Request,
    model: type[BaseModel],
) -> BaseModel:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RequestValidationError(
            [
                {
                    "type": "json_invalid",
                    "loc": ("body",),
                    "msg": "Invalid JSON body",
                    "input": None,
                }
            ],
            body=None,
        ) from exc
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        errors = []
        for error in exc.errors():
            normalized = dict(error)
            normalized["loc"] = ("body", *tuple(error.get("loc") or ()))
            errors.append(normalized)
        raise RequestValidationError(errors, body=body) from exc


def _normalize_chat_share_token_or_404(value: Any) -> str:
    token = str(value or "").strip()
    if not CHAT_SHARE_TOKEN_RE.fullmatch(token):
        raise HTTPException(
            status_code=404,
            detail={"code": "share_unavailable", "message": "Share link unavailable."},
        )
    return token


def _validate_signup_email(email: str) -> str:
    normalized_email = email.strip().lower()
    if (
        "@" not in normalized_email
        or len(normalized_email) > 254
        or any(char.isspace() for char in normalized_email)
    ):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    return normalized_email


def _validate_feedback_contact_email(contact_email: str) -> str:
    normalized_email = contact_email.strip()
    if not normalized_email:
        return ""
    if (
        len(normalized_email) > 254
        or normalized_email.count("@") != 1
        or any(char.isspace() or ord(char) < 32 for char in normalized_email)
    ):
        raise HTTPException(status_code=400, detail="Invalid contact email address.")
    local_part, domain = normalized_email.rsplit("@", 1)
    if (
        not local_part
        or len(local_part) > 64
        or local_part.startswith(".")
        or local_part.endswith(".")
        or ".." in local_part
        or re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local_part) is None
    ):
        raise HTTPException(status_code=400, detail="Invalid contact email address.")
    domain_labels = domain.split(".")
    if not domain or any(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) is None
        for label in domain_labels
    ):
        raise HTTPException(status_code=400, detail="Invalid contact email address.")
    return normalized_email


def _validate_feedback_payload(payload: FeedbackRequest) -> tuple[str, str, str]:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Feedback message is required.")
    if len(message) > 5_000:
        raise HTTPException(
            status_code=400,
            detail="Feedback message must be 5,000 characters or fewer.",
        )

    contact_email = _validate_feedback_contact_email(payload.contact_email)
    page_path = payload.page_path.strip()
    if (
        not page_path
        or len(page_path) > 512
        or not page_path.startswith("/")
        or page_path.startswith("//")
        or any(ord(char) < 32 for char in page_path)
    ):
        raise HTTPException(status_code=400, detail="Invalid source page path.")
    return message, contact_email, page_path


def _validate_password_complexity(password: str) -> None:
    error = password_complexity_error(password)
    if error is not None:
        raise HTTPException(status_code=400, detail=error)


def _hash_email_verification_code(
    email: str,
    code: str,
    *,
    purpose: str = EMAIL_VERIFICATION_PURPOSE_SIGNUP,
) -> str:
    normalized_email = email.strip().lower()
    normalized_code = code.strip()
    normalized_purpose = purpose.strip() or EMAIL_VERIFICATION_PURPOSE_SIGNUP
    message = f"{normalized_purpose}:{normalized_email}:{normalized_code}"
    return hmac.new(
        SESSION_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _client_ip_for_request(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or "unknown"
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client is not None else "unknown"


def _hash_client_ip(client_ip: str) -> str:
    return hmac.new(
        SESSION_SECRET.encode("utf-8"),
        f"client-ip:{client_ip.strip()}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _validate_signup_payload(
    payload: SignupRequest,
) -> tuple[str, str, str, str, str, str]:
    username = payload.username.strip()
    email = _validate_signup_email(payload.email)
    password = payload.password
    display_name = payload.display_name.strip() or username
    verification_id = payload.email_verification_id.strip()
    verification_code = payload.email_verification_code.strip()

    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3-32 characters and contain only letters, numbers, or underscores.",
        )
    _validate_password_complexity(password)
    if not verification_id:
        raise HTTPException(status_code=400, detail="Email verification is required.")
    if not re.fullmatch(r"\d{6}", verification_code):
        raise HTTPException(
            status_code=400, detail="Verification code must be 6 digits."
        )
    if username_exists(username):
        raise HTTPException(status_code=409, detail="Username is already taken.")
    if email_exists(email):
        raise HTTPException(status_code=409, detail="Email is already taken.")
    return username, email, password, display_name, verification_id, verification_code


def _validate_current_agreement_acceptance(
    agreement_version: str,
    agreement_accepted: bool,
) -> dict[str, Any]:
    metadata = current_agreement_metadata()
    if not agreement_accepted or agreement_version.strip() != metadata["version"]:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "agreement_required",
                "message": "You must accept the current research preview terms before continuing.",
                "agreement": metadata,
            },
        )
    return metadata


def _reset_mapping_store_cache() -> None:
    mapping_store._mtime_ns = None
    mapping_store._targets = []


def _generate_temporary_identity() -> tuple[str, str, str]:
    for _ in range(10):
        username = f"{TEMPORARY_USER_PREFIX}_{int(time.time())}_{secrets.token_hex(4)}"
        email = f"{username}@{TEMPORARY_USER_EMAIL_DOMAIN}"
        if username_exists(username) or email_exists(email):
            continue
        return username, email, "Temporary User"
    raise HTTPException(
        status_code=503,
        detail="Failed to allocate a temporary user name. Please try again.",
    )


async def _rollback_temporary_provision(username: str) -> None:
    normalized_username = username.strip()
    if not normalized_username:
        return
    with contextlib.suppress(Exception):
        await asyncio.to_thread(
            privileged_client.deprovision_user,
            normalized_username,
            delete_home=True,
        )
    with contextlib.suppress(Exception):
        await asyncio.to_thread(privileged_client.remove_mapping, normalized_username)
    with contextlib.suppress(Exception):
        await asyncio.to_thread(
            delete_user_by_mapping_username,
            normalized_username,
        )
    with contextlib.suppress(Exception):
        _reset_mapping_store_cache()


def _process_signup_job_sync(job: dict[str, Any]) -> None:
    job_id = str(job["job_id"])
    username = str(job["username"])
    email = str(job["email"])
    display_name = str(job["display_name"])
    set_signup_job_status(job_id, status="provisioning")

    try:
        privileged_client.provision_user(
            username=username,
            email=email,
            display_name=display_name,
        )
        _reset_mapping_store_cache()
        target = mapping_store.get_target_by_username(username)
        if target is None:
            raise RuntimeError("Failed to resolve newly created mapping target.")

        activate_signup_user(job_id, mapping_username=username)
        set_signup_job_status(job_id, status="completed")
    except Exception as exc:
        try:
            _reset_mapping_store_cache()
        except Exception:
            pass

        with contextlib.suppress(Exception):
            privileged_client.deprovision_user(username, delete_home=True)
        with contextlib.suppress(Exception):
            privileged_client.remove_mapping(username)

        set_signup_job_status(
            job_id,
            status="failed",
            error_message=str(exc),
        )


async def _signup_worker_loop() -> None:
    last_agreement_cleanup_at = 0
    while True:
        await asyncio.to_thread(cleanup_terminal_signup_jobs)
        now = _now_seconds()
        if now - last_agreement_cleanup_at >= 24 * 60 * 60:
            await asyncio.to_thread(cleanup_expired_agreement_acceptances)
            last_agreement_cleanup_at = now
        job = await asyncio.to_thread(get_next_pending_signup_job)
        if job is None:
            await asyncio.sleep(2)
            continue
        await asyncio.to_thread(_process_signup_job_sync, job)


def _create_session_token(user_id: str, session_version: int | None = None) -> str:
    now = datetime.now(UTC)
    if session_version is None:
        record = get_user_by_id(user_id)
        session_version = int(record.auth_session_version) if record is not None else 0
    payload = {
        "sub": user_id,
        "sv": int(session_version),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=SESSION_TTL_SECONDS)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, SESSION_SECRET, algorithm="HS256")


def _decode_session_token(token: str) -> dict[str, Any] | None:
    try:
        decoded = jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
    )


def _extract_request_token(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        return token

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None


def _serialize_user(user: CurrentUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "name": user.name,
        "role": user.role,
        "mapping_username": user.mapping_username,
        "workspace_root": str(_get_user_workspace_root(user)),
        "is_temporary": bool(user.is_temporary),
        "features": {"chat_sharing": CHAT_SHARING_ENABLED},
    }


def _revocation_message(reason: str) -> str:
    if reason == "idle_timeout":
        minutes = max(int(RUNTIME_IDLE_TIMEOUT_SECONDS) // 60, 1)
        return f"Workspace slept after {minutes} minutes of inactivity. Please sign in again."
    if reason == "temporary_user_expired":
        minutes = max(int(RUNTIME_IDLE_TIMEOUT_SECONDS) // 60, 1)
        return (
            f"Temporary workspace expired after {minutes} minutes of inactivity. "
            "Your chat history and workspace data were deleted."
        )
    if reason == "password_changed":
        return "Password changed. Please sign in again."
    return "Session expired"


def _session_revocation_payload(reason: str) -> dict[str, Any]:
    return {
        "authenticated": False,
        "reason": reason,
        "message": _revocation_message(reason),
    }


def _resolve_current_user(request: Request) -> tuple[CurrentUser | None, str | None]:
    token = _extract_request_token(request)
    if not token:
        return None, None

    decoded = _decode_session_token(token)
    if not isinstance(decoded, dict):
        return None, None

    user_id = str(decoded.get("sub") or "").strip()
    if not user_id:
        return None, None

    issued_at = int(decoded.get("iat") or 0)
    record = get_user_by_id(user_id)
    if record is None or not record.active:
        return None, None
    token_session_version = int(decoded.get("sv") or 0)
    if token_session_version != int(record.auth_session_version):
        return None, "password_changed"

    runtime_state = get_runtime_state(record.id)
    revoked_after = int((runtime_state or {}).get("session_revoked_after") or 0)
    revoked_reason = str((runtime_state or {}).get("last_sleep_reason") or "").strip()
    if revoked_after and issued_at <= revoked_after:
        return None, revoked_reason or "idle_timeout"

    record_is_temporary = is_temporary_user(record.id)

    target = mapping_store.resolve_target(
        mapping_username=record.mapping_username,
        email=record.email,
        username=record.username,
    )
    if target is None:
        return None, None

    return (
        CurrentUser(
            id=record.id,
            email=record.email,
            username=record.username,
            name=record.name,
            role=record.role,
            mapping_username=record.mapping_username,
            target=target,
            is_temporary=record_is_temporary,
        ),
        None,
    )


async def get_current_user_ws(websocket: WebSocket) -> CurrentUser:
    user, revoked_reason = await asyncio.to_thread(_resolve_current_user, websocket)
    if user is None and revoked_reason:
        await websocket.close(code=4401, reason=_revocation_message(revoked_reason))
        raise RuntimeError("websocket session revoked")
    if user is None:
        await websocket.close(code=4401, reason="Not authenticated")
        raise RuntimeError("websocket unauthenticated")
    activity_recorded = await asyncio.to_thread(mark_foreground_activity, user.id)
    if activity_recorded is False:
        reason = "temporary_user_expired" if user.is_temporary else "idle_timeout"
        await websocket.close(code=4401, reason=_revocation_message(reason))
        raise RuntimeError("websocket runtime sleep is in progress")
    return user


async def get_current_user(request: Request) -> CurrentUser:
    cached_resolution = getattr(
        request.state,
        REQUEST_AUTH_RESOLUTION_STATE_KEY,
        None,
    )
    if isinstance(cached_resolution, tuple) and len(cached_resolution) == 2:
        user, revoked_reason = cached_resolution
    else:
        user, revoked_reason = await asyncio.to_thread(_resolve_current_user, request)
    if user is None and revoked_reason:
        raise HTTPException(status_code=401, detail=_revocation_message(revoked_reason))
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _normalize_relative_browser_path(path: str | None) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw or raw == "/":
        return ""

    parts: list[str] = []
    for part in PurePosixPath(raw.lstrip("/")).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise HTTPException(status_code=400, detail="Invalid path")
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _normalize_logical_absolute_path(path: Path) -> Path:
    parts: list[str] = []
    for part in PurePosixPath(path.as_posix()).parts:
        if part in {"", "/", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return Path("/").joinpath(*parts) if parts else Path("/")


def _resolve_file_browser_target(root: Path, path: str | None) -> tuple[str, Path]:
    relative = _normalize_relative_browser_path(path)
    target = (root / relative).resolve()
    return relative, target


def _expand_user_directory_input(user: CurrentUser, path: str | None) -> Path:
    home = _normalize_logical_absolute_path(user.target.home_dir.resolve())
    raw = str(path or "").strip()
    if not raw or raw == "~":
        return home
    if raw.startswith("~/"):
        return _normalize_logical_absolute_path(home / raw[2:])
    if raw.startswith("/"):
        return _normalize_logical_absolute_path(Path(raw))
    return _normalize_logical_absolute_path(home / raw)


def _resolve_file_browser_root(user: CurrentUser, requested_root: str | None) -> Path:
    home = _normalize_logical_absolute_path(user.target.home_dir.resolve())
    root = (
        home
        if not requested_root
        else _expand_user_directory_input(user, requested_root)
    )
    _authorize_file_browser_target(user, root)
    return root


def _authorize_file_browser_target(user: CurrentUser, path: Path) -> Path:
    try:
        return authorize_file_browser_path(
            path,
            home=user.target.home_dir,
            mode=_normalized_file_browser_mode(),
        )
    except FileBrowserAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _probe_path_as_user(path: Path, *, linux_user: str) -> dict[str, Any]:
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
        "    payload['is_dir'] = False\n"
        "    payload['is_file'] = False\n"
        "    payload['readable'] = False\n"
        "    payload['enterable'] = False\n"
        "print(json.dumps(payload))\n"
    )
    result = subprocess.run(
        ["runuser", "-u", linux_user, "--", "python3", "-c", script, str(path)],
        capture_output=True,
        text=True,
        check=False,
        env=interface_subprocess_env(),
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Path probe failed")
    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500, detail="Invalid path probe response"
        ) from exc
    return payload if isinstance(payload, dict) else {}


def _assert_user_can_open_directory(path: Path, *, linux_user: str) -> None:
    payload = _probe_path_as_user(path, linux_user=linux_user)
    if not payload.get("exists"):
        raise HTTPException(status_code=404, detail="Requested path does not exist")
    if not payload.get("is_dir"):
        raise HTTPException(status_code=400, detail="Requested path is not a directory")
    if not (payload.get("readable") and payload.get("enterable")):
        raise HTTPException(
            status_code=403, detail="Permission denied for this directory"
        )


def _assert_user_can_read_file(path: Path, *, linux_user: str) -> None:
    payload = _probe_path_as_user(path, linux_user=linux_user)
    if not payload.get("exists"):
        raise HTTPException(status_code=404, detail="Requested file does not exist")
    if not payload.get("is_file"):
        raise HTTPException(status_code=400, detail="Requested path is not a file")
    if not payload.get("readable"):
        raise HTTPException(status_code=403, detail="Permission denied for this file")


def _list_directory_as_user(
    path: Path,
    *,
    relative_path: str,
    linux_user: str,
) -> list[dict[str, Any]]:
    script = (
        "import json, os, pathlib, sys\n"
        "target = pathlib.Path(sys.argv[1]).resolve()\n"
        "logical_base = pathlib.PurePosixPath(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else pathlib.PurePosixPath()\n"
        "if not target.exists():\n"
        "    print(json.dumps({'error': 'not_found'}))\n"
        "    raise SystemExit(0)\n"
        "if not target.is_dir():\n"
        "    print(json.dumps({'error': 'not_directory'}))\n"
        "    raise SystemExit(0)\n"
        "if not os.access(target, os.R_OK | os.X_OK):\n"
        "    print(json.dumps({'error': 'permission_denied'}))\n"
        "    raise SystemExit(0)\n"
        "entries = []\n"
        "for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):\n"
        "    if child.name.startswith('.'):\n"
        "        continue\n"
        "    try:\n"
        "        child_stat = child.stat()\n"
        "    except PermissionError:\n"
        "        continue\n"
        "    entries.append({\n"
        "        'name': child.name,\n"
        "        'path': (logical_base / child.name).as_posix(),\n"
        "        'type': 'directory' if child.is_dir() else 'file',\n"
        "        'size': int(child_stat.st_size),\n"
        "        'modified': int(child_stat.st_mtime),\n"
        "    })\n"
        "print(json.dumps({'entries': entries}))\n"
    )
    result = subprocess.run(
        [
            "runuser",
            "-u",
            linux_user,
            "--",
            "python3",
            "-c",
            script,
            str(path),
            relative_path,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=interface_subprocess_env(),
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Directory access failed")

    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500, detail="Invalid directory access response"
        ) from exc

    error = str(payload.get("error") or "").strip()
    if error == "not_found":
        raise HTTPException(status_code=404, detail="Requested path does not exist")
    if error == "not_directory":
        raise HTTPException(status_code=400, detail="Requested path is not a directory")
    if error == "permission_denied":
        raise HTTPException(
            status_code=403, detail="Permission denied for this directory"
        )
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    return [item for item in entries if isinstance(item, dict)]


def _filter_file_browser_entries(
    user: CurrentUser,
    directory: Path,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed_entries: list[dict[str, Any]] = []
    for item in entries:
        name = str(item.get("name") or "")
        if not name:
            continue
        try:
            authorize_file_browser_path(
                directory / name,
                home=user.target.home_dir,
                mode=_normalized_file_browser_mode(),
            )
        except FileBrowserAccessError:
            continue
        allowed_entries.append(item)
    return allowed_entries


def _get_user_workspace_root(user: CurrentUser) -> Path:
    if user.target.home_dir:
        return user.target.home_dir
    return user.target.workdir


class _UserSessionDBProxy:
    def __init__(self, spec: HermesTarget):
        self.spec = spec

    def _call(self, method: str, **kwargs: Any) -> Any:
        started_at = time.monotonic()
        try:
            if os.geteuid() != 0 or os.getenv(
                "INTERFACE_FORCE_PRIVILEGED_HELPER", ""
            ).strip().lower() in {"1", "true", "yes"}:
                try:
                    return privileged_client.session_db_call(
                        self.spec.username, method, kwargs
                    )
                except PrivilegedClientError as exc:
                    if method == "fork_session" and str(exc) == "Fork target marker conflict":
                        raise ValueError(str(exc)) from exc
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            f"Failed to access Hermes session DB as "
                            f"{self.spec.linux_user}: {exc}"
                        ),
                    ) from exc

            try:
                result = run_process_group(
                    [
                        "runuser",
                        "-u",
                        self.spec.linux_user,
                        "--",
                        "env",
                        "-i",
                        f"HOME={self.spec.home_dir}",
                        f"HERMES_HOME={self.spec.hermes_home}",
                        f"TERMINAL_CWD={self.spec.workdir}",
                        f"PATH={os.environ.get('PATH', '')}",
                        "PYTHONUNBUFFERED=1",
                        DEFAULT_SESSION_DB_PYTHON,
                        "-c",
                        USER_SESSION_DB_RPC_SOURCE,
                        str(self.spec.state_db_path),
                        method,
                    ],
                    cwd=str(self.spec.workdir),
                    timeout_seconds=SESSION_DB_INNER_TIMEOUT_SECONDS,
                    input_text=json.dumps(kwargs, ensure_ascii=False),
                )
            except subprocess.TimeoutExpired as exc:
                raise HTTPException(
                    status_code=504,
                    detail=(
                        f"Hermes session DB helper timed out after "
                        f"{SESSION_DB_INNER_TIMEOUT_SECONDS:.0f} seconds"
                    ),
                ) from exc
        finally:
            elapsed = time.monotonic() - started_at
            if elapsed >= 1.0:
                LOGGER.info(
                    "Hermes session DB helper %s for %s took %.3fs",
                    method,
                    self.spec.username,
                    elapsed,
                )

        stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
        raw_payload = stdout_lines[-1] if stdout_lines else ""
        if not raw_payload:
            detail = (
                result.stderr.strip()
                or result.stdout.strip()
                or f"session DB helper exited with code {result.returncode}"
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Failed to access Hermes session DB as {self.spec.linux_user}: "
                    f"{detail}"
                ),
            )

        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            detail = result.stderr.strip() or raw_payload
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Invalid Hermes session DB helper response for "
                    f"{self.spec.linux_user}: {detail}"
                ),
            ) from exc

        if not isinstance(payload, dict) or not payload.get("ok"):
            detail = str(
                payload.get("error") or result.stderr.strip() or "unknown error"
            )
            error_type = str(payload.get("type") or "").strip()
            if error_type == "ValueError":
                raise ValueError(detail)
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Failed to access Hermes session DB as {self.spec.linux_user}: "
                    f"{detail}"
                ),
            )

        return payload.get("result")

    def list_sessions_rich(
        self,
        source: str | None = None,
        exclude_sources: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
        include_children: bool = False,
        project_compression_tips: bool = True,
        order_by_last_active: bool = False,
    ) -> list[dict[str, Any]]:
        result = self._call(
            "list_sessions_rich",
            source=source,
            exclude_sources=exclude_sources,
            limit=limit,
            offset=offset,
            include_children=include_children,
            project_compression_tips=project_compression_tips,
            order_by_last_active=order_by_last_active,
        )
        return result if isinstance(result, list) else []

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        result = self._call("get_session", session_id=session_id)
        return result if isinstance(result, dict) else None

    def resolve_session_id(self, session_id_or_prefix: str) -> str | None:
        result = self._call(
            "resolve_session_id", session_id_or_prefix=session_id_or_prefix
        )
        normalized = str(result or "").strip()
        return normalized or None

    def get_compression_tip(self, session_id: str) -> str | None:
        result = self._call("get_compression_tip", session_id=session_id)
        normalized = str(result or "").strip()
        return normalized or None

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        result = self._call("get_messages", session_id=session_id)
        return result if isinstance(result, list) else []

    def get_logical_session_context(
        self, session_id: str, *, include_messages: bool = False
    ) -> dict[str, Any]:
        result = self._call(
            "get_logical_session_context",
            session_id=session_id,
            include_messages=include_messages,
        )
        return result if isinstance(result, dict) else {}

    def set_session_title(self, session_id: str, title: str) -> bool:
        return bool(self._call("set_session_title", session_id=session_id, title=title))

    def import_shared_session(
        self,
        *,
        session_id: str,
        title: str,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        result = self._call(
            "import_shared_session",
            session_id=session_id,
            title=title,
            messages=messages,
        )
        return result if isinstance(result, dict) else {}

    def fork_session(
        self,
        *,
        target_session_id: str,
        source_session_id: str,
        request_id: str,
        fork_cursor: str,
        source_title: str,
        visible_history: list[dict[str, Any]],
        display_turns: list[dict[str, Any]],
        raw_boundary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result = self._call(
            "fork_session",
            target_session_id=target_session_id,
            source_session_id=source_session_id,
            request_id=request_id,
            fork_cursor=fork_cursor,
            source_title=source_title,
            visible_history=visible_history,
            display_turns=display_turns,
            raw_boundary=raw_boundary,
        )
        return result if isinstance(result, dict) else {}

    def delete_session(self, session_id: str) -> bool:
        return bool(self._call("delete_session", session_id=session_id))

    def close(self) -> None:
        return None


def _load_direct_session_db(db_path: Path):
    try:
        from hermes_state import SessionDB  # type: ignore
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to import Hermes session DB: {exc}"
        ) from exc
    return SessionDB(db_path=db_path)


def _load_session_db(spec: HermesTarget):
    try:
        pwd.getpwnam(spec.linux_user)
    except KeyError:
        return _load_direct_session_db(spec.state_db_path)
    return _UserSessionDBProxy(spec)


@contextlib.contextmanager
def _open_session_db(spec: HermesTarget) -> Iterator[Any]:
    db = _load_session_db(spec)
    try:
        yield db
    finally:
        db.close()


def _load_session_context_sync(
    target: HermesTarget,
    session_id: str,
    *,
    include_messages: bool = False,
) -> tuple[
    str,
    dict[str, Any] | None,
    str,
    dict[str, Any] | None,
    list[dict[str, Any]],
]:
    with _open_session_db(target) as db:
        return _resolve_logical_session_context_snapshot(
            db,
            session_id,
            include_messages=include_messages,
        )


def _normalize_session_row(session: dict[str, Any]) -> dict[str, Any]:
    started_at = int(session.get("started_at") or 0)
    last_active = int(session.get("last_active") or session.get("started_at") or 0)
    preview = str(session.get("preview") or "")
    title = str(session.get("title") or "").strip() or preview or "New chat"
    return {
        "id": str(session.get("id") or ""),
        "source": str(session.get("source") or ""),
        "model": str(session.get("model") or ""),
        "title": title,
        "preview": preview,
        "started_at": started_at,
        "last_active": last_active,
        "message_count": int(session.get("message_count") or 0),
        "tool_call_count": int(session.get("tool_call_count") or 0),
    }


def _apply_session_title_fallback(
    session: dict[str, Any], display_meta: dict[str, Any] | None
) -> dict[str, Any]:
    normalized = _normalize_session_row(session)
    hermes_title = str(session.get("title") or "").strip()
    draft_title = str((display_meta or {}).get("draft_title") or "").strip()
    if hermes_title:
        normalized["title"] = hermes_title
        normalized["title_source"] = "hermes_root"
    elif draft_title:
        normalized["title"] = draft_title
        normalized["title_source"] = "draft"
    else:
        normalized["title_source"] = "fallback"
    return normalized


def _logical_session_id_from_row(session: dict[str, Any] | None) -> str:
    if not isinstance(session, dict):
        return ""
    return str(session.get("_lineage_root_id") or session.get("id") or "").strip()


def _is_interface_managed_source(source: str | None) -> bool:
    return str(source or "").strip().lower() in INTERFACE_SESSION_SOURCES


def _is_compression_continuation(
    parent_session: dict[str, Any] | None,
    child_session: dict[str, Any] | None,
) -> bool:
    if not isinstance(parent_session, dict) or not isinstance(child_session, dict):
        return False

    parent_id = str(parent_session.get("id") or "").strip()
    if not parent_id:
        return False

    if str(child_session.get("parent_session_id") or "").strip() != parent_id:
        return False

    if str(parent_session.get("end_reason") or "").strip() != "compression":
        return False

    ended_at = float(parent_session.get("ended_at") or 0)
    started_at = float(child_session.get("started_at") or 0)
    return ended_at > 0 and started_at >= ended_at


def _find_logical_session_root_id(db: Any, session_id: str) -> str:
    current_id = str(session_id or "").strip()
    if not current_id:
        return ""

    current_session = db.get_session(current_id)
    if not current_session:
        return current_id

    for _ in range(100):
        parent_id = str(current_session.get("parent_session_id") or "").strip()
        if not parent_id:
            return current_id

        parent_session = db.get_session(parent_id)
        if not _is_compression_continuation(parent_session, current_session):
            return current_id

        current_id = parent_id
        current_session = parent_session

    return current_id


def _get_logical_session_tip_id(db: Any, logical_session_id: str) -> str:
    logical_id = str(logical_session_id or "").strip()
    if not logical_id:
        return ""
    return str(db.get_compression_tip(logical_id) or logical_id).strip()


def _resolve_tip_session_id_for_user_sync(
    user_id: str,
    logical_session_id: str,
) -> str | None:
    record = get_user_by_id(str(user_id or "").strip())
    if record is None or not record.active:
        return None

    target = mapping_store.resolve_target(
        mapping_username=record.mapping_username,
        email=record.email,
        username=record.username,
    )
    if target is None:
        return None

    with _open_session_db(target) as db:
        tip_session_id = _get_logical_session_tip_id(db, logical_session_id)
    return tip_session_id or None


async def _resolve_tip_session_id_for_user(
    user_id: str,
    logical_session_id: str,
) -> str | None:
    return await asyncio.to_thread(
        _resolve_tip_session_id_for_user_sync,
        user_id,
        logical_session_id,
    )


def _get_projected_logical_session_row(
    db: Any, logical_session_id: str
) -> dict[str, Any] | None:
    for session in db.list_sessions_rich(
        source=None,
        limit=100000,
        offset=0,
    ):
        if not _is_interface_managed_source(session.get("source")):
            continue
        if _logical_session_id_from_row(session) == logical_session_id:
            return session
    return None


def _normalize_logical_session_row(
    session: dict[str, Any],
    *,
    logical_session_id: str,
    logical_session: dict[str, Any] | None,
    display_meta: dict[str, Any] | None,
    live_state: dict[str, Any] | None = None,
    resume_session_id: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_session_row(session)
    normalized["id"] = logical_session_id
    live_tip_session_id = (
        str(live_state.get("tip_session_id") or "").strip()
        if isinstance(live_state, dict)
        else ""
    )
    normalized["resume_session_id"] = str(
        live_tip_session_id
        or resume_session_id
        or session.get("id")
        or logical_session_id
    ).strip()

    root_title = str((logical_session or {}).get("title") or "").strip()
    tip_title = str(session.get("title") or "").strip()
    draft_title = str((display_meta or {}).get("draft_title") or "").strip()
    if root_title:
        normalized["title"] = root_title
        normalized["title_source"] = "hermes_root"
    elif tip_title:
        normalized["title"] = tip_title
        normalized["title_source"] = "hermes_tip"
    elif draft_title:
        normalized["title"] = draft_title
        normalized["title_source"] = "draft"
    else:
        normalized["title_source"] = "fallback"

    normalized["live"] = live_state
    normalized["is_running"] = bool(
        isinstance(live_state, dict)
        and str(live_state.get("status") or "")
        in {"queued", "starting", "running", "awaiting_approval"}
    )
    normalized["has_pending_approval"] = bool(
        isinstance(live_state, dict)
        and isinstance(live_state.get("pending_approval"), dict)
    )

    return normalized


def _active_live_state_conflict(user_id: str) -> bool:
    for live_state in list_live_session_states(user_id).values():
        if not isinstance(live_state, dict):
            continue
        if str(live_state.get("status") or "").strip() in {
            "queued",
            "starting",
            "running",
            "awaiting_approval",
        }:
            return True
    return False


def _normalize_message_row(message: dict[str, Any]) -> dict[str, Any]:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        tool_calls = []
    return {
        "id": int(message.get("id") or 0),
        "role": str(message.get("role") or ""),
        "content": message.get("content") or "",
        "timestamp": int(message.get("timestamp") or 0),
        "tool_name": message.get("tool_name") or "",
        "tool_call_id": message.get("tool_call_id") or "",
        "tool_calls": tool_calls,
        "finish_reason": message.get("finish_reason") or "",
        "reasoning": message.get("reasoning") or "",
    }


def _normalize_display_message(message: dict[str, Any]) -> dict[str, Any]:
    message_id = str(message.get("id") or uuid.uuid4().hex)
    normalized = {
        "id": message_id,
        "role": str(message.get("role") or "assistant"),
        "content": str(message.get("content") or ""),
        "reasoningContent": str(message.get("reasoningContent") or ""),
        "toolCalls": message.get("toolCalls")
        if isinstance(message.get("toolCalls"), list)
        else [],
        "progressLines": message.get("progressLines")
        if isinstance(message.get("progressLines"), list)
        else [],
        "files": message.get("files") if isinstance(message.get("files"), list) else [],
        "timestamp": int(message.get("timestamp") or 0),
        "done": bool(message.get("done", True)),
        "source": str(message.get("source") or "display_store"),
    }
    if normalized["role"] == "assistant" and normalized["done"]:
        normalized["fork_cursor"] = message_id
    return normalized


def _normalize_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = (
        tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    )
    normalized = {
        "id": str(tool_call.get("id") or ""),
        "index": int(tool_call.get("index") or 0),
        "function": {
            "name": str(function.get("name") or ""),
            "arguments": str(function.get("arguments") or ""),
        },
    }
    if tool_call.get("type"):
        normalized["type"] = str(tool_call.get("type"))
    return normalized


def _append_progress_entries(message: dict[str, Any], entries: list[str]) -> None:
    progress_lines = message.setdefault("progressLines", [])
    if not isinstance(progress_lines, list):
        progress_lines = []
        message["progressLines"] = progress_lines
    for entry in entries:
        if not entry:
            continue
        if progress_lines and progress_lines[-1] == entry:
            continue
        progress_lines.append(entry)


def _extract_progress_lines(text: str) -> list[str]:
    return re.findall(r"`(?:💻|🔍|🧠|📁|🌐|📝|⚙️|🛠️)[^`]*`", text or "")


def _content_contains_only_progress_lines(text: str, progress_lines: list[str]) -> bool:
    normalized_text = str(text or "")
    if not normalized_text.strip() or not progress_lines:
        return False

    remainder = normalized_text
    for progress_line in progress_lines:
        if not progress_line:
            continue
        remainder = remainder.replace(progress_line, "")
    return not remainder.strip()


def _build_fallback_display_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    pending_assistant: dict[str, Any] | None = None

    def has_payload(message: dict[str, Any]) -> bool:
        return bool(
            str(message.get("content") or "").strip()
            or str(message.get("reasoningContent") or "").strip()
            or message.get("toolCalls")
            or message.get("progressLines")
            or message.get("files")
        )

    def flush_pending() -> None:
        nonlocal pending_assistant
        if pending_assistant and has_payload(pending_assistant):
            normalized.append(pending_assistant)
        pending_assistant = None

    for raw_message in messages:
        role = str(raw_message.get("role") or "")
        if role == "tool":
            continue

        if role == "user":
            flush_pending()
            content = str(raw_message.get("content") or "")
            normalized.append(
                {
                    "id": f"fallback-{raw_message.get('id') or uuid.uuid4().hex}",
                    "role": "user",
                    "content": content,
                    "reasoningContent": "",
                    "toolCalls": [],
                    "progressLines": [],
                    "files": [],
                    "timestamp": int(raw_message.get("timestamp") or 0),
                    "done": True,
                    "source": "fallback",
                }
            )
            continue

        if role != "assistant":
            continue

        raw_content = str(raw_message.get("content") or "")
        progress_lines = _extract_progress_lines(raw_content)
        display_message = {
            "id": f"fallback-{raw_message.get('id') or uuid.uuid4().hex}",
            "role": "assistant",
            "content": (
                ""
                if _content_contains_only_progress_lines(raw_content, progress_lines)
                else raw_content
            ),
            "reasoningContent": str(raw_message.get("reasoning") or ""),
            "toolCalls": raw_message.get("tool_calls")
            if isinstance(raw_message.get("tool_calls"), list)
            else [],
            "progressLines": progress_lines,
            "files": [],
            "timestamp": int(raw_message.get("timestamp") or 0),
            "done": True,
            "source": "fallback",
        }

        has_text = bool(display_message["content"].strip())
        has_tool_context = bool(
            display_message["toolCalls"] or display_message["progressLines"]
        )

        if pending_assistant is None:
            if has_tool_context and not has_text:
                pending_assistant = display_message
            elif has_payload(display_message):
                normalized.append(display_message)
            continue

        if display_message["content"].strip():
            pending_assistant["content"] = (
                f"{pending_assistant['content']}\n\n{display_message['content']}"
                if pending_assistant["content"].strip()
                else display_message["content"]
            )
        if display_message["reasoningContent"].strip():
            pending_assistant["reasoningContent"] = (
                f"{pending_assistant['reasoningContent']}\n\n{display_message['reasoningContent']}"
                if pending_assistant["reasoningContent"].strip()
                else display_message["reasoningContent"]
            )
        if display_message["toolCalls"]:
            pending_assistant["toolCalls"].extend(display_message["toolCalls"])
        if display_message["progressLines"]:
            pending_assistant["progressLines"].extend(display_message["progressLines"])
        pending_assistant["timestamp"] = max(
            int(pending_assistant.get("timestamp") or 0),
            int(display_message.get("timestamp") or 0),
        )

        if has_text:
            flush_pending()

    flush_pending()
    return normalized


def _markdown_single_line(value: Any, default: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or default


def _normalize_markdown_body(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _strip_export_progress_content(content: str) -> str:
    cleaned = _normalize_markdown_body(content)
    for progress_line in _extract_progress_lines(cleaned):
        cleaned = cleaned.replace(progress_line, "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _parse_stored_user_content_for_export(
    content: Any,
) -> tuple[str, list[dict[str, Any]]]:
    source = str(content or "")
    start = source.find(ATTACHMENT_BLOCK_START)
    end = source.find(ATTACHMENT_BLOCK_END)
    if start != 0 or end < 0:
        return source, []

    json_text = source[len(ATTACHMENT_BLOCK_START) : end].strip()
    remainder = source[end + len(ATTACHMENT_BLOCK_END) :].lstrip()
    if remainder.startswith(ATTACHMENT_HINT_LINE):
        remainder = remainder[len(ATTACHMENT_HINT_LINE) :].lstrip()

    parsed_files: list[dict[str, Any]] = []
    try:
        decoded = json.loads(json_text)
    except json.JSONDecodeError:
        decoded = []
    if isinstance(decoded, list):
        for item in decoded:
            if not isinstance(item, dict):
                continue
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            parsed_files.append(
                {
                    "name": str(item.get("name") or "attachment"),
                    "localPath": str(item.get("path") or item.get("localPath") or ""),
                    "content_type": str(item.get("content_type") or ""),
                    "size": size,
                }
            )

    return remainder, parsed_files


def _format_export_attachment(file: dict[str, Any]) -> str:
    name = _markdown_single_line(file.get("name"), "attachment")
    local_path = _markdown_single_line(
        file.get("localPath") or file.get("path") or "", ""
    )
    if local_path:
        return f"- {name} ({local_path})"
    return f"- {name}"


def _export_message_section(message: dict[str, Any]) -> tuple[str, str] | None:
    normalized = _normalize_display_message(message)
    role = str(normalized.get("role") or "").strip()
    if role == "tool":
        return None

    if role == "user":
        content, parsed_files = _parse_stored_user_content_for_export(
            normalized.get("content")
        )
        files = (
            normalized.get("files") if isinstance(normalized.get("files"), list) else []
        )
        if not files and parsed_files:
            files = parsed_files

        blocks: list[str] = []
        if files:
            attachment_lines = [
                _format_export_attachment(item)
                for item in files
                if isinstance(item, dict)
            ]
            if attachment_lines:
                blocks.append("**Attachments**\n" + "\n".join(attachment_lines))

        content_text = _normalize_markdown_body(content)
        if content_text:
            blocks.append(content_text)

        if not blocks:
            return None
        return "You", "\n\n".join(blocks)

    if role != "assistant":
        return None

    content_text = _strip_export_progress_content(str(normalized.get("content") or ""))
    if not content_text:
        return None
    return "Potato Agent", content_text


def _build_session_markdown_export(title: str, messages: list[dict[str, Any]]) -> str:
    heading = _markdown_single_line(title, "Chat export")
    parts = [f"# {heading}"]
    exported_count = 0

    for message in messages:
        if not isinstance(message, dict):
            continue
        section = _export_message_section(message)
        if section is None:
            continue
        label, body = section
        parts.extend(["", f"## {label}", "", body])
        exported_count += 1

    if exported_count == 0:
        parts.extend(["", "_No exportable chat messages._"])

    return "\n".join(parts).rstrip() + "\n"


def _export_markdown_filename(title: str, session_id: str) -> str:
    base = _markdown_single_line(title, "")
    base = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', "_", base).strip(" ._")
    if not base or base == "New chat":
        session_fragment = re.sub(r"[^A-Za-z0-9_-]+", "", str(session_id or ""))[:12]
        base = f"chat-{session_fragment}" if session_fragment else "chat"
    if len(base) > 96:
        base = base[:96].rstrip(" ._")
    if not base.lower().endswith(".md"):
        base = f"{base}.md"
    return base


def _session_export_title(
    logical_session: dict[str, Any] | None,
    projected_session: dict[str, Any] | None,
    display_meta: dict[str, Any] | None,
    session_id: str,
) -> str:
    root_title = _markdown_single_line((logical_session or {}).get("title"), "")
    tip_title = _markdown_single_line((projected_session or {}).get("title"), "")
    draft_title = _markdown_single_line((display_meta or {}).get("draft_title"), "")
    return (
        root_title
        or tip_title
        or draft_title
        or f"Chat {str(session_id or '').strip() or 'export'}"
    )


def _chat_share_error(
    status_code: int,
    code: str,
    message: str,
    *,
    retry_after: int = 0,
) -> HTTPException:
    headers = (
        {"Retry-After": str(max(1, int(retry_after)))} if int(retry_after) > 0 else None
    )
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=headers,
    )


def _chat_share_live_state_is_active(live_state: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(live_state, dict)
        and str(live_state.get("status") or "").strip() in ACTIVE_LIVE_STATUSES
    )


def _chat_share_source_revision(
    context: tuple[
        str,
        dict[str, Any] | None,
        str,
        dict[str, Any] | None,
        list[dict[str, Any]],
    ],
) -> tuple[Any, ...]:
    logical_session_id, logical_session, tip_session_id, projected_session, _ = context

    def session_revision(session: dict[str, Any] | None) -> tuple[Any, ...]:
        if not isinstance(session, dict):
            return ()
        return (
            str(session.get("id") or ""),
            str(session.get("source") or ""),
            str(session.get("title") or ""),
            int(session.get("message_count") or 0),
            float(session.get("last_active") or session.get("started_at") or 0),
            float(session.get("ended_at") or 0),
            str(session.get("end_reason") or ""),
        )

    return (
        logical_session_id,
        tip_session_id,
        session_revision(logical_session),
        session_revision(projected_session),
    )


def _redact_chat_share_private_paths(content: str, target: HermesTarget) -> str:
    redacted = force_redact_text(content)
    private_paths = {
        str(path or "").strip().rstrip("/")
        for path in (target.home_dir, target.hermes_home, target.workdir)
        if str(path or "").strip().rstrip("/") not in {"", "/"}
    }
    terminators = r"\s<>\"'`()\[\]{}"
    boundary = r"(?=$|[\s<>\"'`()\[\]{},.;:!?，。；：！？])"
    for private_path in sorted(private_paths, key=len, reverse=True):
        pattern = re.compile(
            re.escape(private_path) + rf"(?:/[^{terminators}]+)*" + boundary
        )
        redacted = pattern.sub("[private path]", redacted)
    return redacted


def _sanitize_chat_share_display_messages(
    display_messages: list[dict[str, Any]],
    target: HermesTarget,
) -> list[dict[str, str]]:
    sanitized: list[dict[str, str]] = []
    for message in display_messages:
        if not isinstance(message, dict) or not bool(message.get("done", True)):
            continue
        role = str(message.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue

        raw_content = str(message.get("content") or "")
        if role == "user":
            content, _ = _parse_stored_user_content_for_export(raw_content)
            if (
                raw_content.startswith(ATTACHMENT_BLOCK_START)
                and content == raw_content
            ):
                content = ""
            content = _normalize_markdown_body(content)
        else:
            content = _strip_export_progress_content(raw_content)

        content = _redact_chat_share_private_paths(content, target).strip()
        if content:
            sanitized.append({"role": role, "content": content})
    return sanitized


def _chat_share_source_title(
    logical_session: dict[str, Any] | None,
    projected_session: dict[str, Any] | None,
    display_meta: dict[str, Any] | None,
    target: HermesTarget,
) -> str:
    title = (
        str((logical_session or {}).get("title") or "").strip()
        or str((projected_session or {}).get("title") or "").strip()
        or str((display_meta or {}).get("draft_title") or "").strip()
    )
    return _redact_chat_share_private_paths(title, target).strip()


def _load_chat_share_snapshot_sync(
    user: CurrentUser,
    session_id: str,
) -> tuple[str, str, list[dict[str, str]]]:
    requested_session_id = str(session_id or "").strip()
    if not requested_session_id or requested_session_id == "draft":
        raise _chat_share_error(
            409,
            "session_not_shareable",
            "Open a saved chat before creating a share link.",
        )

    first_context = _load_session_context_sync(
        user.target,
        requested_session_id,
        include_messages=False,
    )
    logical_session_id, logical_session, _, projected_session, _ = first_context
    if not logical_session or not _is_interface_managed_source(
        logical_session.get("source")
    ):
        raise _chat_share_error(
            404,
            "session_not_found",
            "Session not found.",
        )

    live_before = get_live_session_state(user.id, logical_session_id)
    if _chat_share_live_state_is_active(live_before):
        raise _chat_share_error(
            409,
            "session_active",
            "Wait for the current response to finish before sharing.",
        )

    display_before = get_display_session_meta(user.id, logical_session_id)
    if display_before is None:
        raise _chat_share_error(
            409,
            "display_transcript_unavailable",
            "The visible chat transcript is not ready to share.",
        )
    raw_display_messages = display_before.get("messages")
    if not isinstance(raw_display_messages, list):
        raw_display_messages = []
    messages = _sanitize_chat_share_display_messages(
        raw_display_messages,
        user.target,
    )
    if not messages:
        raise _chat_share_error(
            409,
            "no_visible_messages",
            "The chat has no visible questions or answers to share.",
        )

    second_context = _load_session_context_sync(
        user.target,
        logical_session_id,
        include_messages=False,
    )
    display_after = get_display_session_meta(user.id, logical_session_id)
    live_after = get_live_session_state(user.id, logical_session_id)
    if (
        _chat_share_live_state_is_active(live_after)
        or live_before != live_after
        or _chat_share_source_revision(first_context)
        != _chat_share_source_revision(second_context)
        or display_before != display_after
    ):
        raise _chat_share_error(
            409,
            "session_changed",
            "The chat changed while its share snapshot was being created.",
        )

    title = _chat_share_source_title(
        logical_session,
        projected_session,
        display_before,
        user.target,
    )
    return logical_session_id, title, messages


def _import_shared_session_sync(
    target: HermesTarget,
    *,
    session_id: str,
    title: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    with _open_session_db(target) as db:
        importer = getattr(db, "import_shared_session", None)
        if callable(importer):
            result = importer(session_id=session_id, title=title, messages=messages)
        else:
            from interface.session_db_rpc import execute as execute_session_db_rpc

            result = execute_session_db_rpc(
                db,
                "import_shared_session",
                {"session_id": session_id, "title": title, "messages": messages},
            )
    if not isinstance(result, dict):
        raise RuntimeError("Shared chat import returned an invalid result")
    return result


def _fresh_shared_display_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    timestamp = _now_seconds()
    return [
        {
            "id": uuid.uuid4().hex,
            "role": str(message.get("role") or ""),
            "content": str(message.get("content") or ""),
            "reasoningContent": "",
            "toolCalls": [],
            "progressLines": [],
            "files": [],
            "timestamp": timestamp,
            "done": True,
            "source": "shared_import",
        }
        for message in messages
        if isinstance(message, dict)
        and str(message.get("role") or "") in {"user", "assistant"}
    ]


def _raw_messages_match_shared_snapshot(
    raw_messages: list[dict[str, Any]],
    shared_messages: list[dict[str, Any]],
) -> bool:
    if len(raw_messages) != len(shared_messages):
        return False
    return all(
        str(raw.get("role") or "") == str(shared.get("role") or "")
        and isinstance(raw.get("content"), str)
        and str(raw.get("content") or "") == str(shared.get("content") or "")
        for raw, shared in zip(raw_messages, shared_messages)
        if isinstance(raw, dict) and isinstance(shared, dict)
    ) and all(
        isinstance(raw, dict) and isinstance(shared, dict)
        for raw, shared in zip(raw_messages, shared_messages)
    )


def _load_imported_chat_response_sync(
    user: CurrentUser,
    imported_session_id: str,
    *,
    shared_messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    (
        logical_session_id,
        logical_session,
        tip_session_id,
        projected_session,
        raw_messages,
    ) = _load_session_context_sync(
        user.target,
        imported_session_id,
        include_messages=True,
    )
    if (
        logical_session_id != imported_session_id
        or not logical_session
        or not _is_interface_managed_source(logical_session.get("source"))
    ):
        return None

    display_messages = get_display_messages(user.id, logical_session_id)
    if display_messages is None:
        if _raw_messages_match_shared_snapshot(raw_messages, shared_messages):
            display_candidate = _fresh_shared_display_messages(shared_messages)
        else:
            display_candidate = _build_fallback_display_messages(raw_messages)
        create_display_messages_if_absent(
            user.id,
            logical_session_id,
            display_candidate,
        )
        display_messages = get_display_messages(user.id, logical_session_id)
    if display_messages is None:
        raise RuntimeError("Unable to persist the imported display transcript")

    display_meta = get_display_session_meta(user.id, logical_session_id)
    live_state = get_live_session_state(user.id, logical_session_id)
    return {
        "session": _normalize_logical_session_row(
            projected_session or logical_session,
            logical_session_id=logical_session_id,
            logical_session=logical_session,
            display_meta=display_meta,
            live_state=live_state,
            resume_session_id=tip_session_id or logical_session_id,
        ),
        "messages": [
            _normalize_display_message(message)
            for message in display_messages
            if isinstance(message, dict)
        ],
    }


def _display_message_bucket_key(message: dict[str, Any]) -> str:
    return str(message.get("role") or "").strip()


def _tool_call_merge_key(tool_call: dict[str, Any], index: int) -> str:
    normalized = _normalize_tool_call(tool_call)
    tool_id = str(normalized.get("id") or "").strip()
    if tool_id:
        return f"id:{tool_id}"

    function = (
        normalized.get("function")
        if isinstance(normalized.get("function"), dict)
        else {}
    )
    name = str(function.get("name") or "").strip()
    arguments = str(function.get("arguments") or "")
    return f"fallback:{index}:{name}:{arguments}"


def _merge_display_tool_calls(
    preferred_tool_calls: list[dict[str, Any]],
    fallback_tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = [_normalize_tool_call(item) for item in preferred_tool_calls or []]
    positions = {
        _tool_call_merge_key(item, index): index for index, item in enumerate(merged)
    }

    for fallback_index, fallback_item in enumerate(fallback_tool_calls or []):
        normalized_fallback = _normalize_tool_call(fallback_item)
        key = _tool_call_merge_key(normalized_fallback, fallback_index)
        existing_index = positions.get(key)

        if existing_index is None:
            positions[key] = len(merged)
            merged.append(normalized_fallback)
            continue

        existing = merged[existing_index]
        existing_function = (
            existing.get("function")
            if isinstance(existing.get("function"), dict)
            else {}
        )
        fallback_function = (
            normalized_fallback.get("function")
            if isinstance(normalized_fallback.get("function"), dict)
            else {}
        )

        if fallback_function.get("name"):
            existing_function["name"] = str(fallback_function["name"])

        fallback_arguments = str(fallback_function.get("arguments") or "")
        existing_arguments = str(existing_function.get("arguments") or "")
        if fallback_arguments and len(fallback_arguments) >= len(existing_arguments):
            existing_function["arguments"] = fallback_arguments

        if normalized_fallback.get("type"):
            existing["type"] = str(normalized_fallback["type"])

        existing["function"] = existing_function

    return merged


def _merge_display_message_with_fallback(
    preferred_message: dict[str, Any],
    fallback_message: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = _normalize_display_message(preferred_message)
    if not isinstance(fallback_message, dict):
        return merged

    fallback = _normalize_display_message(fallback_message)
    if str(merged.get("role") or "") != str(fallback.get("role") or ""):
        return merged

    if (
        not str(merged.get("content") or "").strip()
        and str(fallback.get("content") or "").strip()
    ):
        merged["content"] = str(fallback.get("content") or "")

    if (
        not str(merged.get("reasoningContent") or "").strip()
        and str(fallback.get("reasoningContent") or "").strip()
    ):
        merged["reasoningContent"] = str(fallback.get("reasoningContent") or "")

    merged["toolCalls"] = _merge_display_tool_calls(
        merged.get("toolCalls") if isinstance(merged.get("toolCalls"), list) else [],
        fallback.get("toolCalls")
        if isinstance(fallback.get("toolCalls"), list)
        else [],
    )

    _append_progress_entries(
        merged,
        fallback.get("progressLines")
        if isinstance(fallback.get("progressLines"), list)
        else [],
    )

    merged_files = merged.get("files") if isinstance(merged.get("files"), list) else []
    fallback_files = (
        fallback.get("files") if isinstance(fallback.get("files"), list) else []
    )
    if not merged_files and fallback_files:
        merged["files"] = fallback_files

    merged["timestamp"] = max(
        int(merged.get("timestamp") or 0),
        int(fallback.get("timestamp") or 0),
    )
    merged["done"] = bool(merged.get("done", True) and fallback.get("done", True))
    return merged


def _merge_display_transcripts(
    preferred_messages: list[dict[str, Any]],
    fallback_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    preferred = [
        _normalize_display_message(item)
        for item in preferred_messages or []
        if isinstance(item, dict)
    ]
    fallback = [
        _normalize_display_message(item)
        for item in fallback_messages or []
        if isinstance(item, dict)
    ]

    if not preferred:
        return fallback
    if not fallback:
        return preferred

    fallback_buckets: dict[str, list[dict[str, Any]]] = {}
    for message in fallback:
        fallback_buckets.setdefault(_display_message_bucket_key(message), []).append(
            message
        )

    bucket_offsets: dict[str, int] = {}
    merged: list[dict[str, Any]] = []
    for message in preferred:
        bucket_key = _display_message_bucket_key(message)
        bucket_index = bucket_offsets.get(bucket_key, 0)
        bucket_offsets[bucket_key] = bucket_index + 1
        fallback_bucket = fallback_buckets.get(bucket_key, [])
        fallback_message = (
            fallback_bucket[bucket_index]
            if bucket_index < len(fallback_bucket)
            else None
        )
        merged.append(_merge_display_message_with_fallback(message, fallback_message))

    return merged


def _collect_compression_lineage_session_ids(
    db: Any, logical_session_id: str
) -> list[str]:
    logical_id = str(logical_session_id or "").strip()
    if not logical_id:
        return []

    all_sessions = db.list_sessions_rich(
        source=None,
        include_children=True,
        project_compression_tips=False,
        limit=100000,
        offset=0,
    )
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for session in all_sessions:
        if not _is_interface_managed_source(session.get("source")):
            continue
        parent_id = str(session.get("parent_session_id") or "").strip()
        if not parent_id:
            continue
        children_by_parent.setdefault(parent_id, []).append(session)

    session_ids: list[str] = []
    current_session = db.get_session(logical_id)
    while isinstance(current_session, dict):
        current_id = str(current_session.get("id") or "").strip()
        if not current_id or current_id in session_ids:
            break
        session_ids.append(current_id)

        children = sorted(
            children_by_parent.get(current_id, []),
            key=lambda item: float(item.get("started_at") or 0),
        )
        next_session = None
        for child in children:
            if _is_compression_continuation(current_session, child):
                next_session = child
                break
        current_session = next_session

    return session_ids


def _resolve_logical_session_context(
    db: Any, session_id: str
) -> tuple[str, dict[str, Any] | None, str, dict[str, Any] | None]:
    logical_session_id, logical_session, tip_session_id, projected_session, _ = (
        _resolve_logical_session_context_snapshot(db, session_id)
    )
    return logical_session_id, logical_session, tip_session_id, projected_session


def _resolve_logical_session_context_snapshot(
    db: Any,
    session_id: str,
    *,
    include_messages: bool = False,
) -> tuple[
    str,
    dict[str, Any] | None,
    str,
    dict[str, Any] | None,
    list[dict[str, Any]],
]:
    get_context = getattr(db, "get_logical_session_context", None)
    if callable(get_context):
        result = get_context(session_id, include_messages=include_messages)
        return (
            str(result.get("logical_session_id") or "").strip(),
            result.get("logical_session")
            if isinstance(result.get("logical_session"), dict)
            else None,
            str(result.get("tip_session_id") or "").strip(),
            result.get("projected_session")
            if isinstance(result.get("projected_session"), dict)
            else None,
            result.get("messages") if isinstance(result.get("messages"), list) else [],
        )

    resolved = db.resolve_session_id(session_id)
    if not resolved:
        return "", None, "", None, []

    logical_session_id = _find_logical_session_root_id(db, resolved)
    logical_session = db.get_session(logical_session_id)
    tip_session_id = _get_logical_session_tip_id(db, logical_session_id)
    projected_session = (
        _get_projected_logical_session_row(db, logical_session_id) or logical_session
    )
    messages = db.get_messages(tip_session_id) if include_messages else []
    return (
        logical_session_id,
        logical_session,
        tip_session_id,
        projected_session,
        messages,
    )


def _session_title_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _sanitize_session_title_or_raise(raw_title: Any) -> str:
    from hermes_state import SessionDB  # type: ignore

    try:
        sanitized = SessionDB.sanitize_title(str(raw_title or ""))
    except ValueError as exc:
        raise _session_title_error(
            status_code=400,
            code="title_too_long",
            message=f"Title must be {SessionDB.MAX_TITLE_LENGTH} characters or fewer.",
        ) from exc

    if not sanitized:
        raise _session_title_error(
            status_code=400,
            code="title_empty",
            message="Title cannot be empty.",
        )

    return sanitized


def _invalidate_chat_share_session_lifecycle_sync(
    *,
    owner_user_id: str,
    logical_session_id: str,
) -> str:
    lifecycle_claim_id = uuid.uuid4().hex
    invalidate_source_session_shares(
        owner_user_id,
        logical_session_id,
        lifecycle_claim_id=lifecycle_claim_id,
    )
    return lifecycle_claim_id


def _rollback_chat_share_session_lifecycle_sync(
    *,
    owner_user_id: str,
    logical_session_id: str,
    lifecycle_claim_id: str,
) -> None:
    clear_source_session_invalidation(
        owner_user_id,
        logical_session_id,
        lifecycle_claim_id=lifecycle_claim_id,
    )


def _heartbeat_chat_share_session_lifecycle_sync(
    *,
    owner_user_id: str,
    logical_session_id: str,
    lifecycle_claim_id: str,
) -> None:
    refreshed = heartbeat_source_session_invalidation(
        owner_user_id,
        logical_session_id,
        lifecycle_claim_id=lifecycle_claim_id,
    )
    if not refreshed:
        raise RuntimeError("Chat share lifecycle claim is no longer active")


def _complete_chat_share_session_lifecycle_sync(
    *,
    owner_user_id: str,
    recipient_user_id: str,
    logical_session_id: str,
    lifecycle_claim_id: str,
) -> None:
    finalized = finalize_source_session_invalidation(
        owner_user_id,
        logical_session_id,
        lifecycle_claim_id=lifecycle_claim_id,
    )
    if not finalized:
        raise RuntimeError("Chat share lifecycle claim is no longer active")
    mark_chat_share_import_target_deleted_by_session(
        recipient_user_id=recipient_user_id,
        imported_session_id=logical_session_id,
    )


def _archive_expired_target_sync(
    target: HermesTarget,
    auth_user: Any,
    cutoff: float,
) -> int:
    archived_count = 0
    with _open_session_db(target) as db:
        sessions = db.list_sessions_rich(source=None, limit=100000, offset=0)
        for session in sessions:
            if not _is_interface_managed_source(session.get("source")):
                continue
            session_id = _logical_session_id_from_row(session)
            last_active = float(
                session.get("last_active") or session.get("started_at") or 0
            )
            if not session_id or last_active >= cutoff:
                continue
            if archived_session_exists(target.username, session_id):
                continue

            tip_session_id = _get_logical_session_tip_id(db, session_id)
            raw_messages = db.get_messages(tip_session_id)
            display_meta = (
                get_display_session_meta(auth_user.id, session_id)
                if auth_user is not None
                else None
            )
            display_messages = (
                display_meta.get("messages")
                if display_meta and isinstance(display_meta.get("messages"), list)
                else _build_fallback_display_messages(raw_messages)
            )
            draft_title = (
                str(display_meta.get("draft_title") or "") if display_meta else ""
            )

            lifecycle_claim_id = ""
            if auth_user is not None:
                lifecycle_claim_id = _invalidate_chat_share_session_lifecycle_sync(
                    owner_user_id=auth_user.id,
                    logical_session_id=session_id,
                )

            archived = archive_session_record(
                mapping_username=target.username,
                email_snapshot="",
                session=session,
                messages=raw_messages,
                display_messages=display_messages,
                draft_title=draft_title,
            )
            if not archived:
                if auth_user is not None and lifecycle_claim_id:
                    _rollback_chat_share_session_lifecycle_sync(
                        owner_user_id=auth_user.id,
                        logical_session_id=session_id,
                        lifecycle_claim_id=lifecycle_claim_id,
                    )
                continue

            try:
                for lineage_session_id in reversed(
                    _collect_compression_lineage_session_ids(db, session_id)
                ):
                    if auth_user is not None:
                        _heartbeat_chat_share_session_lifecycle_sync(
                            owner_user_id=auth_user.id,
                            logical_session_id=session_id,
                            lifecycle_claim_id=lifecycle_claim_id,
                        )
                    if not db.delete_session(lineage_session_id):
                        raise RuntimeError(
                            f"Failed to delete archived session {lineage_session_id}"
                        )
            except Exception:
                if (
                    auth_user is not None
                    and lifecycle_claim_id
                    and db.get_session(session_id) is not None
                ):
                    with contextlib.suppress(Exception):
                        _rollback_chat_share_session_lifecycle_sync(
                            owner_user_id=auth_user.id,
                            logical_session_id=session_id,
                            lifecycle_claim_id=lifecycle_claim_id,
                        )
                raise
            if auth_user is not None:
                try:
                    _complete_chat_share_session_lifecycle_sync(
                        owner_user_id=auth_user.id,
                        recipient_user_id=_chat_share_recipient_id_for_user_id(
                            auth_user.id,
                            is_temporary=is_temporary_user(auth_user.id),
                        ),
                        logical_session_id=session_id,
                        lifecycle_claim_id=lifecycle_claim_id,
                    )
                except Exception:
                    LOGGER.exception(
                        "Failed to finalize chat share state for archived session %s",
                        session_id,
                    )
                delete_display_messages(auth_user.id, session_id)
            archived_count += 1
    return archived_count


async def _archive_expired_sessions_once() -> None:
    run_id = await asyncio.to_thread(start_archive_run)
    archived_count = 0
    try:
        await asyncio.to_thread(
            cleanup_expired_archived_sessions,
            retention_days=ARCHIVE_STORAGE_RETENTION_DAYS,
        )
        cutoff = time.time() - (ARCHIVE_RETENTION_DAYS * 86400)
        auth_users = {
            user.mapping_username: user for user in await asyncio.to_thread(list_users)
        }

        for target in await asyncio.to_thread(mapping_store.load_targets):
            archived_count += await asyncio.to_thread(
                _archive_expired_target_sync,
                target,
                auth_users.get(target.username),
                cutoff,
            )

        await asyncio.to_thread(
            finish_archive_run,
            run_id,
            status="success",
            archived_count=archived_count,
        )
    except Exception as exc:
        await asyncio.to_thread(
            finish_archive_run,
            run_id,
            status="error",
            archived_count=archived_count,
            error_message=str(exc),
        )


async def _archive_scheduler_loop() -> None:
    while True:
        now = datetime.now()
        scheduled_for = now.replace(
            hour=ARCHIVE_SCHEDULE_HOUR, minute=0, second=0, microsecond=0
        )
        if scheduled_for <= now:
            scheduled_for = scheduled_for + timedelta(days=1)
        await asyncio.sleep(max((scheduled_for - now).total_seconds(), 60.0))
        try:
            await _archive_expired_sessions_once()
        except Exception:
            pass


async def _chat_share_cleanup_loop() -> None:
    while True:
        await asyncio.sleep(CHAT_SHARE_CLEANUP_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(cleanup_expired_chat_shares)
        except Exception:
            LOGGER.exception("Chat share cleanup failed")


async def _mark_temporary_cleanup_failed(user_id: str, error_message: str) -> None:
    if not user_id:
        return
    with contextlib.suppress(Exception):
        await asyncio.to_thread(
            revoke_runtime_session,
            user_id,
            reason="temporary_user_expired",
        )
    with contextlib.suppress(Exception):
        await asyncio.to_thread(
            mark_temporary_user_cleanup_attempt,
            user_id,
            status=TEMPORARY_USER_STATUS_FAILED,
            error_message=error_message,
        )


async def _delete_temporary_user_local_state(
    user_id: str,
    mapping_username: str,
) -> None:
    await asyncio.to_thread(delete_user_by_mapping_username, mapping_username)
    await asyncio.to_thread(delete_display_user_data, user_id)
    await asyncio.to_thread(delete_runtime_state, user_id)


async def _reconcile_admin_principals_once() -> None:
    try:
        await reconcile_principals_once()
    except AdminUsageUnavailable:
        LOGGER.warning("Admin usage principal reconciliation is deferred")
    except Exception:
        LOGGER.exception("Admin usage principal reconciliation failed")


async def _close_temporary_user_bridge(user_id: str) -> bool:
    registry: TuiGatewayBridgeRegistry | None = getattr(
        app.state, "tui_gateway_bridges", None
    )
    if registry is None:
        return True
    close_for_cleanup = getattr(registry, "close_for_cleanup", None)
    if close_for_cleanup is not None:
        result = await close_for_cleanup(user_id)
        return result is not False
    closed = await registry.close_for_reconfigure(user_id)
    return bool(closed)


async def _cleanup_temporary_user_candidate(
    auth_user: Any, target: HermesTarget
) -> bool:
    user_id = str(getattr(auth_user, "id", "") or "").strip()
    mapping_username = str(
        getattr(target, "username", "")
        or getattr(auth_user, "mapping_username", "")
        or ""
    ).strip()
    if not user_id or not mapping_username:
        return False

    claimed = await asyncio.to_thread(
        claim_temporary_user_cleanup,
        user_id,
        idle_timeout_seconds=RUNTIME_IDLE_TIMEOUT_SECONDS,
        cleanup_retry_seconds=TEMPORARY_USER_CLEANUP_RETRY_SECONDS,
    )
    if claimed is None:
        return False
    claimed_at = int(claimed.get("last_cleanup_attempt_at") or 0)

    async def claim_is_valid() -> bool:
        return await asyncio.to_thread(
            temporary_cleanup_claim_is_valid,
            user_id,
            claimed_at=claimed_at,
            idle_timeout_seconds=RUNTIME_IDLE_TIMEOUT_SECONDS,
        )

    async def restore_active_claim() -> None:
        await asyncio.to_thread(
            release_temporary_cleanup_claim,
            user_id,
            claimed_at=claimed_at,
        )

    try:
        if not await claim_is_valid():
            await restore_active_claim()
            return False
        bridge_closed = await _close_temporary_user_bridge(user_id)
        if not bridge_closed:
            await restore_active_claim()
            return False
        if not await claim_is_valid():
            await restore_active_claim()
            return False
        try:
            has_background_jobs = await asyncio.to_thread(
                _has_active_background_processes_for_target,
                target,
            )
        except Exception:
            LOGGER.exception(
                "Keeping temporary runtime active because background process check failed for %s",
                mapping_username,
            )
            await restore_active_claim()
            return False
        if has_background_jobs:
            await restore_active_claim()
            try:
                await asyncio.to_thread(mark_background_activity, user_id)
            except Exception:
                LOGGER.exception(
                    "Failed to record background activity for temporary user %s",
                    mapping_username,
                )
            return False
        if not await claim_is_valid():
            await restore_active_claim()
            return False
        await asyncio.to_thread(
            invalidate_user_chat_share_data,
            user_id,
            recipient_user_id=_chat_share_recipient_id_for_user_id(
                user_id,
                is_temporary=True,
            ),
        )
        await asyncio.to_thread(
            revoke_runtime_session,
            user_id,
            reason="temporary_user_expired",
        )
        retired = await asyncio.to_thread(
            retire_temporary_user_identity,
            user_id,
        )
        if not retired:
            raise RuntimeError("Temporary usage identity is unavailable")
        await asyncio.to_thread(
            privileged_client.deprovision_user,
            mapping_username,
            delete_home=True,
        )
        await asyncio.to_thread(privileged_client.remove_mapping, mapping_username)
        _reset_mapping_store_cache()
        await _delete_temporary_user_local_state(user_id, mapping_username)
        LOGGER.info("Cleaned up temporary user %s", mapping_username)
        return True
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        await _mark_temporary_cleanup_failed(user_id, detail)
        LOGGER.exception(
            "Failed to clean up temporary user %s (%s)",
            mapping_username,
            user_id,
        )
        return False


def _stop_idle_runtime_candidate(auth_user: Any, target: HermesTarget) -> bool:
    if not target.systemd_service or not target.username:
        return False
    if not os.path.exists(f"/etc/systemd/system/{target.systemd_service}"):
        return False

    if os.geteuid() != 0 or privileged_client.force_helper:
        result = privileged_client.stop_idle_runtime(
            target.username,
            auth_user.id,
            RUNTIME_IDLE_TIMEOUT_SECONDS,
        )
        stopped = bool(result.get("stopped"))
        if stopped:
            LOGGER.info(
                "Stopped idle runtime for %s after %s seconds",
                target.username,
                RUNTIME_IDLE_TIMEOUT_SECONDS,
            )
        elif result.get("reason"):
            LOGGER.info(
                "Kept idle runtime for %s active: %s",
                target.username,
                result.get("reason"),
            )
        return stopped

    with service_operation_lock(target.systemd_service):
        eligibility = get_runtime_idle_eligibility(
            auth_user.id,
            idle_timeout_seconds=RUNTIME_IDLE_TIMEOUT_SECONDS,
        )
        if not eligibility or not bool(eligibility.get("eligible")):
            return False
        try:
            if _has_active_background_processes_for_target(target):
                mark_background_activity(auth_user.id)
                return False
        except Exception:
            LOGGER.exception(
                "Keeping runtime active because background process check failed for %s",
                target.username,
            )
            return False
        claim_id = claim_runtime_sleep(
            auth_user.id,
            idle_timeout_seconds=RUNTIME_IDLE_TIMEOUT_SECONDS,
        )
        if claim_id is None:
            return False
        try:
            try:
                if _has_active_background_processes_for_target(target):
                    release_runtime_sleep_claim(auth_user.id, claim_id=claim_id)
                    claim_id = ""
                    mark_background_activity(auth_user.id)
                    return False
            except Exception:
                LOGGER.exception(
                    "Keeping runtime active because background process check failed for %s",
                    target.username,
                )
                return False
            service_active = is_service_active(target.systemd_service)
            if not runtime_sleep_claim_is_valid(
                auth_user.id,
                claim_id=claim_id,
                idle_timeout_seconds=RUNTIME_IDLE_TIMEOUT_SECONDS,
            ):
                return False
            if not service_active:
                revoke_runtime_session(
                    auth_user.id,
                    reason="idle_timeout",
                )
                claim_id = ""
                LOGGER.info(
                    "Revoked idle runtime session for inactive service %s after %s seconds",
                    target.username,
                    RUNTIME_IDLE_TIMEOUT_SECONDS,
                )
                return True
            if os.geteuid() == 0:
                stop_service(target.systemd_service)
            else:
                privileged_client.stop_runtime(target.username)
            revoke_runtime_session(
                auth_user.id,
                reason="idle_timeout",
            )
            claim_id = ""
            LOGGER.info(
                "Stopped idle runtime for %s after %s seconds",
                target.username,
                RUNTIME_IDLE_TIMEOUT_SECONDS,
            )
            return True
        finally:
            if claim_id:
                release_runtime_sleep_claim(auth_user.id, claim_id=claim_id)


async def _run_runtime_idle_check_once() -> int:
    await asyncio.gather(
        asyncio.to_thread(cleanup_expired_runtime_leases),
        asyncio.to_thread(cleanup_turn_submission_receipts),
    )
    candidates, temporary_candidates = await asyncio.gather(
        asyncio.to_thread(
            list_idle_runtime_candidates,
            idle_timeout_seconds=RUNTIME_IDLE_TIMEOUT_SECONDS,
        ),
        asyncio.to_thread(
            list_idle_temporary_user_candidates,
            idle_timeout_seconds=RUNTIME_IDLE_TIMEOUT_SECONDS,
            cleanup_retry_seconds=TEMPORARY_USER_CLEANUP_RETRY_SECONDS,
        ),
    )
    if not candidates and not temporary_candidates:
        return 0

    stopped = 0
    users_by_id = {user.id: user for user in await asyncio.to_thread(list_users)}
    for candidate in candidates:
        auth_user = users_by_id.get(str(candidate.get("user_id") or ""))
        if auth_user is None:
            continue
        if await asyncio.to_thread(is_temporary_user, auth_user.id):
            continue
        target = await asyncio.to_thread(
            mapping_store.resolve_target,
            mapping_username=auth_user.mapping_username,
            email=auth_user.email,
            username=auth_user.username,
        )
        if target is None:
            continue
        if await asyncio.to_thread(_stop_idle_runtime_candidate, auth_user, target):
            stopped += 1

    for candidate in temporary_candidates:
        user_id = str(candidate.get("user_id") or "").strip()
        if not user_id:
            continue
        auth_user = users_by_id.get(user_id)
        if auth_user is None:
            try:
                await asyncio.to_thread(
                    invalidate_user_chat_share_data,
                    user_id,
                    recipient_user_id=_chat_share_recipient_id_for_user_id(
                        user_id,
                        is_temporary=True,
                    ),
                )
                await asyncio.to_thread(delete_temporary_user_record, user_id)
            except Exception as exc:
                await _mark_temporary_cleanup_failed(
                    user_id,
                    str(exc) or type(exc).__name__,
                )
            continue
        target = await asyncio.to_thread(
            mapping_store.resolve_target,
            mapping_username=str(
                candidate.get("mapping_username") or auth_user.mapping_username
            ),
            email=auth_user.email,
            username=auth_user.username,
        )
        if target is None:
            cleanup_status = str(candidate.get("cleanup_status") or "")
            if cleanup_status != TEMPORARY_USER_STATUS_ACTIVE:
                mapping_username = str(
                    candidate.get("mapping_username")
                    or auth_user.mapping_username
                    or ""
                ).strip()
                try:
                    await asyncio.to_thread(
                        invalidate_user_chat_share_data,
                        user_id,
                        recipient_user_id=_chat_share_recipient_id_for_user_id(
                            user_id,
                            is_temporary=True,
                        ),
                    )
                    await _delete_temporary_user_local_state(
                        user_id,
                        mapping_username,
                    )
                except Exception as exc:
                    await _mark_temporary_cleanup_failed(
                        user_id,
                        str(exc) or type(exc).__name__,
                    )
                    continue
                LOGGER.info(
                    "Finished local cleanup for temporary user %s after mapping removal",
                    mapping_username,
                )
                stopped += 1
                continue
            await _mark_temporary_cleanup_failed(
                user_id,
                "No Hermes runtime is mapped to this temporary user.",
            )
            continue
        if await _cleanup_temporary_user_candidate(auth_user, target):
            stopped += 1
    return stopped


async def _runtime_idle_scheduler_loop() -> None:
    while True:
        await asyncio.sleep(max(RUNTIME_IDLE_CHECK_INTERVAL_SECONDS, 5))
        try:
            await _run_runtime_idle_check_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Runtime idle scheduler failed")


def _sanitize_filename(filename: str) -> str:
    base = Path(filename or "upload.bin").name
    cleaned = FILENAME_SANITIZE_RE.sub("_", base).strip("._")
    return cleaned or "upload.bin"


def _attachment_content_disposition(filename: str) -> str:
    base = Path(filename or "download.bin").name or "download.bin"
    quoted = quote(base, safe="")
    if quoted != base:
        return f"attachment; filename*=utf-8''{quoted}"
    return f'attachment; filename="{base}"'


def _inline_content_disposition(filename: str) -> str:
    base = Path(filename or "preview.bin").name or "preview.bin"
    quoted = quote(base, safe="")
    if quoted != base:
        return f"inline; filename*=utf-8''{quoted}"
    return f'inline; filename="{base}"'


def _guess_preview_mime_type(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".yaml" or suffix == ".yml":
        return "application/yaml"
    mime_type, _ = mimetypes.guess_type(filename or "")
    return mime_type or "application/octet-stream"


def _is_text_preview_filename(filename: str) -> bool:
    path = Path(filename or "")
    name = path.name.lower()
    suffixes = {suffix.lower() for suffix in path.suffixes}
    if suffixes & TEXT_PREVIEW_EXTENSIONS:
        return True
    if name in TEXT_PREVIEW_FILENAMES:
        return True
    if name.endswith("_log"):
        return True
    return False


def _classify_preview_type(filename: str, mime_type: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    normalized_mime = str(mime_type or "").split(";", 1)[0].strip().lower()

    if suffix == ".pdf" or normalized_mime == "application/pdf":
        return "pdf"
    if suffix in IMAGE_PREVIEW_EXTENSIONS or normalized_mime.startswith("image/"):
        return "image"
    if (
        normalized_mime.startswith("text/")
        or _is_text_preview_filename(filename)
        or normalized_mime
        in {
            "application/javascript",
            "application/json",
            "application/sql",
            "application/toml",
            "application/xml",
            "application/yaml",
            "application/x-httpd-php",
            "application/x-javascript",
            "application/x-sh",
            "application/x-yaml",
        }
    ):
        return "text"
    return "unsupported"


def _normalize_file_size(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


@dataclass
class _OpenFileStream:
    process: subprocess.Popen[bytes]
    metadata: dict[str, Any]
    prefetched: bytes = b""


class _DownloadLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._by_user: dict[str, int] = {}

    def acquire(self, user_id: str) -> bool:
        with self._lock:
            user_count = self._by_user.get(user_id, 0)
            if (
                self._total >= MAX_DOWNLOADS_GLOBAL
                or user_count >= MAX_DOWNLOADS_PER_USER
            ):
                return False
            self._total += 1
            self._by_user[user_id] = user_count + 1
            return True

    def release(self, user_id: str) -> None:
        with self._lock:
            user_count = self._by_user.get(user_id, 0)
            if user_count <= 0:
                return
            self._total = max(0, self._total - 1)
            if user_count == 1:
                self._by_user.pop(user_id, None)
            else:
                self._by_user[user_id] = user_count - 1


_DOWNLOAD_LIMITER = _DownloadLimiter()


def _terminate_file_stream(opened: _OpenFileStream) -> None:
    process = opened.process
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=2)
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()


def _read_file_stream_metadata(
    process: subprocess.Popen[bytes],
) -> tuple[dict[str, Any], bytes]:
    if process.stdout is None:
        raise RuntimeError("file stream did not provide stdout")
    fd = process.stdout.fileno()
    buffer = bytearray()
    deadline = time.monotonic() + FILE_STREAM_IDLE_TIMEOUT_SECONDS
    while b"\n" not in buffer:
        if len(buffer) > FILE_STREAM_METADATA_MAX_BYTES:
            raise RuntimeError("file stream metadata is too large")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("file stream metadata timed out")
        readable, _, _ = select.select([fd], [], [], remaining)
        if not readable:
            raise RuntimeError("file stream metadata timed out")
        chunk = os.read(fd, min(4096, FILE_STREAM_METADATA_MAX_BYTES + 1 - len(buffer)))
        if not chunk:
            raise RuntimeError("file stream ended before metadata")
        buffer.extend(chunk)

    header, prefetched = bytes(buffer).split(b"\n", 1)
    if len(header) > FILE_STREAM_METADATA_MAX_BYTES:
        raise RuntimeError("file stream metadata is too large")
    try:
        metadata = json.loads(header.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid file stream metadata") from exc
    if not isinstance(metadata, dict) or metadata.get("protocol") != "file-stream-v2":
        raise RuntimeError("invalid file stream protocol")
    size = metadata.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise RuntimeError("invalid file stream size")
    filename = Path(str(metadata.get("filename") or "download.bin")).name
    if not filename:
        filename = "download.bin"
    metadata["filename"] = filename
    return metadata, prefetched


def _open_file_stream(command: list[str]) -> _OpenFileStream:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env=interface_subprocess_env(),
    )
    opened = _OpenFileStream(process=process, metadata={})
    try:
        metadata, prefetched = _read_file_stream_metadata(process)
        opened.metadata = metadata
        opened.prefetched = prefetched
        return opened
    except BaseException as exc:
        stderr = b""
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=0.1)
        if process.poll() is not None and process.stderr is not None:
            stderr = process.stderr.read(4096)
        _terminate_file_stream(opened)
        if MAINTENANCE_ERROR_MARKER.encode("ascii") in stderr:
            raise PrivilegedMaintenanceError(MAINTENANCE_ERROR_MESSAGE) from exc
        raise


def _file_stream_command_for_user(
    user: CurrentUser,
    *,
    root: str | None,
    path: str,
) -> list[str]:
    mode = _normalized_file_browser_mode()
    browser_root = _resolve_file_browser_root(user, root)
    relative_path = _normalize_relative_browser_path(path)
    requested_path = browser_root / relative_path
    _authorize_file_browser_target(user, requested_path)

    if _use_privileged_file_helper():
        return privileged_client.file_stream_v2_command(
            user.target.username,
            mode=mode,
            root=str(browser_root),
            path=relative_path,
        )
    return build_file_stream_worker_command(
        linux_user=user.target.linux_user,
        home=user.target.home_dir,
        browser_root=browser_root,
        requested_path=requested_path,
        mode=mode,
        public_data_root=file_browser_policy.DEFAULT_PUBLIC_DATA_PATH,
    )


def _open_user_file_stream(
    user: CurrentUser,
    *,
    root: str | None,
    path: str,
) -> _OpenFileStream:
    return _open_file_stream(_file_stream_command_for_user(user, root=root, path=path))


def _preview_info_from_metadata(
    metadata: dict[str, Any],
    *,
    path: str,
    root: str | None,
) -> dict[str, Any]:
    filename = Path(str(metadata.get("filename") or path or "preview.bin")).name
    size = _normalize_file_size(metadata.get("size"))
    mime_type = _guess_preview_mime_type(filename)
    preview_type = _classify_preview_type(filename, mime_type)
    too_large = size > MAX_PREVIEW_SIZE_BYTES
    return {
        "filename": filename,
        "size": size,
        "modified": _normalize_file_size(metadata.get("modified")),
        "mime_type": mime_type,
        "preview_type": "too_large" if too_large else preview_type,
        "raw_preview_type": preview_type,
        "preview_limit": MAX_PREVIEW_SIZE_BYTES,
        "too_large": too_large,
        "download_url": _build_file_download_url(path=path, root=root),
    }


async def _load_file_preview_context(
    *,
    path: str,
    root: str | None,
    user: CurrentUser,
) -> dict[str, Any]:
    try:
        opened = await asyncio.to_thread(
            _open_user_file_stream,
            user,
            root=root,
            path=path,
        )
    except PrivilegedMaintenanceError as exc:
        raise HTTPException(status_code=503, detail=MAINTENANCE_ERROR_MESSAGE) from exc
    except (OSError, RuntimeError, PrivilegedClientError) as exc:
        raise HTTPException(status_code=403, detail="File access denied") from exc
    try:
        return _preview_info_from_metadata(opened.metadata, path=path, root=root)
    finally:
        await asyncio.to_thread(_terminate_file_stream, opened)


def _build_file_download_url(*, path: str, root: str | None) -> str:
    query = f"path={quote(str(path or '').lstrip('/'), safe='')}"
    if root:
        query += f"&root={quote(str(root), safe='')}"
    return f"/api/files/download?{query}"


def _build_file_preview_content_url(*, path: str, root: str | None) -> str:
    query = f"path={quote(str(path or '').lstrip('/'), safe='')}"
    if root:
        query += f"&root={quote(str(root), safe='')}"
    return f"/api/files/preview/content?{query}"


def _ensure_previewable_size(info: dict[str, Any]) -> None:
    if bool(info.get("too_large")):
        raise HTTPException(
            status_code=413,
            detail=(
                f"File is too large to preview "
                f"(> {MAX_PREVIEW_SIZE_BYTES // (1024 * 1024)} MB)."
            ),
        )


def _iter_open_file_stream(
    opened: _OpenFileStream,
    *,
    max_bytes: int | None = None,
    download_user_id: str | None = None,
) -> Iterator[bytes]:
    process = opened.process
    assert process.stdout is not None
    total = 0
    try:
        if opened.prefetched:
            total += len(opened.prefetched)
            if max_bytes is not None and total > max_bytes:
                raise HTTPException(
                    status_code=413, detail="File is too large to preview."
                )
            yield opened.prefetched
            opened.prefetched = b""

        fd = process.stdout.fileno()
        while True:
            readable, _, _ = select.select(
                [fd],
                [],
                [],
                FILE_STREAM_IDLE_TIMEOUT_SECONDS,
            )
            if not readable:
                raise RuntimeError("file stream timed out")
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise HTTPException(
                    status_code=413, detail="File is too large to preview."
                )
            yield chunk
        returncode = process.wait(timeout=2)
        if returncode != 0 or total != int(opened.metadata["size"]):
            raise RuntimeError("file stream failed")
    finally:
        _terminate_file_stream(opened)
        if download_user_id is not None:
            _DOWNLOAD_LIMITER.release(download_user_id)


def _read_open_file_stream_bytes(opened: _OpenFileStream, max_bytes: int) -> bytes:
    return b"".join(_iter_open_file_stream(opened, max_bytes=max_bytes))


async def _get_tui_bridge_for_user(user: CurrentUser) -> TuiGatewayBridge:
    registry: TuiGatewayBridgeRegistry = app.state.tui_gateway_bridges
    bridge = await registry.get_or_create(user.id, user.target)
    session_run_manager: SessionRunManager = app.state.session_run_manager
    await session_run_manager.attach_bridge(bridge)
    return bridge


def _use_privileged_file_helper() -> bool:
    return os.geteuid() != 0 or privileged_client.force_helper


def _has_active_background_processes_for_target(target: HermesTarget) -> bool:
    if os.geteuid() == 0 and not privileged_client.force_helper:
        return has_active_background_processes(target)
    return privileged_client.has_active_background_processes(target.username)


def _get_active_model_id_for_user(
    target: HermesTarget,
    model_options: Any,
    *,
    config: dict[str, Any] | None = None,
) -> str:
    if os.geteuid() == 0 and not privileged_client.force_helper:
        return get_active_model_option_id(
            target, model_options, proxy_base_url=get_model_proxy_base_url(config)
        )
    return privileged_client.get_active_model_id(target.username)


app = FastAPI(title="Potato Interface")
app.mount(
    "/static/spatial",
    StaticFiles(directory=SPATIAL_STATIC_DIR),
    name="spatial-static",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(bulk_rnaseq_viewer_router)
app.include_router(daily_updates_router)
app.include_router(gene_catalog_router)
app.include_router(genome_browser_router)
app.include_router(pan_genome_router)
app.include_router(spatial_viewer_router)
app.include_router(wgcna_viewer_router)
app.include_router(admin_router)


def _should_refresh_activity_for_request(request: Request) -> bool:
    path = str(request.scope.get("path") or "")
    if not path.startswith("/api/"):
        return False
    if path.startswith("/api/spatial/"):
        return False
    if path.startswith("/api/wgcna/"):
        return False
    if path.startswith("/api/bulk-rnaseq/"):
        return False
    if path == "/api/daily-updates":
        return False
    if path.startswith("/api/genome-browser/"):
        return False
    if path.startswith("/api/pan-genome/"):
        return False
    if path == "/api/v1/gene-catalog":
        return False
    if path == "/api/v1/genes" or path.startswith("/api/v1/genes/"):
        return False
    if getattr(request, "method", "GET") == "GET" and re.fullmatch(
        r"/api/sessions/[^/]+/live", path
    ):
        return False
    if getattr(request, "method", "GET") == "GET" and re.fullmatch(
        r"/api/turns/[^/]+", path
    ):
        return False
    query_params = getattr(request, "query_params", {})
    if (
        getattr(request, "method", "GET") == "GET"
        and re.fullmatch(r"/api/sessions/[^/]+", path)
        and str(query_params.get("background") or "") == "1"
    ):
        return False
    if path in ACTIVITY_REFRESH_EXCLUDED_PATHS:
        return False
    if path.startswith("/api/auth/signup/"):
        return False
    return True


def _interface_request_body_limit(scope: dict[str, Any]) -> int:
    path = str(scope.get("path") or "")
    if path == "/api/feedback":
        return 32 * 1024
    if path == "/api/files/upload":
        return HARD_MAX_UPLOAD_BYTES + 1024 * 1024
    if re.fullmatch(r"/api/sessions/[^/]+/display", path):
        return 16 * 1024 * 1024
    return 2 * 1024 * 1024


def _interface_request_body_error_headers(
    scope: dict[str, Any],
    status_code: int,
) -> list[tuple[bytes, bytes]]:
    del status_code
    path = str(scope.get("path") or "")
    if path.startswith("/api/chat-shares/") or re.fullmatch(
        r"/api/sessions/[^/]+/shares", path
    ):
        return [
            (b"cache-control", b"no-store"),
            (b"pragma", b"no-cache"),
            (b"x-content-type-options", b"nosniff"),
        ]
    return []


@app.middleware("http")
async def refresh_authenticated_activity(request: Request, call_next):
    if _should_refresh_activity_for_request(request):
        user, revoked_reason = await asyncio.to_thread(_resolve_current_user, request)
        setattr(
            request.state,
            REQUEST_AUTH_RESOLUTION_STATE_KEY,
            (user, revoked_reason),
        )
        if user is not None:
            activity_recorded = await asyncio.to_thread(
                mark_foreground_activity, user.id
            )
            if activity_recorded is False:
                reason = (
                    "temporary_user_expired" if user.is_temporary else "idle_timeout"
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": _revocation_message(reason)},
                )
    return await call_next(request)


@app.middleware("http")
async def protect_admin_responses(request: Request, call_next):
    response = await call_next(request)
    request_path = str(request.scope.get("path") or "")
    if request_path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
    if request_path.startswith("/api/chat-shares/") or re.fullmatch(
        r"/api/sessions/[^/]+/shares", request_path
    ):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.on_event("startup")
async def on_startup() -> None:
    ensure_auth_db()
    cleanup_terminal_signup_jobs()
    cleanup_expired_agreement_acceptances()
    ensure_display_store()
    app.state.turn_submission_receipt_cleanup = cleanup_turn_submission_receipts()
    app.state.stale_live_sessions_failed = mark_active_live_session_states_failed(
        "interface restarted before the run completed"
    )
    ensure_archive_db()
    app.state.archive_cleanup = await asyncio.to_thread(
        cleanup_expired_archived_sessions,
        retention_days=ARCHIVE_STORAGE_RETENTION_DAYS,
    )
    ensure_feedback_store()
    app.state.feedback_cleanup = cleanup_feedback_submissions()
    ensure_chat_share_store()
    app.state.chat_share_cleanup = cleanup_expired_chat_shares()
    ensure_runtime_state_store()
    app.state.tui_gateway_bridges = TuiGatewayBridgeRegistry()
    app.state.session_run_manager = SessionRunManager(
        tip_resolver=_resolve_tip_session_id_for_user
    )
    app.state.archive_scheduler_task = asyncio.create_task(_archive_scheduler_loop())
    app.state.signup_worker_task = asyncio.create_task(_signup_worker_loop())
    app.state.runtime_idle_scheduler_task = asyncio.create_task(
        _runtime_idle_scheduler_loop()
    )
    app.state.chat_share_cleanup_task = asyncio.create_task(_chat_share_cleanup_loop())
    app.state.system_resource_sampler = SystemResourceSampler()
    app.state.system_resource_sampler_task = asyncio.create_task(
        app.state.system_resource_sampler.run()
    )
    app.state.admin_principal_reconciliation_task = asyncio.create_task(
        _reconcile_admin_principals_once()
    )


@app.on_event("shutdown")
async def on_shutdown() -> None:
    archive_scheduler_task = getattr(app.state, "archive_scheduler_task", None)
    if archive_scheduler_task is not None:
        archive_scheduler_task.cancel()
    signup_worker_task = getattr(app.state, "signup_worker_task", None)
    if signup_worker_task is not None:
        signup_worker_task.cancel()
    runtime_idle_scheduler_task = getattr(
        app.state, "runtime_idle_scheduler_task", None
    )
    if runtime_idle_scheduler_task is not None:
        runtime_idle_scheduler_task.cancel()
    chat_share_cleanup_task = getattr(app.state, "chat_share_cleanup_task", None)
    if chat_share_cleanup_task is not None:
        chat_share_cleanup_task.cancel()
    system_resource_sampler_task = getattr(
        app.state, "system_resource_sampler_task", None
    )
    if system_resource_sampler_task is not None:
        system_resource_sampler_task.cancel()
    reconciliation_task = getattr(
        app.state, "admin_principal_reconciliation_task", None
    )
    if reconciliation_task is not None:
        reconciliation_task.cancel()
    pending_tasks = [
        task
        for task in (
            archive_scheduler_task,
            signup_worker_task,
            runtime_idle_scheduler_task,
            chat_share_cleanup_task,
            system_resource_sampler_task,
            reconciliation_task,
        )
        if task is not None
    ]
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)
    session_run_manager: SessionRunManager | None = getattr(
        app.state, "session_run_manager", None
    )
    if session_run_manager is not None:
        await session_run_manager.shutdown()
    bridge_registry: TuiGatewayBridgeRegistry | None = getattr(
        app.state, "tui_gateway_bridges", None
    )
    if bridge_registry is not None:
        await bridge_registry.close_all()


@app.get("/health")
async def healthcheck() -> dict[str, Any]:
    return {"status": True}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    if not FAVICON_PATH.is_file():
        raise HTTPException(status_code=404, detail="Favicon not found")
    return FileResponse(FAVICON_PATH)


@app.get("/about", include_in_schema=False)
async def serve_about() -> FileResponse:
    file_path = ABOUT_DIR / "index.html"
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="About page not found")
    return FileResponse(file_path)


@app.post("/api/feedback")
async def submit_feedback(payload: FeedbackRequest) -> dict[str, bool]:
    message, contact_email, page_path = _validate_feedback_payload(payload)
    submitted_at_seconds = _now_seconds()
    try:
        claim = await asyncio.to_thread(
            claim_feedback_submission,
            message=message,
            contact_email=contact_email,
            now=submitted_at_seconds,
        )
    except (OSError, sqlite3.Error):
        LOGGER.exception("Feedback metadata claim failed")
        raise HTTPException(
            status_code=503,
            detail="Feedback is temporarily unavailable. Please try again later.",
        ) from None

    if not claim.accepted or claim.submission_id is None:
        raise HTTPException(
            status_code=429,
            detail="Too many feedback submissions. Please try again later.",
            headers={"Retry-After": str(max(1, claim.retry_after))},
        )

    submission_id = claim.submission_id
    submitted_at = (
        datetime.fromtimestamp(submitted_at_seconds, tz=UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    try:
        result = await send_feedback_email(
            message=message,
            contact_email=contact_email,
            page_path=page_path,
            submitted_at=submitted_at,
            submission_id=submission_id,
        )
    except (MailerConfigurationError, MailerDeliveryError) as exc:
        try:
            await asyncio.to_thread(
                finish_feedback_submission,
                submission_id,
                status="failed",
                now=submitted_at_seconds,
            )
        except (LookupError, OSError, sqlite3.Error, ValueError):
            LOGGER.exception(
                "Feedback failure metadata update failed: submission_id=%s",
                submission_id,
            )
        LOGGER.warning(
            "Feedback delivery failed: submission_id=%s error_type=%s",
            submission_id,
            exc.error_type
            if isinstance(exc, MailerDeliveryError) and exc.error_type
            else type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Feedback is temporarily unavailable. Please try again later.",
        ) from None

    try:
        await asyncio.to_thread(
            finish_feedback_submission,
            submission_id,
            status="sent",
            resend_email_id=result.email_id,
            now=submitted_at_seconds,
        )
    except (LookupError, OSError, sqlite3.Error, ValueError):
        LOGGER.exception(
            "Feedback success metadata update failed: submission_id=%s",
            submission_id,
        )
    return {"ok": True}


@app.get("/api/auth/session")
async def auth_session(request: Request) -> dict[str, Any]:
    user, revoked_reason = await asyncio.to_thread(_resolve_current_user, request)
    if user is None and revoked_reason:
        return _session_revocation_payload(revoked_reason)
    if user is None:
        return {"authenticated": False}
    return {"authenticated": True, "user": _serialize_user(user)}


@app.post("/api/runtime/start")
async def start_runtime(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        runtime = await asyncio.to_thread(privileged_client.ensure_runtime, user.target)
        await asyncio.to_thread(mark_runtime_started, user.id)
        await asyncio.to_thread(clear_session_revocation, user.id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "ok": True,
        "runtime": runtime,
        "user": _serialize_user(user),
    }


@app.websocket("/api/tui/ws")
async def tui_gateway_websocket(websocket: WebSocket) -> None:
    user = await get_current_user_ws(websocket)
    bridge = await _get_tui_bridge_for_user(user)
    await websocket.accept()
    if not await bridge.add_subscriber(websocket):
        await websocket.close(code=4429, reason="Too many active connections")
        return

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > MAX_WEBSOCKET_RPC_BYTES:
                await websocket.close(code=1009, reason="RPC message too large")
                return
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "rpc.error",
                            "payload": {"message": "Invalid JSON"},
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            request_id = str(payload.get("id") or "")
            method = str(payload.get("method") or "").strip()
            params = (
                payload.get("params") if isinstance(payload.get("params"), dict) else {}
            )
            if not method:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "rpc.error",
                            "id": request_id,
                            "payload": {"message": "method is required"},
                        },
                        ensure_ascii=False,
                    )
                )
                continue
            if method not in BROWSER_WEBSOCKET_METHODS:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "rpc.error",
                            "id": request_id,
                            "payload": {"message": "Method is not allowed"},
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            try:
                result = await bridge.rpc(method, params)
            except TuiGatewayBridgeError:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "rpc.error",
                            "id": request_id,
                            "payload": {"message": "Gateway request failed"},
                        },
                        ensure_ascii=False,
                    )
                )
                continue
            except Exception:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "rpc.error",
                            "id": request_id,
                            "payload": {"message": "Gateway request failed"},
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            await websocket.send_text(
                json.dumps(
                    {
                        "type": "rpc.result",
                        "id": request_id,
                        "payload": force_redact_value(result),
                    },
                    ensure_ascii=False,
                )
            )
    except WebSocketDisconnect:
        pass
    finally:
        bridge.remove_subscriber(websocket)
        registry: TuiGatewayBridgeRegistry = app.state.tui_gateway_bridges
        await registry.maybe_close_if_unused(user.id)


@app.get("/api/status")
async def api_status(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    archived_session_count = await asyncio.to_thread(
        count_archived_sessions,
        mapping_username=user.mapping_username,
    )
    return {
        "status": True,
        "user": _serialize_user(user),
        "workspace_service": user.target.systemd_service,
        "archived_session_count": archived_session_count,
    }


@app.get("/api/archive/status")
async def archive_status(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    archived_session_count = await asyncio.to_thread(
        count_archived_sessions,
        mapping_username=user.mapping_username,
    )
    return {
        "status": True,
        "retention_days": ARCHIVE_RETENTION_DAYS,
        "storage_retention_days": ARCHIVE_STORAGE_RETENTION_DAYS,
        "schedule_hour": ARCHIVE_SCHEDULE_HOUR,
        "archived_session_count": archived_session_count,
    }


@app.get("/", include_in_schema=False)
@app.get("/lite", include_in_schema=False)
async def serve_lite_index() -> FileResponse:
    file_path = LITE_DIR / "index.html"
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Lite frontend not found")
    return FileResponse(file_path)


@app.get("/api/legal/agreement")
async def get_current_legal_agreement() -> dict[str, Any]:
    return current_agreement_metadata()


@app.get("/user-agreement", include_in_schema=False)
@app.get("/user-agreement/{version}", include_in_schema=False)
async def serve_user_agreement(version: str | None = None) -> FileResponse:
    file_path = (
        agreement_document_path(version)
        if version is not None
        else CURRENT_AGREEMENT_PATH
    )
    if file_path is None or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Agreement version not found")
    headers = (
        {"Cache-Control": "public, max-age=31536000, immutable"}
        if version
        else {"Cache-Control": "no-cache"}
    )
    return FileResponse(file_path, headers=headers)


@app.post("/api/auth/signin")
async def signin(payload: SigninRequest, response: Response) -> Any:
    login = payload.email.strip()
    if not login or not payload.password:
        raise HTTPException(
            status_code=400, detail="Email/username and password are required"
        )

    record, password_hash = await asyncio.to_thread(
        get_user_with_password_by_login, login
    )
    if record is None or not record.active:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not await asyncio.to_thread(verify_password, payload.password, password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    agreement = current_agreement_metadata()
    agreement_is_current = await asyncio.to_thread(
        has_agreement_acceptance,
        record.id,
        str(agreement["version"]),
        document_sha256=str(agreement["sha256"]),
    )
    if not agreement_is_current:
        if (
            not payload.agreement_accepted
            or payload.agreement_version.strip() != agreement["version"]
        ):
            return JSONResponse(
                status_code=428,
                content={
                    "error": "agreement_required",
                    "message": "Accept the current research preview terms to sign in.",
                    "agreement": agreement,
                },
            )
        await asyncio.to_thread(
            record_agreement_acceptance,
            user_id=record.id,
            agreement_version=str(agreement["version"]),
            document_sha256=str(agreement["sha256"]),
            source="signin",
        )

    target = mapping_store.resolve_target(
        mapping_username=record.mapping_username,
        email=record.email,
        username=record.username,
    )
    if target is None:
        raise HTTPException(
            status_code=403, detail="No Hermes runtime is mapped to this user"
        )

    user = CurrentUser(
        id=record.id,
        email=record.email,
        username=record.username,
        name=record.name,
        role=record.role,
        mapping_username=record.mapping_username,
        target=target,
        is_temporary=await asyncio.to_thread(is_temporary_user, record.id),
    )
    _set_session_cookie(
        response, _create_session_token(user.id, record.auth_session_version)
    )
    return _serialize_user(user)


@app.post("/api/auth/temporary")
async def create_temporary_auth_session(
    response: Response,
    payload: TemporaryAuthRequest | None = None,
) -> dict[str, Any]:
    agreement = _validate_current_agreement_acceptance(
        payload.agreement_version if payload is not None else "",
        payload.agreement_accepted if payload is not None else False,
    )
    last_integrity_error: sqlite3.IntegrityError | None = None
    for _ in range(3):
        username, email, display_name = await asyncio.to_thread(
            _generate_temporary_identity
        )
        password = secrets.token_urlsafe(32)
        try:
            await asyncio.to_thread(
                privileged_client.provision_user,
                username,
                email=email,
                display_name=display_name,
            )
            _reset_mapping_store_cache()
            target = mapping_store.get_target_by_username(username)
            if target is None:
                raise RuntimeError("Failed to resolve newly created temporary runtime.")

            record = await asyncio.to_thread(
                create_temporary_user,
                username=username,
                email=email,
                password=password,
                mapping_username=username,
                name=display_name,
                agreement_version=str(agreement["version"]),
                agreement_document_sha256=str(agreement["sha256"]),
            )
            user = CurrentUser(
                id=record.id,
                email=record.email,
                username=record.username,
                name=record.name,
                role=record.role,
                mapping_username=record.mapping_username,
                target=target,
                is_temporary=True,
            )
            _set_session_cookie(
                response,
                _create_session_token(user.id, record.auth_session_version),
            )
            return _serialize_user(user)
        except sqlite3.IntegrityError as exc:
            last_integrity_error = exc
            await _rollback_temporary_provision(username)
            continue
        except Exception as exc:
            await _rollback_temporary_provision(username)
            raise HTTPException(
                status_code=503,
                detail=f"Failed to create temporary user: {exc}",
            ) from exc

    raise HTTPException(
        status_code=503,
        detail=(
            "Failed to allocate a temporary user name."
            if last_integrity_error is None
            else "Temporary user name collision. Please try again."
        ),
    )


@app.post("/api/auth/signup/email-verifications")
async def create_signup_email_verification(
    payload: EmailVerificationRequest, request: Request
) -> dict[str, Any]:
    email = _validate_signup_email(payload.email)
    if await asyncio.to_thread(email_exists, email):
        raise HTTPException(status_code=409, detail="Email is already taken.")

    now = _now_seconds()
    client_ip_hash = _hash_client_ip(_client_ip_for_request(request))
    stats = await asyncio.to_thread(
        email_verification_send_stats,
        email=email,
        purpose=EMAIL_VERIFICATION_PURPOSE_SIGNUP,
        client_ip_hash=client_ip_hash,
        now=now,
    )
    if stats.last_email_sent_at is not None:
        resend_after = max(
            EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS
            - (now - stats.last_email_sent_at),
            1,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Verification code was sent recently. Please wait before requesting another code.",
                "resend_after": resend_after,
            },
        )
    if stats.email_hourly_count >= EMAIL_VERIFICATION_EMAIL_HOURLY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many verification emails sent to this address. Please try again later.",
        )
    if stats.ip_hourly_count >= EMAIL_VERIFICATION_IP_HOURLY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many verification email requests. Please try again later.",
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = now + EMAIL_VERIFICATION_TTL_SECONDS
    verification_id = await asyncio.to_thread(
        create_pending_email_verification,
        email=email,
        code_hash=_hash_email_verification_code(email, code),
        purpose=EMAIL_VERIFICATION_PURPOSE_SIGNUP,
        client_ip_hash=client_ip_hash,
        expires_at=expires_at,
        now=now,
    )

    try:
        result = await send_signup_verification_email(
            email=email,
            code=code,
            verification_id=verification_id,
            expires_at=expires_at,
        )
    except MailerConfigurationError as exc:
        await asyncio.to_thread(mark_email_verification_failed, verification_id)
        LOGGER.warning(
            "Signup verification email is not configured: error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Verification email could not be sent. Please try again later.",
        ) from exc
    except MailerDeliveryError as exc:
        await asyncio.to_thread(mark_email_verification_failed, verification_id)
        LOGGER.warning(
            "Signup verification email send failed: status=%s error_type=%s",
            exc.status_code,
            exc.error_type,
        )
        raise HTTPException(
            status_code=503,
            detail="Verification email could not be sent. Please try again later.",
        ) from exc
    except Exception as exc:
        await asyncio.to_thread(mark_email_verification_failed, verification_id)
        LOGGER.warning(
            "Signup verification email send failed: error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Verification email could not be sent. Please try again later.",
        ) from exc

    await asyncio.to_thread(
        record_email_verification_sent,
        verification_id,
        resend_email_id=result.email_id,
        now=_now_seconds(),
    )
    LOGGER.info(
        "Signup verification email sent: status=%s email_id=%s",
        result.status_code,
        result.email_id,
    )
    return {
        "ok": True,
        "verification_id": verification_id,
        "expires_at": expires_at,
        "resend_after": EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    }


@app.post("/api/auth/password-reset/email-verifications")
async def create_password_reset_email_verification(
    payload: EmailVerificationRequest, request: Request
) -> dict[str, Any]:
    email = _validate_signup_email(payload.email)
    now = _now_seconds()
    client_ip_hash = _hash_client_ip(_client_ip_for_request(request))
    stats = await asyncio.to_thread(
        email_verification_send_stats,
        email=email,
        purpose=EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET,
        client_ip_hash=client_ip_hash,
        now=now,
    )
    if stats.last_email_sent_at is not None:
        resend_after = max(
            EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS
            - (now - stats.last_email_sent_at),
            1,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Password reset code was sent recently. Please wait before requesting another code.",
                "resend_after": resend_after,
            },
        )
    if stats.email_hourly_count >= EMAIL_VERIFICATION_EMAIL_HOURLY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many password reset emails sent to this address. Please try again later.",
        )
    if stats.ip_hourly_count >= EMAIL_VERIFICATION_IP_HOURLY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many password reset email requests. Please try again later.",
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = now + EMAIL_VERIFICATION_TTL_SECONDS
    verification_id = await asyncio.to_thread(
        create_pending_email_verification,
        email=email,
        code_hash=_hash_email_verification_code(
            email,
            code,
            purpose=EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET,
        ),
        purpose=EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET,
        client_ip_hash=client_ip_hash,
        expires_at=expires_at,
        now=now,
    )

    record = await asyncio.to_thread(get_user_by_email, email)
    if record is not None and record.active:
        try:
            result = await send_password_reset_email(
                email=email,
                code=code,
                verification_id=verification_id,
                expires_at=expires_at,
            )
        except MailerConfigurationError as exc:
            await asyncio.to_thread(mark_email_verification_failed, verification_id)
            LOGGER.warning(
                "Password reset email is not configured: error_type=%s",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Password reset email could not be sent. Please try again later.",
            ) from exc
        except MailerDeliveryError as exc:
            await asyncio.to_thread(mark_email_verification_failed, verification_id)
            LOGGER.warning(
                "Password reset email send failed: status=%s error_type=%s",
                exc.status_code,
                exc.error_type,
            )
            raise HTTPException(
                status_code=503,
                detail="Password reset email could not be sent. Please try again later.",
            ) from exc
        except Exception as exc:
            await asyncio.to_thread(mark_email_verification_failed, verification_id)
            LOGGER.warning(
                "Password reset email send failed: error_type=%s",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Password reset email could not be sent. Please try again later.",
            ) from exc

        await asyncio.to_thread(
            record_email_verification_sent,
            verification_id,
            resend_email_id=result.email_id,
            now=_now_seconds(),
        )
        LOGGER.info(
            "Password reset email sent: status=%s email_id=%s",
            result.status_code,
            result.email_id,
        )
    else:
        LOGGER.info("Password reset requested for non-active account email.")

    return {
        "ok": True,
        "verification_id": verification_id,
        "expires_at": expires_at,
        "resend_after": EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    }


@app.post("/api/auth/password-reset")
async def reset_password(
    payload: PasswordResetRequest,
    response: Response,
) -> dict[str, Any]:
    email = _validate_signup_email(payload.email)
    verification_id = payload.email_verification_id.strip()
    verification_code = payload.email_verification_code.strip()
    new_password = payload.new_password

    _validate_password_complexity(new_password)
    if not verification_id:
        raise HTTPException(status_code=400, detail="Email verification is required.")
    if not re.fullmatch(r"\d{6}", verification_code):
        raise HTTPException(
            status_code=400, detail="Verification code must be 6 digits."
        )

    try:
        await asyncio.to_thread(
            reset_user_password_with_email_verification,
            email=email,
            new_password=new_password,
            email_verification_id=verification_id,
            email_verification_code_hash=_hash_email_verification_code(
                email,
                verification_code,
                purpose=EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET,
            ),
            purpose=EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET,
        )
    except EmailVerificationError as exc:
        status_code = 429 if exc.reason == "too_many_attempts" else 400
        raise HTTPException(status_code=status_code, detail=exc.message) from exc

    _clear_session_cookie(response)
    return {"ok": True}


@app.post("/api/auth/signup")
async def signup(payload: SignupRequest) -> dict[str, Any]:
    (
        username,
        email,
        password,
        display_name,
        verification_id,
        verification_code,
    ) = await asyncio.to_thread(_validate_signup_payload, payload)
    agreement = _validate_current_agreement_acceptance(
        payload.agreement_version,
        payload.agreement_accepted,
    )
    try:
        job_id = await asyncio.to_thread(
            create_signup_job_with_email_verification,
            username=username,
            email=email,
            password=password,
            display_name=display_name,
            email_verification_id=verification_id,
            email_verification_code_hash=_hash_email_verification_code(
                email, verification_code
            ),
            agreement_version=str(agreement["version"]),
            agreement_document_sha256=str(agreement["sha256"]),
        )
    except EmailVerificationError as exc:
        status_code = 429 if exc.reason == "too_many_attempts" else 400
        raise HTTPException(status_code=status_code, detail=exc.message) from exc
    except sqlite3.IntegrityError as exc:
        detail = str(exc).lower()
        if (
            "signup_jobs.email" in detail
            or "users.email" in detail
            or "email" in detail
        ):
            raise HTTPException(
                status_code=409, detail="Email is already taken."
            ) from exc
        if (
            "signup_jobs.username" in detail
            or "users.username" in detail
            or "username" in detail
        ):
            raise HTTPException(
                status_code=409, detail="Username is already taken."
            ) from exc
        raise HTTPException(
            status_code=409, detail="Username or email is already taken."
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to create signup job: {exc}"
        ) from exc

    return {"ok": True, "job_id": job_id, "status": "pending"}


@app.get("/api/auth/signup/{job_id}")
async def signup_status(job_id: str) -> dict[str, Any]:
    job = await asyncio.to_thread(get_signup_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Signup job not found")
    return {"ok": True, "job": job}


@app.get("/api/auth/me")
async def auth_me(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return _serialize_user(user)


@app.post("/api/auth/password")
async def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    if user.is_temporary:
        raise HTTPException(
            status_code=403,
            detail="Temporary users cannot change passwords.",
        )
    current_password = payload.current_password
    new_password = payload.new_password
    if not current_password or not new_password:
        raise HTTPException(
            status_code=400, detail="Current password and new password are required."
        )
    _validate_password_complexity(new_password)

    record, password_hash = await asyncio.to_thread(
        get_user_with_password_by_id, user.id
    )
    password_matches = bool(
        record is not None
        and record.id == user.id
        and await asyncio.to_thread(
            verify_password,
            current_password,
            password_hash,
        )
    )
    if not password_matches:
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    updated_record = await asyncio.to_thread(
        update_user_password, user.id, new_password
    )
    if updated_record is None:
        raise HTTPException(status_code=404, detail="User not found.")

    _set_session_cookie(
        response,
        _create_session_token(user.id, updated_record.auth_session_version),
    )
    return {"ok": True, "user": _serialize_user(user)}


@app.post("/api/auth/signout")
async def signout(response: Response) -> dict[str, Any]:
    _clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/models")
async def get_models(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    try:
        config = await asyncio.to_thread(mapping_store.load_config, resolve_env=True)
        model_options = normalize_model_options(config)
        active_id = await asyncio.to_thread(
            _get_active_model_id_for_user,
            user.target,
            model_options,
            config=config,
        )
    except ModelOptionsError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid model whitelist configuration: {exc}",
        ) from exc
    except PrivilegedClientError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read active model: {exc}",
        ) from exc

    return {
        "data": [
            option.to_public(
                is_primary=option.id == model_options.primary_id,
                is_active=option.id == active_id,
            )
            for option in model_options.options
        ],
        "primary_id": model_options.primary_id,
        "active_id": active_id,
    }


@app.put("/api/models/active")
async def update_active_model(
    payload: ActiveModelUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    requested_id = str(payload.id or "").strip()
    if not requested_id:
        raise HTTPException(status_code=400, detail="Model id is required")

    try:
        config = await asyncio.to_thread(mapping_store.load_config, resolve_env=True)
        model_options = normalize_model_options(config)
    except ModelOptionsError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid model whitelist configuration: {exc}",
        ) from exc

    selected = model_options.get(requested_id)
    if selected is None:
        raise HTTPException(status_code=400, detail="Model is not allowed")

    try:
        active_id = await asyncio.to_thread(
            _get_active_model_id_for_user,
            user.target,
            model_options,
            config=config,
        )
        if requested_id == active_id:
            return {
                "ok": True,
                "active_id": active_id,
                "model": selected.to_public(
                    is_primary=selected.id == model_options.primary_id,
                    is_active=True,
                ),
            }
    except (ModelOptionsError, PrivilegedClientError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    registry: TuiGatewayBridgeRegistry = app.state.tui_gateway_bridges
    existing_bridge = await registry.get_existing(user.id)
    bridge_reconfigure_conflict = bool(
        existing_bridge is not None
        and (
            existing_bridge.has_reconfigure_conflict()
            if hasattr(existing_bridge, "has_reconfigure_conflict")
            else existing_bridge.has_pending_requests()
        )
    )
    if await asyncio.to_thread(_active_live_state_conflict, user.id) or (
        bridge_reconfigure_conflict
    ):
        raise HTTPException(
            status_code=409,
            detail="Cannot switch models while a response or approval is active",
        )

    closed = await registry.close_for_reconfigure(user.id)
    if not closed:
        raise HTTPException(
            status_code=409,
            detail="Cannot switch models while a response or approval is active",
        )

    try:
        if os.geteuid() == 0 and not privileged_client.force_helper:
            await asyncio.to_thread(
                patch_user_active_model,
                user.target,
                selected,
                proxy_base_url=get_model_proxy_base_url(config),
            )
        else:
            await asyncio.to_thread(
                privileged_client.patch_active_model,
                user.target.username,
                selected.id,
            )
    except (ModelOptionsError, PrivilegedClientError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "active_id": selected.id,
        "model": selected.to_public(
            is_primary=selected.id == model_options.primary_id,
            is_active=True,
        ),
    }


def _load_normalized_sessions_sync(
    target: HermesTarget,
    *,
    live_states: dict[str, dict[str, Any]],
    display_metas: dict[str, dict[str, Any]],
    fetch_limit: int,
) -> list[dict[str, Any]]:
    with _open_session_db(target) as db:
        sessions = db.list_sessions_rich(
            source="tui",
            limit=fetch_limit,
            offset=0,
            order_by_last_active=True,
        )
        normalized_by_id: dict[str, dict[str, Any]] = {}
        seen_session_ids: set[str] = set()
        for item in sessions:
            if not _is_interface_managed_source(item.get("source")):
                continue
            logical_session_id = _logical_session_id_from_row(item)
            logical_session = item
            if str(item.get("_lineage_root_id") or "").strip():
                root_session = db.get_session(logical_session_id)
                if not root_session or not _is_interface_managed_source(
                    root_session.get("source")
                ):
                    continue
                logical_session = root_session
            normalized_by_id[logical_session_id] = _normalize_logical_session_row(
                item,
                logical_session_id=logical_session_id,
                logical_session=logical_session,
                display_meta=display_metas.get(logical_session_id),
                live_state=live_states.get(logical_session_id),
                resume_session_id=str(item.get("id") or logical_session_id),
            )
            seen_session_ids.add(logical_session_id)

        for logical_session_id, display_meta in display_metas.items():
            if logical_session_id in seen_session_ids:
                continue
            live_state = live_states.get(logical_session_id)
            normalized_by_id[logical_session_id] = _normalize_logical_session_row(
                {
                    "id": logical_session_id,
                    "source": "tui",
                    "model": "",
                    "title": "",
                    "preview": "",
                    "started_at": int(display_meta.get("created_at") or 0),
                    "last_active": int(
                        live_state.get("updated_at")
                        if isinstance(live_state, dict)
                        else display_meta.get("updated_at") or 0
                    ),
                    "message_count": int(display_meta.get("message_count") or 0),
                    "tool_call_count": 0,
                },
                logical_session_id=logical_session_id,
                logical_session={
                    "id": logical_session_id,
                    "source": "tui",
                    "title": "",
                },
                display_meta=display_meta,
                live_state=live_state,
                resume_session_id=logical_session_id,
            )
    return sorted(
        normalized_by_id.values(),
        key=lambda item: (item["last_active"], item["started_at"]),
        reverse=True,
    )


@app.get("/api/sessions")
async def get_sessions(
    limit: int = 50, offset: int = 0, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    page_limit = max(1, min(int(limit or 50), 200))
    page_offset = max(0, int(offset or 0))
    fetch_limit = page_offset + page_limit + 1
    live_states, display_metas = await asyncio.gather(
        asyncio.to_thread(list_live_session_states, user.id),
        asyncio.to_thread(
            list_display_session_metas,
            user.id,
            include_messages=False,
        ),
    )
    normalized = await asyncio.to_thread(
        _load_normalized_sessions_sync,
        user.target,
        live_states=live_states,
        display_metas=display_metas,
        fetch_limit=fetch_limit,
    )
    page = normalized[page_offset : page_offset + page_limit]
    return {
        "sessions": page,
        "limit": page_limit,
        "offset": page_offset,
        "next_offset": page_offset + len(page),
        "has_more": len(normalized) > page_offset + page_limit,
    }


@app.post("/api/sessions/{session_id}/shares")
async def create_session_share(
    session_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_chat_share_request(request)
    await _validate_chat_share_json_body(request, ChatShareCreateRequest)
    if user.is_temporary:
        raise _chat_share_error(
            403,
            "formal_account_required",
            "Chat sharing is only available to signed-in accounts.",
        )

    logical_session_id, title, messages = await asyncio.to_thread(
        _load_chat_share_snapshot_sync,
        user,
        session_id,
    )
    try:
        created = await asyncio.to_thread(
            create_chat_share,
            owner_user_id=user.id,
            source_session_id=logical_session_id,
            title=title,
            messages=messages,
        )
    except ChatShareLimitError as exc:
        raise _chat_share_error(
            429,
            exc.code,
            "Too many chat share links have been created. Try again later.",
            retry_after=exc.retry_after,
        ) from exc
    except ChatShareValidationError as exc:
        detail = str(exc).lower()
        if "too large" in detail or "too many" in detail:
            raise _chat_share_error(
                413,
                "share_too_large",
                "This chat is too large to share.",
            ) from exc
        raise _chat_share_error(
            409,
            "session_not_shareable",
            "This chat cannot be shared in its current state.",
        ) from exc
    except Exception as exc:
        LOGGER.exception("Failed to create a chat share")
        raise _chat_share_error(
            503,
            "sharing_unavailable",
            "Chat sharing is temporarily unavailable.",
        ) from exc

    return {
        "token": created.token,
        "expires_at": created.expires_at,
        "max_recipients": CHAT_SHARE_MAX_RECIPIENTS,
    }


@app.post("/api/chat-shares/import", response_model=None)
async def import_chat_share(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any] | JSONResponse:
    _require_chat_share_request(request)
    payload = await _validate_chat_share_json_body(request, ChatShareImportRequest)
    assert isinstance(payload, ChatShareImportRequest)
    token = _normalize_chat_share_token_or_404(payload.token)
    recipient_user_id = _chat_share_recipient_id(user)
    try:
        claim = await asyncio.to_thread(
            claim_chat_share_import,
            token=token,
            recipient_user_id=recipient_user_id,
        )
    except Exception as exc:
        LOGGER.exception("Failed to claim a chat share import")
        raise _chat_share_error(
            503,
            "sharing_unavailable",
            "Chat sharing is temporarily unavailable.",
        ) from exc

    if claim.status == CLAIM_STATUS_UNAVAILABLE:
        raise _chat_share_error(
            404,
            "share_unavailable",
            "Share link unavailable.",
        )
    if claim.status in {CLAIM_STATUS_RECIPIENT_LIMIT, CLAIM_STATUS_TARGET_DELETED}:
        raise _chat_share_error(
            410,
            "share_import_unavailable",
            "This share link cannot be imported into this account.",
        )
    if claim.status == CLAIM_STATUS_RATE_LIMITED:
        raise _chat_share_error(
            429,
            "share_import_rate_limited",
            "Too many shared chats have been imported. Try again later.",
            retry_after=claim.retry_after,
        )
    if claim.status == CLAIM_STATUS_IN_PROGRESS:
        return JSONResponse(
            status_code=202,
            headers={"Retry-After": "2"},
            content={"pending": True},
        )

    shared_messages = [dict(message) for message in claim.messages]
    if claim.status == CLAIM_STATUS_COMPLETED:
        try:
            imported = await asyncio.to_thread(
                _load_imported_chat_response_sync,
                user,
                claim.imported_session_id,
                shared_messages=shared_messages,
            )
        except Exception as exc:
            LOGGER.exception("Failed to load a completed chat share import")
            raise _chat_share_error(
                503,
                "sharing_unavailable",
                "Chat sharing is temporarily unavailable.",
            ) from exc
        if imported is None:
            try:
                await asyncio.to_thread(
                    mark_chat_share_import_target_deleted_by_session,
                    recipient_user_id=recipient_user_id,
                    imported_session_id=claim.imported_session_id,
                )
            except Exception as exc:
                raise _chat_share_error(
                    503,
                    "sharing_unavailable",
                    "Chat sharing is temporarily unavailable.",
                ) from exc
            raise _chat_share_error(
                410,
                "share_import_target_deleted",
                "The previously imported chat has been deleted.",
            )
        return {"created": False, **imported}

    if claim.status != CLAIM_STATUS_CLAIMED:
        raise _chat_share_error(
            503,
            "sharing_unavailable",
            "Chat sharing is temporarily unavailable.",
        )

    try:
        claim_is_valid = await asyncio.to_thread(
            chat_share_import_claim_is_valid,
            share_id=claim.share_id,
            recipient_user_id=recipient_user_id,
            claim_id=claim.claim_id,
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                fail_chat_share_import,
                share_id=claim.share_id,
                recipient_user_id=recipient_user_id,
                claim_id=claim.claim_id,
                error_code="claim_check_failed",
            )
        raise _chat_share_error(
            503,
            "sharing_unavailable",
            "Chat sharing is temporarily unavailable.",
        ) from exc
    if not claim_is_valid:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                fail_chat_share_import,
                share_id=claim.share_id,
                recipient_user_id=recipient_user_id,
                claim_id=claim.claim_id,
                error_code="claim_invalid",
            )
        return JSONResponse(
            status_code=202,
            headers={"Retry-After": "1"},
            content={"pending": True},
        )

    try:
        import_result = await asyncio.to_thread(
            _import_shared_session_sync,
            user.target,
            session_id=claim.imported_session_id,
            title=claim.title,
            messages=shared_messages,
        )
        imported = await asyncio.to_thread(
            _load_imported_chat_response_sync,
            user,
            claim.imported_session_id,
            shared_messages=shared_messages,
        )
        if imported is None:
            raise RuntimeError("Imported chat session is unavailable")
        completed = await asyncio.to_thread(
            complete_chat_share_import,
            share_id=claim.share_id,
            recipient_user_id=recipient_user_id,
            claim_id=claim.claim_id,
            imported_title=str(import_result.get("title") or ""),
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                fail_chat_share_import,
                share_id=claim.share_id,
                recipient_user_id=recipient_user_id,
                claim_id=claim.claim_id,
                error_code="import_failed",
            )
        LOGGER.exception("Failed to import a shared chat")
        raise _chat_share_error(
            503,
            "sharing_unavailable",
            "Chat sharing is temporarily unavailable.",
        ) from exc

    if not completed:
        return JSONResponse(
            status_code=202,
            headers={"Retry-After": "1"},
            content={"pending": True},
        )
    return {"created": True, **imported}


def _get_live_poll_snapshot_sync(
    user_id: str,
    session_id: str,
    *,
    after_run_id: str = "",
    after_event_seq: int = -1,
) -> dict[str, Any] | None:
    snapshot = get_live_poll_snapshot(
        user_id,
        session_id,
        after_run_id=after_run_id,
        after_event_seq=after_event_seq,
    )
    if snapshot is None:
        return None
    messages = snapshot.get("messages")
    if isinstance(messages, list):
        snapshot["messages"] = [_normalize_display_message(item) for item in messages]
    return snapshot


@app.get("/api/sessions/{session_id}/live")
async def get_session_live_snapshot(
    session_id: str,
    after_run_id: str = "",
    after_event_seq: int = -1,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    snapshot = await asyncio.to_thread(
        _get_live_poll_snapshot_sync,
        user.id,
        str(session_id or "").strip(),
        after_run_id=str(after_run_id or "").strip(),
        after_event_seq=int(after_event_seq),
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return snapshot


@app.get("/api/turns/{request_id}", response_model=None)
async def get_submitted_turn(
    request_id: str, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any] | JSONResponse:
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id or len(normalized_request_id) > 128:
        raise HTTPException(status_code=404, detail="Submitted turn not found")
    live_session_id, receipt = await asyncio.gather(
        asyncio.to_thread(
            find_live_session_id_by_run_id,
            user.id,
            normalized_request_id,
        ),
        asyncio.to_thread(
            get_turn_submission_receipt,
            user.id,
            normalized_request_id,
        ),
    )
    session_id = str(live_session_id or (receipt or {}).get("session_id") or "").strip()
    if not session_id:
        receipt_status = str((receipt or {}).get("status") or "")
        if receipt_status == "pending":
            return JSONResponse(
                status_code=202,
                content={
                    "ok": True,
                    "pending": True,
                    "request_id": normalized_request_id,
                    "expires_at": int((receipt or {}).get("expires_at") or 0),
                },
            )
        if receipt_status == "failed":
            raise HTTPException(
                status_code=409,
                detail=str(
                    (receipt or {}).get("last_error") or "Turn submission failed"
                ),
            )
        raise HTTPException(status_code=404, detail="Submitted turn not found")
    response = await _build_submitted_turn_response(
        user,
        session_id,
        expected_run_id=normalized_request_id,
    )
    if response is None:
        if str((receipt or {}).get("status") or "") == "submitted":
            raise HTTPException(
                status_code=409,
                detail="Submitted turn is no longer the current run",
            )
        raise HTTPException(status_code=404, detail="Submitted turn not found")
    return response


async def _build_submitted_turn_response(
    user: CurrentUser,
    session_id: str,
    *,
    expected_run_id: str = "",
) -> dict[str, Any] | None:
    snapshot, display_meta = await asyncio.gather(
        asyncio.to_thread(
            _get_live_poll_snapshot_sync,
            user.id,
            session_id,
        ),
        asyncio.to_thread(get_display_session_meta, user.id, session_id),
    )
    if snapshot is None:
        return None
    live_state = snapshot.get("live")
    if expected_run_id and str((live_state or {}).get("run_id") or "") != str(
        expected_run_id
    ):
        return None
    messages = snapshot.get("messages")
    session = _normalize_logical_session_row(
        {
            "id": session_id,
            "source": "tui",
            "model": "",
            "title": "",
            "preview": "",
            "started_at": int((display_meta or {}).get("created_at") or 0),
            "last_active": int((display_meta or {}).get("updated_at") or 0),
            "message_count": len(messages) if isinstance(messages, list) else 0,
            "tool_call_count": 0,
        },
        logical_session_id=session_id,
        logical_session={"id": session_id, "source": "tui", "title": ""},
        display_meta=display_meta,
        live_state=live_state if isinstance(live_state, dict) else None,
        resume_session_id=str((live_state or {}).get("tip_session_id") or session_id),
    )
    return {
        "ok": True,
        "created": False,
        "session": session,
        **snapshot,
    }


def _fork_target_session_id(user_id: str, request_id: str) -> str:
    digest = hashlib.sha256(
        f"potato-session-fork\0{user_id}\0{request_id}".encode("utf-8")
    ).hexdigest()
    return f"fork_{digest[:32]}"


def _fork_display_prefix(
    messages: list[dict[str, Any]],
    fork_cursor: str,
    target_session_id: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    matching_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, dict)
        and str(message.get("id") or "") == fork_cursor
    ]
    if len(matching_indexes) != 1:
        raise ValueError("Fork cursor does not uniquely identify a message")
    cursor_index = matching_indexes[0]
    cursor_message = messages[cursor_index]
    if (
        str(cursor_message.get("role") or "") != "assistant"
        or not bool(cursor_message.get("done", True))
    ):
        raise ValueError("Only completed assistant messages can be forked")

    normalized_prefix: list[dict[str, Any]] = []
    for index, message in enumerate(messages[: cursor_index + 1]):
        normalized = _normalize_display_message(message)
        normalized.pop("fork_cursor", None)
        source_message_id = str(message.get("id") or index)
        normalized["id"] = "fork-" + hashlib.sha256(
            f"{target_session_id}\0{index}\0{source_message_id}".encode("utf-8")
        ).hexdigest()[:24]
        normalized_prefix.append(normalized)

    display_turns: list[dict[str, Any]] = []
    pending_user: dict[str, Any] | None = None
    pending_user_index = -1
    for index, message in enumerate(normalized_prefix):
        role = str(message.get("role") or "")
        if role == "user":
            pending_user = message
            pending_user_index = index
            continue
        if role != "assistant" or pending_user is None:
            continue
        user_content = build_hermes_user_content(
            str(pending_user.get("content") or ""),
            pending_user.get("files")
            if isinstance(pending_user.get("files"), list)
            else [],
        )
        display_turns.append(
            {
                "user": user_content,
                "assistant": str(message.get("content") or ""),
                "user_message_index": pending_user_index,
                "assistant_message_index": index,
                "user_timestamp": int(pending_user.get("timestamp") or 0),
                "assistant_timestamp": int(message.get("timestamp") or 0),
            }
        )
        pending_user = None
        pending_user_index = -1

    if not display_turns or int(display_turns[-1]["assistant_message_index"]) != len(
        normalized_prefix
    ) - 1:
        raise ValueError("Fork cursor is not part of a complete conversation turn")

    visible_history: list[dict[str, Any]] = []
    for turn in display_turns:
        visible_history.extend(
            [
                {
                    "role": "user",
                    "content": turn["user"],
                    "timestamp": turn["user_timestamp"],
                },
                {
                    "role": "assistant",
                    "content": turn["assistant"],
                    "timestamp": turn["assistant_timestamp"],
                },
            ]
        )
    return normalized_prefix, visible_history, display_turns


def _fork_session_sync(
    *,
    user_id: str,
    target: HermesTarget,
    requested_session_id: str,
    fork_cursor: str,
    request_id: str,
) -> dict[str, Any]:
    target_session_id = _fork_target_session_id(user_id, request_id)
    with _open_session_db(target) as db:
        logical_session_id, logical_session, tip_session_id, projected_session = (
            _resolve_logical_session_context(db, requested_session_id)
        )
        if not logical_session or not _is_interface_managed_source(
            logical_session.get("source")
        ):
            raise HTTPException(status_code=404, detail="Session not found")
        raw_messages = db.get_messages(tip_session_id)

        display_meta = get_display_session_meta(user_id, logical_session_id)
        display_messages = (
            display_meta.get("messages")
            if isinstance(display_meta, dict)
            and isinstance(display_meta.get("messages"), list)
            else _build_fallback_display_messages(raw_messages)
        )
        cloned_display, visible_history, display_turns = _fork_display_prefix(
            display_messages,
            fork_cursor,
            target_session_id,
        )
        source_title = _session_export_title(
            logical_session,
            projected_session,
            display_meta,
            logical_session_id,
        )
        raw_boundary = get_message_fork_boundary(
            user_id,
            logical_session_id,
            fork_cursor,
        )
        result = db.fork_session(
            target_session_id=target_session_id,
            source_session_id=logical_session_id,
            request_id=request_id,
            fork_cursor=fork_cursor,
            source_title=source_title,
            visible_history=visible_history,
            display_turns=display_turns,
            raw_boundary=raw_boundary,
        )

    inserted_display = create_display_messages_if_absent(
        user_id,
        target_session_id,
        cloned_display,
    )
    if not inserted_display and bool(result.get("created")):
        existing_display = get_display_messages(user_id, target_session_id)
        if existing_display != cloned_display:
            raise ValueError("Fork display target conflict")

    boundary_map = result.get("boundary_map")
    if not isinstance(boundary_map, list):
        boundary_map = []
    target_boundary_head = int(result.get("target_boundary_head") or 0)
    if target_boundary_head and not boundary_map:
        boundary_map = [
            {
                "display_index": len(cloned_display) - 1,
                "active_message_head": target_boundary_head,
            }
        ]
    for boundary in boundary_map:
        if not isinstance(boundary, dict):
            continue
        try:
            display_index = int(boundary.get("display_index"))
            active_message_head = int(boundary.get("active_message_head") or 0)
        except (TypeError, ValueError):
            continue
        if not 0 <= display_index < len(cloned_display) or active_message_head <= 0:
            continue
        display_message = cloned_display[display_index]
        if str(display_message.get("role") or "") != "assistant":
            continue
        save_message_fork_boundary(
            user_id,
            target_session_id,
            str(display_message.get("id") or ""),
            physical_session_id=target_session_id,
            active_message_head=active_message_head,
        )

    with _open_session_db(target) as db:
        (
            logical_target_id,
            logical_target,
            target_tip_id,
            projected_target,
            target_raw_messages,
        ) = _resolve_logical_session_context_snapshot(
            db,
            target_session_id,
            include_messages=True,
        )
    current_display = get_display_messages(user_id, target_session_id)
    if current_display is None:
        current_display = _build_fallback_display_messages(target_raw_messages)
    current_display_meta = get_display_session_meta(user_id, target_session_id)
    live_state = get_live_session_state(user_id, target_session_id)
    return {
        "created": bool(result.get("created")),
        "context_mode": str(result.get("context_mode") or "visible"),
        "session": _normalize_logical_session_row(
            projected_target or logical_target or {"id": logical_target_id},
            logical_session_id=logical_target_id,
            logical_session=logical_target,
            display_meta=current_display_meta,
            live_state=live_state,
            resume_session_id=target_tip_id or logical_target_id,
        ),
        "messages": [
            _normalize_display_message(message) for message in current_display
        ],
        "live": live_state,
    }


@app.post("/api/sessions/{session_id}/forks")
async def fork_session(
    session_id: str,
    payload: SessionForkRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    fork_cursor = str(payload.fork_cursor or "").strip()
    request_id = str(payload.request_id or "").strip()
    if not fork_cursor or len(fork_cursor) > 256:
        raise HTTPException(status_code=400, detail="Invalid fork cursor")
    if not request_id or len(request_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid fork request id")
    try:
        return await asyncio.to_thread(
            _fork_session_sync,
            user_id=user.id,
            target=user.target,
            requested_session_id=str(session_id or "").strip(),
            fork_cursor=fork_cursor,
            request_id=request_id,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "conflict" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


async def _heartbeat_pending_turn_submission(
    user_id: str,
    request_id: str,
) -> None:
    while True:
        await asyncio.sleep(TURN_SUBMISSION_RECEIPT_HEARTBEAT_SECONDS)
        try:
            renewed = await asyncio.to_thread(
                heartbeat_turn_submission_receipt,
                user_id,
                request_id,
            )
        except Exception:
            LOGGER.exception(
                "Failed to heartbeat turn submission receipt %s for user %s",
                request_id,
                user_id,
            )
            continue
        if not renewed:
            return


@app.get("/api/sessions/{session_id}")
async def get_session_detail(
    session_id: str, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    display_meta_fallback, session_context = await asyncio.gather(
        asyncio.to_thread(get_display_session_meta, user.id, session_id),
        asyncio.to_thread(
            _load_session_context_sync,
            user.target,
            session_id,
            include_messages=True,
        ),
    )
    (
        logical_session_id,
        logical_session,
        tip_session_id,
        projected_session,
        raw_messages,
    ) = session_context
    if not logical_session or not _is_interface_managed_source(
        logical_session.get("source")
    ):
        if display_meta_fallback is None:
            raise HTTPException(status_code=404, detail="Session not found")
        logical_session_id = session_id
        logical_session = {"id": session_id, "source": "tui", "title": ""}
        tip_session_id = session_id
        projected_session = {
            "id": session_id,
            "source": "tui",
            "model": "",
            "title": "",
            "preview": "",
            "started_at": int(display_meta_fallback.get("created_at") or 0),
            "last_active": int(display_meta_fallback.get("updated_at") or 0),
            "message_count": len(
                display_meta_fallback.get("messages")
                if isinstance(display_meta_fallback.get("messages"), list)
                else []
            ),
            "tool_call_count": 0,
        }
        raw_messages = []

    display_messages, live_state, display_meta = await asyncio.gather(
        asyncio.to_thread(get_display_messages, user.id, logical_session_id),
        asyncio.to_thread(get_live_session_state, user.id, logical_session_id),
        asyncio.to_thread(get_display_session_meta, user.id, logical_session_id),
    )
    if display_messages is None:
        display_messages = _build_fallback_display_messages(raw_messages)
    if (
        isinstance(live_state, dict)
        and str(live_state.get("status") or "").strip() in ACTIVE_LIVE_STATUSES
    ):
        session_run_manager: SessionRunManager | None = getattr(
            app.state,
            "session_run_manager",
            None,
        )
        if session_run_manager is not None:
            live_state = await session_run_manager.reconcile_active_session_tip(
                user.id,
                logical_session_id,
            )
            tip_session_id = str(
                (live_state or {}).get("tip_session_id") or tip_session_id
            ).strip()

    return {
        "session": _normalize_logical_session_row(
            projected_session,
            logical_session_id=logical_session_id,
            logical_session=logical_session,
            display_meta=display_meta,
            live_state=live_state,
            resume_session_id=tip_session_id or logical_session_id,
        ),
        "messages": [_normalize_display_message(item) for item in display_messages],
        "live": live_state,
    }


@app.get("/api/sessions/{session_id}/export.md")
async def export_session_markdown(
    session_id: str, user: CurrentUser = Depends(get_current_user)
) -> FastAPIResponse:
    display_meta_fallback, session_context = await asyncio.gather(
        asyncio.to_thread(get_display_session_meta, user.id, session_id),
        asyncio.to_thread(
            _load_session_context_sync,
            user.target,
            session_id,
            include_messages=True,
        ),
    )
    (
        logical_session_id,
        logical_session,
        _,
        projected_session,
        raw_messages,
    ) = session_context
    if not logical_session or not _is_interface_managed_source(
        logical_session.get("source")
    ):
        if display_meta_fallback is None:
            raise HTTPException(status_code=404, detail="Session not found")
        logical_session_id = session_id
        logical_session = {"id": session_id, "source": "tui", "title": ""}
        raw_messages = []

    display_messages, display_meta = await asyncio.gather(
        asyncio.to_thread(get_display_messages, user.id, logical_session_id),
        asyncio.to_thread(get_display_session_meta, user.id, logical_session_id),
    )
    if display_messages is None:
        display_messages = _build_fallback_display_messages(raw_messages)
    export_title = _session_export_title(
        logical_session,
        projected_session,
        display_meta or display_meta_fallback,
        logical_session_id,
    )
    markdown = _build_session_markdown_export(export_title, display_messages)
    filename = _export_markdown_filename(export_title, logical_session_id)

    return FastAPIResponse(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": _attachment_content_disposition(filename),
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.put("/api/sessions/{session_id}/display")
async def sync_session_display(
    session_id: str,
    payload: SessionDisplaySyncRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    (
        logical_session_id,
        logical_session,
        _,
        _,
        raw_messages,
    ) = await asyncio.to_thread(
        _load_session_context_sync,
        user.target,
        session_id,
        include_messages=True,
    )
    if not logical_session or not _is_interface_managed_source(
        logical_session.get("source")
    ):
        raise HTTPException(status_code=404, detail="Session not found")

    merged_messages = _merge_display_transcripts(
        payload.messages,
        _build_fallback_display_messages(raw_messages),
    )
    draft_title = str(payload.draft_title or "").strip() or None
    await asyncio.to_thread(
        save_display_messages,
        user.id,
        logical_session_id,
        merged_messages,
        draft_title=draft_title,
    )
    return {
        "ok": True,
        "messages": [_normalize_display_message(item) for item in merged_messages],
    }


def _update_session_title_sync(
    target: HermesTarget,
    session_id: str,
    sanitized_title: str,
) -> tuple[
    str,
    dict[str, Any] | None,
    str,
    dict[str, Any] | None,
    list[dict[str, Any]],
]:
    with _open_session_db(target) as db:
        logical_session_id, logical_session, tip_session_id, _ = (
            _resolve_logical_session_context(db, session_id)
        )
        if not logical_session or not _is_interface_managed_source(
            logical_session.get("source")
        ):
            raise _session_title_error(
                status_code=404,
                code="session_not_found",
                message="Session not found.",
            )

        try:
            updated = db.set_session_title(logical_session_id, sanitized_title)
        except ValueError as exc:
            detail = str(exc)
            if "already in use" in detail:
                raise _session_title_error(
                    status_code=409,
                    code="title_duplicate",
                    message="A chat with this title already exists.",
                ) from exc
            if "too long" in detail:
                raise _session_title_error(
                    status_code=400,
                    code="title_too_long",
                    message="Title must be 100 characters or fewer.",
                ) from exc
            raise _session_title_error(
                status_code=400,
                code="title_invalid",
                message="Invalid title.",
            ) from exc

        if not updated:
            raise _session_title_error(
                status_code=404,
                code="session_not_found",
                message="Session not found.",
            )

        refreshed_logical_session = db.get_session(logical_session_id)
        refreshed_tip_session_id = _get_logical_session_tip_id(db, logical_session_id)
        projected_session = (
            _get_projected_logical_session_row(db, logical_session_id)
            or refreshed_logical_session
        )
        raw_messages = db.get_messages(refreshed_tip_session_id)

    return (
        logical_session_id,
        refreshed_logical_session,
        refreshed_tip_session_id,
        projected_session,
        raw_messages,
    )


@app.put("/api/sessions/{session_id}/title")
async def update_session_title(
    session_id: str,
    payload: SessionTitleUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    sanitized_title = _sanitize_session_title_or_raise(payload.title)
    (
        logical_session_id,
        refreshed_logical_session,
        refreshed_tip_session_id,
        projected_session,
        raw_messages,
    ) = await asyncio.to_thread(
        _update_session_title_sync,
        user.target,
        session_id,
        sanitized_title,
    )

    display_messages, live_state, display_meta = await asyncio.gather(
        asyncio.to_thread(get_display_messages, user.id, logical_session_id),
        asyncio.to_thread(get_live_session_state, user.id, logical_session_id),
        asyncio.to_thread(get_display_session_meta, user.id, logical_session_id),
    )
    if display_messages is None:
        display_messages = _build_fallback_display_messages(raw_messages)

    return {
        "session": _normalize_logical_session_row(
            projected_session or {"id": logical_session_id, "title": sanitized_title},
            logical_session_id=logical_session_id,
            logical_session=refreshed_logical_session,
            display_meta=display_meta,
            live_state=live_state,
            resume_session_id=refreshed_tip_session_id or logical_session_id,
        ),
        "messages": [_normalize_display_message(item) for item in display_messages],
        "live": live_state,
    }


@app.post("/api/sessions/{session_id}/turns")
async def submit_session_turn(
    session_id: str,
    payload: SessionTurnSubmitRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    prompt = str(payload.prompt or "").strip()
    mode = str(payload.mode or "chat").strip().lower()
    if mode not in {"chat", "plan"}:
        raise HTTPException(status_code=400, detail="Invalid turn mode")
    attachments = (
        [item for item in payload.attachments if isinstance(item, dict)]
        if isinstance(payload.attachments, list)
        else []
    )
    if not prompt and not attachments:
        raise HTTPException(
            status_code=400, detail="Prompt or attachments are required"
        )
    if _attachment_total_size_bytes(attachments) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=_attachment_total_too_large_detail(),
        )

    bridge: TuiGatewayBridge | None = None
    logical_session_id = ""
    tip_session_id = ""
    projected_session: dict[str, Any] | None = None
    logical_session: dict[str, Any] | None = None
    fallback_messages: list[dict[str, Any]] = []
    display_messages: list[dict[str, Any]] | None = None
    live_session_id = ""
    created_new_session = False
    session_run_manager: SessionRunManager = app.state.session_run_manager
    draft_title = str(payload.draft_title or "").strip()
    request_id = str(payload.request_id or "").strip()
    if len(request_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid request id")
    request_id = request_id or uuid.uuid4().hex
    receipt = await asyncio.to_thread(
        create_turn_submission_receipt,
        user.id,
        request_id,
        requested_session_id=str(session_id or "").strip(),
    )
    if not bool(receipt.get("created")):
        existing_session_id = str(receipt.get("session_id") or "").strip()
        if str(receipt.get("status") or "") == "submitted" and existing_session_id:
            existing_response = await _build_submitted_turn_response(
                user,
                existing_session_id,
                expected_run_id=request_id,
            )
            if existing_response is not None:
                return existing_response
        detail = str(
            receipt.get("last_error") or "Turn submission is already in progress"
        )
        raise HTTPException(status_code=409, detail=detail)

    receipt_heartbeat_task = asyncio.create_task(
        _heartbeat_pending_turn_submission(user.id, request_id)
    )
    receipt_finished = False
    try:
        bridge = await _get_tui_bridge_for_user(user)
        if session_id == "draft":
            created = await bridge.rpc("session.create", {"cols": 100})
            live_session_id = str(created.get("session_id") or "").strip()
            if not live_session_id:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to create TUI gateway session",
                )
            title_info = await bridge.rpc(
                "session.title", {"session_id": live_session_id}
            )
            logical_session_id = str(
                title_info.get("session_key") or live_session_id
            ).strip()
            logical_session = {
                "id": logical_session_id,
                "source": "tui",
                "title": "",
                "preview": "",
                "started_at": _now_seconds(),
                "last_active": _now_seconds(),
                "message_count": 0,
                "tool_call_count": 0,
            }
            tip_session_id = logical_session_id
            projected_session = logical_session
            fallback_messages = []
            created_new_session = True
        else:
            (
                logical_session_id,
                logical_session,
                tip_session_id,
                projected_session,
                raw_messages,
            ) = await asyncio.to_thread(
                _load_session_context_sync,
                user.target,
                session_id,
                include_messages=True,
            )
            if not logical_session or not _is_interface_managed_source(
                logical_session.get("source")
            ):
                raise HTTPException(status_code=404, detail="Session not found")
            resumed = await bridge.rpc(
                "session.resume",
                {
                    "cols": 100,
                    "session_id": tip_session_id or logical_session_id,
                },
            )
            live_session_id = str(resumed.get("session_id") or "").strip()
            if not live_session_id:
                raise HTTPException(
                    status_code=500,
                    detail="TUI gateway did not return a live session id on resume",
                )
            fallback_messages = _build_fallback_display_messages(raw_messages)

        if not logical_session_id:
            raise HTTPException(
                status_code=500, detail="Failed to resolve logical session id"
            )

        display_messages = await asyncio.to_thread(
            get_display_messages, user.id, logical_session_id
        )
        base_messages = (
            display_messages if display_messages is not None else fallback_messages
        )

        await session_run_manager.ensure_session_bound(
            bridge=bridge,
            user_id=user.id,
            session_id=logical_session_id,
            live_session_id=live_session_id,
        )
        receipt_is_pending = await asyncio.to_thread(
            heartbeat_turn_submission_receipt,
            user.id,
            request_id,
        )
        if not receipt_is_pending:
            raise TuiGatewayBridgeError("Turn submission is no longer pending")
        submit_result = await session_run_manager.submit_turn(
            bridge=bridge,
            user_id=user.id,
            session_id=logical_session_id,
            live_session_id=live_session_id,
            tip_session_id=tip_session_id or logical_session_id,
            prompt=prompt,
            attachments=attachments,
            existing_messages=base_messages,
            draft_title=draft_title,
            mode=mode,
            request_id=request_id,
        )
        receipt_finished = await asyncio.to_thread(
            finish_turn_submission_receipt,
            user.id,
            request_id,
            session_id=logical_session_id,
        )

        live_state, display_meta = await asyncio.gather(
            asyncio.to_thread(get_live_session_state, user.id, logical_session_id),
            asyncio.to_thread(get_display_session_meta, user.id, logical_session_id),
        )
        normalized_session = _normalize_logical_session_row(
            projected_session
            or logical_session
            or {"id": logical_session_id, "source": "tui"},
            logical_session_id=logical_session_id,
            logical_session=logical_session
            or projected_session
            or {"id": logical_session_id, "source": "tui"},
            display_meta=display_meta,
            live_state=live_state,
            resume_session_id=tip_session_id or logical_session_id,
        )
        if created_new_session and not normalized_session.get("started_at"):
            normalized_session["started_at"] = _now_seconds()
            normalized_session["last_active"] = _now_seconds()
        return {
            "ok": True,
            "created": created_new_session,
            "session": normalized_session,
            "messages": [
                _normalize_display_message(item)
                for item in submit_result.get("messages", [])
            ],
            "live": live_state,
        }
    except asyncio.CancelledError:
        if not receipt_finished:
            failure_task = asyncio.create_task(
                asyncio.to_thread(
                    fail_turn_submission_receipt,
                    user.id,
                    request_id,
                    error_message="Turn submission request was cancelled",
                )
            )
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(failure_task)
        raise
    except TuiGatewayBridgeError as exc:
        if not receipt_finished:
            await asyncio.to_thread(
                fail_turn_submission_receipt,
                user.id,
                request_id,
                error_message=str(exc),
            )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        if not receipt_finished:
            await asyncio.to_thread(
                fail_turn_submission_receipt,
                user.id,
                request_id,
                error_message=str(exc) or type(exc).__name__,
            )
        raise
    finally:
        receipt_heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await receipt_heartbeat_task
        if bridge is not None:
            registry: TuiGatewayBridgeRegistry = app.state.tui_gateway_bridges
            await registry.maybe_close_if_unused(user.id)


@app.post("/api/sessions/{session_id}/interrupt")
async def interrupt_session_turn(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    bridge: TuiGatewayBridge | None = None
    try:
        bridge = await _get_tui_bridge_for_user(user)
        session_run_manager: SessionRunManager = app.state.session_run_manager
        result = await session_run_manager.interrupt_run(
            bridge=bridge,
            user_id=user.id,
            session_id=session_id,
        )
        live_state = await asyncio.to_thread(
            get_live_session_state, user.id, session_id
        )
        return {"ok": True, "result": result, "live": live_state}
    except TuiGatewayBridgeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        if bridge is not None:
            registry: TuiGatewayBridgeRegistry = app.state.tui_gateway_bridges
            await registry.maybe_close_if_unused(user.id)


@app.post("/api/sessions/{session_id}/approval")
async def respond_session_approval(
    session_id: str,
    payload: SessionApprovalRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    choice = str(payload.choice or "").strip().lower()
    if choice not in {"once", "session", "always", "deny"}:
        raise HTTPException(status_code=400, detail="Invalid approval choice")
    approval_id = str(payload.approval_id or "").strip()

    live_state = await asyncio.to_thread(get_live_session_state, user.id, session_id)
    pending_approval = (
        live_state.get("pending_approval") if isinstance(live_state, dict) else None
    )
    if (
        not isinstance(live_state, dict)
        or str(live_state.get("status") or "").strip() != "awaiting_approval"
        or not isinstance(pending_approval, dict)
        or not approval_id
        or str(pending_approval.get("approval_id") or "").strip() != approval_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Approval request is no longer pending",
        )

    bridge: TuiGatewayBridge | None = None
    try:
        bridge = await _get_tui_bridge_for_user(user)
        session_run_manager: SessionRunManager = app.state.session_run_manager
        result = await session_run_manager.respond_to_approval(
            bridge=bridge,
            user_id=user.id,
            session_id=session_id,
            choice=choice,
            approval_id=approval_id,
        )
        live_state = await asyncio.to_thread(
            get_live_session_state, user.id, session_id
        )
        return {"ok": True, "result": result, "live": live_state}
    except TuiGatewayBridgeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        if bridge is not None:
            registry: TuiGatewayBridgeRegistry = app.state.tui_gateway_bridges
            await registry.maybe_close_if_unused(user.id)


def _delete_session_sync(
    target: HermesTarget,
    session_id: str,
    *,
    owner_user_id: str,
    recipient_user_id: str,
) -> str:
    with _open_session_db(target) as db:
        logical_session_id, logical_session, _, _ = _resolve_logical_session_context(
            db, session_id
        )
        if not logical_session or not _is_interface_managed_source(
            logical_session.get("source")
        ):
            raise HTTPException(status_code=404, detail="Session not found")
        lifecycle_claim_id = ""
        try:
            lifecycle_claim_id = _invalidate_chat_share_session_lifecycle_sync(
                owner_user_id=owner_user_id,
                logical_session_id=logical_session_id,
            )
            for lineage_session_id in reversed(
                _collect_compression_lineage_session_ids(db, logical_session_id)
            ):
                _heartbeat_chat_share_session_lifecycle_sync(
                    owner_user_id=owner_user_id,
                    logical_session_id=logical_session_id,
                    lifecycle_claim_id=lifecycle_claim_id,
                )
                if not db.delete_session(lineage_session_id):
                    raise RuntimeError(f"Failed to delete session {lineage_session_id}")
        except Exception as exc:
            if lifecycle_claim_id and db.get_session(logical_session_id) is not None:
                with contextlib.suppress(Exception):
                    _rollback_chat_share_session_lifecycle_sync(
                        owner_user_id=owner_user_id,
                        logical_session_id=logical_session_id,
                        lifecycle_claim_id=lifecycle_claim_id,
                    )
            raise _chat_share_error(
                503,
                "sharing_cleanup_unavailable",
                "The chat could not be deleted because sharing state is unavailable.",
            ) from exc
        try:
            _complete_chat_share_session_lifecycle_sync(
                owner_user_id=owner_user_id,
                recipient_user_id=recipient_user_id,
                logical_session_id=logical_session_id,
                lifecycle_claim_id=lifecycle_claim_id,
            )
        except Exception:
            LOGGER.exception(
                "Failed to finalize chat share state for deleted session %s",
                logical_session_id,
            )
    return logical_session_id


def _delete_interface_session_state_sync(user_id: str, session_id: str) -> None:
    delete_display_messages(user_id, session_id)
    delete_live_session_state(user_id, session_id)
    delete_session_events(user_id, session_id)


def _load_direct_file_tree(
    user: CurrentUser,
    *,
    root: str | None,
    path: str | None,
) -> dict[str, Any]:
    browser_root = _resolve_file_browser_root(user, root)
    relative_path, target = _resolve_file_browser_target(browser_root, path)
    target = _authorize_file_browser_target(user, target)
    if not browser_root.exists():
        raise HTTPException(status_code=404, detail="Workspace root does not exist")
    _assert_user_can_open_directory(target, linux_user=user.target.linux_user)
    entries = _list_directory_as_user(
        target,
        relative_path=relative_path,
        linux_user=user.target.linux_user,
    )
    entries = _filter_file_browser_entries(user, target, entries)
    return {
        "root": str(browser_root),
        "path": relative_path,
        "entries": entries,
    }


def _open_direct_file_tree(user: CurrentUser, path: str) -> dict[str, Any]:
    target = _resolve_file_browser_root(user, path)
    _assert_user_can_open_directory(target, linux_user=user.target.linux_user)
    entries = _list_directory_as_user(
        target,
        relative_path="",
        linux_user=user.target.linux_user,
    )
    entries = _filter_file_browser_entries(user, target, entries)
    return {
        "mode": _normalized_file_browser_mode(),
        "root": str(target),
        "path": "",
        "opened_path": str(target),
        "entries": entries,
    }


def _resolve_direct_download_target(
    user: CurrentUser,
    *,
    root: str | None,
    path: str,
) -> Path:
    browser_root = _resolve_file_browser_root(user, root)
    _, target = _resolve_file_browser_target(browser_root, path)
    target = _authorize_file_browser_target(user, target)
    _assert_user_can_read_file(target, linux_user=user.target.linux_user)
    return target


@app.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    logical_session_id = await asyncio.to_thread(
        _delete_session_sync,
        user.target,
        session_id,
        owner_user_id=user.id,
        recipient_user_id=_chat_share_recipient_id(user),
    )
    await asyncio.to_thread(
        _delete_interface_session_state_sync,
        user.id,
        logical_session_id,
    )
    return {"ok": True}


@app.get("/api/files/revision")
async def files_revision(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, str]:
    manager: SessionRunManager = app.state.session_run_manager
    return {"revision": await manager.get_workspace_change_revision(user.id)}


@app.get("/api/files/tree")
async def files_tree(
    path: str | None = None,
    root: str | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    if _use_privileged_file_helper():
        try:
            payload = await asyncio.to_thread(
                privileged_client.file_tree,
                user.target.username,
                mode=_normalized_file_browser_mode(),
                root=root,
                path=path,
            )
        except PrivilegedMaintenanceError as exc:
            raise HTTPException(
                status_code=503, detail=MAINTENANCE_ERROR_MESSAGE
            ) from exc
        except PrivilegedClientError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {
            "root": str(payload.get("root") or ""),
            "path": str(payload.get("path") or ""),
            "entries": payload.get("entries")
            if isinstance(payload.get("entries"), list)
            else [],
        }

    return await asyncio.to_thread(
        _load_direct_file_tree,
        user,
        root=root,
        path=path,
    )


@app.get("/api/files/config")
async def files_config(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    root = _get_user_workspace_root(user)
    return {
        "mode": _normalized_file_browser_mode(),
        "home": str(user.target.home_dir),
        "root": str(root),
    }


@app.get("/api/files/open")
async def open_directory(
    path: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    mode = _normalized_file_browser_mode()
    if _use_privileged_file_helper():
        try:
            payload = await asyncio.to_thread(
                privileged_client.file_tree,
                user.target.username,
                mode=mode,
                root=path,
                path="",
            )
        except PrivilegedMaintenanceError as exc:
            raise HTTPException(
                status_code=503, detail=MAINTENANCE_ERROR_MESSAGE
            ) from exc
        except PrivilegedClientError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        opened_path = str(payload.get("root") or "")
        return {
            "mode": mode,
            "root": opened_path,
            "path": "",
            "opened_path": opened_path,
            "entries": payload.get("entries")
            if isinstance(payload.get("entries"), list)
            else [],
        }

    return await asyncio.to_thread(_open_direct_file_tree, user, path)


@app.get("/api/files/preview/meta")
async def files_preview_meta(
    path: str,
    root: str | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    info = await _load_file_preview_context(path=path, root=root, user=user)
    if not info["too_large"] and info["raw_preview_type"] in {"image", "pdf"}:
        info["content_url"] = _build_file_preview_content_url(path=path, root=root)
    return info


@app.get("/api/files/preview/text")
async def files_preview_text(
    path: str,
    root: str | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        opened = await asyncio.to_thread(
            _open_user_file_stream,
            user,
            root=root,
            path=path,
        )
    except PrivilegedMaintenanceError as exc:
        raise HTTPException(status_code=503, detail=MAINTENANCE_ERROR_MESSAGE) from exc
    except (OSError, RuntimeError, PrivilegedClientError) as exc:
        raise HTTPException(status_code=403, detail="File access denied") from exc
    info = _preview_info_from_metadata(opened.metadata, path=path, root=root)
    try:
        _ensure_previewable_size(info)
        if info["raw_preview_type"] != "text":
            raise HTTPException(
                status_code=415, detail="Requested file is not text-previewable"
            )
        data = await asyncio.to_thread(
            _read_open_file_stream_bytes,
            opened,
            MAX_PREVIEW_SIZE_BYTES,
        )
    except BaseException:
        if opened.process.poll() is None:
            await asyncio.to_thread(_terminate_file_stream, opened)
        raise
    content = data.decode("utf-8-sig", errors="replace")
    return {
        "filename": info["filename"],
        "size": info["size"],
        "modified": info["modified"],
        "mime_type": info["mime_type"],
        "preview_type": "text",
        "content": content,
    }


@app.get("/api/files/preview/content")
async def files_preview_content(
    path: str,
    root: str | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> FastAPIResponse:
    try:
        opened = await asyncio.to_thread(
            _open_user_file_stream,
            user,
            root=root,
            path=path,
        )
    except PrivilegedMaintenanceError as exc:
        raise HTTPException(status_code=503, detail=MAINTENANCE_ERROR_MESSAGE) from exc
    except (OSError, RuntimeError, PrivilegedClientError) as exc:
        raise HTTPException(status_code=403, detail="File access denied") from exc
    info = _preview_info_from_metadata(opened.metadata, path=path, root=root)
    try:
        _ensure_previewable_size(info)
        if info["raw_preview_type"] not in {"image", "pdf"}:
            raise HTTPException(
                status_code=415, detail="Requested file is not inline-previewable"
            )
    except BaseException:
        await asyncio.to_thread(_terminate_file_stream, opened)
        raise

    headers = {
        "Content-Disposition": _inline_content_disposition(str(info["filename"])),
        "X-Content-Type-Options": "nosniff",
    }
    if str(info["mime_type"]).split(";", 1)[0].strip().lower() == "image/svg+xml":
        headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        )
    size = info.get("size")
    if isinstance(size, int) and size >= 0:
        headers["Content-Length"] = str(size)

    return StreamingResponse(
        _iter_open_file_stream(opened, max_bytes=MAX_PREVIEW_SIZE_BYTES),
        media_type=str(info["mime_type"]),
        headers=headers,
    )


@app.get("/api/files/download")
async def files_download(
    path: str,
    root: str | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> FastAPIResponse:
    if not _DOWNLOAD_LIMITER.acquire(user.id):
        raise HTTPException(status_code=429, detail="Too many active file downloads")
    try:
        opened = await asyncio.to_thread(
            _open_user_file_stream,
            user,
            root=root,
            path=path,
        )
    except PrivilegedMaintenanceError as exc:
        _DOWNLOAD_LIMITER.release(user.id)
        raise HTTPException(status_code=503, detail=MAINTENANCE_ERROR_MESSAGE) from exc
    except (OSError, RuntimeError, PrivilegedClientError) as exc:
        _DOWNLOAD_LIMITER.release(user.id)
        raise HTTPException(status_code=403, detail="File access denied") from exc

    filename = str(opened.metadata["filename"])
    headers = {
        "Content-Disposition": _attachment_content_disposition(filename),
        "Content-Length": str(opened.metadata["size"]),
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        _iter_open_file_stream(opened, download_user_id=user.id),
        media_type="application/octet-stream",
        headers=headers,
    )


@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...), user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    content_type = file.content_type or "application/octet-stream"
    if _use_privileged_file_helper():
        command = privileged_client.file_upload_command(
            user.target.username,
            filename=file.filename,
            upload_dir_name=UPLOAD_DIR_NAME,
            max_bytes=MAX_UPLOAD_SIZE_BYTES,
        )
    else:
        command = build_file_upload_worker_command(
            linux_user=user.target.linux_user,
            home=user.target.home_dir,
            mapping_username=user.target.username,
            filename=file.filename,
            upload_dir_name=UPLOAD_DIR_NAME,
            max_bytes=MAX_UPLOAD_SIZE_BYTES,
        )
    try:
        stored = await asyncio.to_thread(
            run_upload_command,
            command,
            file.file,
            max_bytes=MAX_UPLOAD_SIZE_BYTES,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=413,
            detail=_upload_file_too_large_detail(),
        ) from exc
    except UploadMaintenanceError as exc:
        raise HTTPException(status_code=503, detail=MAINTENANCE_ERROR_MESSAGE) from exc
    except (OSError, RuntimeError, PrivilegedClientError) as exc:
        raise HTTPException(status_code=403, detail="File upload failed") from exc
    finally:
        await file.close()

    return {
        "id": uuid.uuid4().hex,
        "name": str(stored["name"]),
        "size": int(stored["size"]),
        "content_type": content_type,
        "path": str(stored["path"]),
    }


app.add_middleware(
    RequestBodyLimitMiddleware,
    limit_for_scope=_interface_request_body_limit,
    error_headers_for_scope=_interface_request_body_error_headers,
)
