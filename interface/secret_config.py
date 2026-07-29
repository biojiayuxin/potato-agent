from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


MAX_SECRET_FILE_BYTES = 16 * 1024
PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production"})
VALID_ENVIRONMENTS = PRODUCTION_ENVIRONMENTS | {"dev", "development", "test"}


class SecretConfigurationError(RuntimeError):
    pass


def interface_environment() -> str:
    value = (os.getenv("INTERFACE_ENVIRONMENT") or "development").strip().lower()
    if value not in VALID_ENVIRONMENTS:
        choices = ", ".join(sorted(VALID_ENVIRONMENTS))
        raise SecretConfigurationError(
            f"INTERFACE_ENVIRONMENT must be one of: {choices}"
        )
    return value


def is_production_environment() -> bool:
    return interface_environment() in PRODUCTION_ENVIRONMENTS


def boolean_environment(name: str, *, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise SecretConfigurationError(
        f"{name} must be a boolean value (true/false, yes/no, on/off, or 1/0)"
    )


def load_session_cookie_secure() -> bool:
    production = is_production_environment()
    allow_insecure_http = boolean_environment(
        "INTERFACE_ALLOW_INSECURE_HTTP",
        default=False,
    )
    secure = boolean_environment(
        "INTERFACE_SESSION_COOKIE_SECURE",
        default=production,
    )
    if production and not secure and not allow_insecure_http:
        raise SecretConfigurationError(
            "INTERFACE_SESSION_COOKIE_SECURE cannot be disabled in production "
            "without INTERFACE_ALLOW_INSECURE_HTTP=true"
        )
    if production and secure and allow_insecure_http:
        raise SecretConfigurationError(
            "INTERFACE_ALLOW_INSECURE_HTTP=true requires "
            "INTERFACE_SESSION_COOKIE_SECURE=false in production"
        )
    return secure


def _read_private_secret_file(
    path: Path,
    *,
    source_name: str,
    allow_group_read: bool = False,
) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SecretConfigurationError(
            f"Unable to open secret source for {source_name}: {path}"
        ) from exc

    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SecretConfigurationError(
                f"Secret source for {source_name} is not a regular file: {path}"
            )
        forbidden_mode = 0o037 if allow_group_read else 0o077
        if stat.S_IMODE(file_stat.st_mode) & forbidden_mode:
            raise SecretConfigurationError(
                f"Secret source for {source_name} must not be accessible by group or other users: {path}"
            )
        if file_stat.st_nlink != 1:
            raise SecretConfigurationError(
                f"Secret source for {source_name} must have exactly one hard link: {path}"
            )
        if file_stat.st_uid not in {0, os.geteuid()}:
            raise SecretConfigurationError(
                f"Secret source for {source_name} must be owned by root or the service user: {path}"
            )
        if file_stat.st_size > MAX_SECRET_FILE_BYTES:
            raise SecretConfigurationError(
                f"Secret source for {source_name} exceeds {MAX_SECRET_FILE_BYTES} bytes"
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            value = handle.read(MAX_SECRET_FILE_BYTES + 1).rstrip("\r\n")
    except UnicodeError as exc:
        raise SecretConfigurationError(
            f"Secret source for {source_name} is not valid UTF-8"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if len(value.encode("utf-8")) > MAX_SECRET_FILE_BYTES:
        raise SecretConfigurationError(
            f"Secret source for {source_name} exceeds {MAX_SECRET_FILE_BYTES} bytes"
        )
    if not value:
        raise SecretConfigurationError(f"Secret source for {source_name} is empty")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise SecretConfigurationError(
            f"Secret source for {source_name} must contain exactly one text line"
        )
    return value


def _validate_systemd_credentials_directory(path: Path) -> None:
    if not path.is_absolute():
        raise SecretConfigurationError(
            f"CREDENTIALS_DIRECTORY must be an absolute path: {path}"
        )
    try:
        if path.resolve(strict=True) != path:
            raise SecretConfigurationError(
                f"CREDENTIALS_DIRECTORY must not contain symlinks: {path}"
            )
        directory_stat = path.stat()
    except OSError as exc:
        raise SecretConfigurationError(
            f"Unable to inspect CREDENTIALS_DIRECTORY: {path}"
        ) from exc
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise SecretConfigurationError(
            f"CREDENTIALS_DIRECTORY is not a directory: {path}"
        )
    if directory_stat.st_uid not in {0, os.geteuid()}:
        raise SecretConfigurationError(
            "CREDENTIALS_DIRECTORY must be owned by root or the service user: "
            f"{path}"
        )
    # systemd exposes credentials through an id-mapped, read-only directory. On
    # supported hosts that directory is 0550 and each credential is 0440.
    if stat.S_IMODE(directory_stat.st_mode) & 0o027:
        raise SecretConfigurationError(
            "CREDENTIALS_DIRECTORY must not be group-writable or accessible by "
            f"other users: {path}"
        )


def load_secret(
    environment_name: str,
    *,
    credential_name: str,
) -> str | None:
    direct_value = (os.getenv(environment_name) or "").strip()
    explicit_file = (os.getenv(f"{environment_name}_FILE") or "").strip()
    credentials_directory = (os.getenv("CREDENTIALS_DIRECTORY") or "").strip()
    credential_path = (
        Path(credentials_directory) / credential_name if credentials_directory else None
    )
    credential_exists = bool(credential_path and credential_path.exists())

    configured_sources = sum(
        (bool(direct_value), bool(explicit_file), credential_exists)
    )
    if configured_sources > 1:
        raise SecretConfigurationError(
            f"Configure only one source for {environment_name}: the environment value, "
            f"{environment_name}_FILE, or the {credential_name!r} systemd credential"
        )
    if direct_value and is_production_environment():
        raise SecretConfigurationError(
            f"{environment_name} must not be stored directly in the production "
            f"process environment; use {environment_name}_FILE or the "
            f"{credential_name!r} systemd credential"
        )
    if explicit_file:
        return _read_private_secret_file(
            Path(explicit_file), source_name=f"{environment_name}_FILE"
        )
    if credential_exists and credential_path is not None:
        _validate_systemd_credentials_directory(Path(credentials_directory))
        return _read_private_secret_file(
            credential_path,
            source_name=f"systemd credential {credential_name!r}",
            allow_group_read=True,
        )
    return direct_value or None


def load_session_secret() -> str:
    value = load_secret(
        "INTERFACE_SESSION_SECRET",
        credential_name="interface-session-secret",
    )
    production = is_production_environment()
    if value is None:
        if production:
            raise SecretConfigurationError(
                "INTERFACE_SESSION_SECRET is required in production; configure "
                "INTERFACE_SESSION_SECRET_FILE or the 'interface-session-secret' "
                "systemd credential"
            )
        return secrets.token_urlsafe(32)
    if production and len(value.encode("utf-8")) < 32:
        raise SecretConfigurationError(
            "INTERFACE_SESSION_SECRET must be at least 32 bytes in production"
        )
    return value
