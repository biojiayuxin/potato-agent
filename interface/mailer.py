from __future__ import annotations

import html
import logging
import os
import uuid
from dataclasses import dataclass

import httpx


LOGGER = logging.getLogger("potato_interface.mailer")
RESEND_API_BASE_URL = "https://api.resend.com"
USER_AGENT = "potato-agent-interface/1.0"


@dataclass(frozen=True)
class ResendSettings:
    api_key: str
    mail_from: str
    reply_to: str = ""


@dataclass(frozen=True)
class ResendEmailResult:
    email_id: str
    status_code: int


class MailerConfigurationError(RuntimeError):
    pass


class MailerDeliveryError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


def get_resend_settings() -> ResendSettings:
    api_key = (os.getenv("INTERFACE_RESEND_API_KEY") or "").strip()
    mail_from = (os.getenv("INTERFACE_MAIL_FROM") or "").strip()
    reply_to = (os.getenv("INTERFACE_MAIL_REPLY_TO") or "").strip()
    if not api_key:
        raise MailerConfigurationError("INTERFACE_RESEND_API_KEY is not configured")
    if not mail_from:
        raise MailerConfigurationError("INTERFACE_MAIL_FROM is not configured")
    return ResendSettings(api_key=api_key, mail_from=mail_from, reply_to=reply_to)


def _signup_verification_text(code: str) -> str:
    return (
        "Your Potato Agent verification code is:\n\n"
        f"{code}\n\n"
        "This code expires in 10 minutes. If you did not request this code, you can ignore this email."
    )


def _signup_verification_html(code: str) -> str:
    escaped_code = html.escape(code)
    return (
        "<div style=\"font-family:Arial,sans-serif;line-height:1.5;color:#14213d\">"
        "<h2>Verify your Potato Agent email</h2>"
        "<p>Your verification code is:</p>"
        "<p style=\"font-size:28px;font-weight:700;letter-spacing:4px\">"
        f"{escaped_code}"
        "</p>"
        "<p>This code expires in 10 minutes.</p>"
        "<p>If you did not request this code, you can ignore this email.</p>"
        "</div>"
    )


def _password_reset_text(code: str) -> str:
    return (
        "Your Potato Agent password reset code is:\n\n"
        f"{code}\n\n"
        "This code expires in 10 minutes. If you did not request a password reset, you can ignore this email."
    )


def _password_reset_html(code: str) -> str:
    escaped_code = html.escape(code)
    return (
        "<div style=\"font-family:Arial,sans-serif;line-height:1.5;color:#14213d\">"
        "<h2>Reset your Potato Agent password</h2>"
        "<p>Your password reset code is:</p>"
        "<p style=\"font-size:28px;font-weight:700;letter-spacing:4px\">"
        f"{escaped_code}"
        "</p>"
        "<p>This code expires in 10 minutes.</p>"
        "<p>If you did not request a password reset, you can ignore this email.</p>"
        "</div>"
    )


def _password_rotation_notice_text(
    *, username: str, new_password: str, site_url: str = ""
) -> str:
    signin_line = (
        f"Please sign in to the Potato Agent website at {site_url} and change it "
        "to your own preferred password."
        if site_url
        else "Please sign in to the Potato Agent website and change it to your "
        "own preferred password."
    )
    return (
        f"Hello {username},\n\n"
        "For security reasons, we have changed the password for your Potato "
        "Agent account.\n\n"
        f"Your temporary password is:\n\n{new_password}\n\n"
        f"{signin_line}\n\n"
        "Your new password must be at least 8 characters and include uppercase "
        "letters, lowercase letters, numbers, and symbols.\n\n"
        "If you did not expect this notice, please contact the administrator."
    )


def _password_rotation_notice_html(
    *, username: str, new_password: str, site_url: str = ""
) -> str:
    escaped_username = html.escape(username)
    escaped_password = html.escape(new_password)
    escaped_site_url = html.escape(site_url)
    if site_url:
        signin_html = (
            "Please sign in to the Potato Agent website at "
            f"<a href=\"{escaped_site_url}\">{escaped_site_url}</a> and change "
            "it to your own preferred password."
        )
    else:
        signin_html = (
            "Please sign in to the Potato Agent website and change it to your "
            "own preferred password."
        )
    return (
        "<div style=\"font-family:Arial,sans-serif;line-height:1.5;color:#14213d\">"
        f"<p>Hello {escaped_username},</p>"
        "<p>For security reasons, we have changed the password for your Potato "
        "Agent account.</p>"
        "<p>Your temporary password is:</p>"
        "<p><code style=\"font-size:18px;font-weight:700\">"
        f"{escaped_password}"
        "</code></p>"
        f"<p>{signin_html}</p>"
        "<p>Your new password must be at least 8 characters and include uppercase "
        "letters, lowercase letters, numbers, and symbols.</p>"
        "<p>If you did not expect this notice, please contact the administrator.</p>"
        "</div>"
    )


def _resend_error_details(response: httpx.Response) -> tuple[str, str]:
    try:
        payload = response.json()
    except Exception:
        return "", response.text[:500]

    error_payload = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error_payload, dict):
        error_type = str(error_payload.get("type") or error_payload.get("name") or "")
        message = str(error_payload.get("message") or payload)[:500]
        return error_type, message
    if isinstance(payload, dict):
        error_type = str(payload.get("type") or payload.get("name") or "")
        message = str(payload.get("message") or payload)[:500]
        return error_type, message
    return "", str(payload)[:500]


async def send_resend_email(
    *,
    email: str,
    subject: str,
    text: str,
    html: str,
    idempotency_key: str | None = None,
    settings: ResendSettings | None = None,
) -> ResendEmailResult:
    resend_settings = settings or get_resend_settings()
    normalized_email = email.strip()
    if not normalized_email:
        raise ValueError("email cannot be empty")
    normalized_subject = subject.strip()
    if not normalized_subject:
        raise ValueError("subject cannot be empty")

    payload: dict[str, object] = {
        "from": resend_settings.mail_from,
        "to": [normalized_email],
        "subject": normalized_subject,
        "text": text,
        "html": html,
    }
    if resend_settings.reply_to:
        payload["reply_to"] = resend_settings.reply_to

    headers = {
        "Authorization": f"Bearer {resend_settings.api_key}",
        "User-Agent": USER_AGENT,
        "Idempotency-Key": idempotency_key or str(uuid.uuid4()),
    }
    timeout = httpx.Timeout(15.0, connect=5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{RESEND_API_BASE_URL}/emails",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise MailerDeliveryError(
            "Resend request failed", error_type=type(exc).__name__
        ) from exc

    if response.status_code < 200 or response.status_code >= 300:
        error_type, message = _resend_error_details(response)
        LOGGER.warning(
            "Resend rejected email: status=%s error_type=%s subject=%s",
            response.status_code,
            error_type,
            normalized_subject,
        )
        raise MailerDeliveryError(
            message or "Resend rejected email",
            status_code=response.status_code,
            error_type=error_type,
        )

    try:
        data = response.json()
    except Exception:
        data = {}
    email_id = str(data.get("id") or "") if isinstance(data, dict) else ""
    LOGGER.info(
        "Resend accepted email: status=%s email_id=%s subject=%s",
        response.status_code,
        email_id,
        normalized_subject,
    )
    return ResendEmailResult(email_id=email_id, status_code=response.status_code)


async def send_signup_verification_email(
    *,
    email: str,
    code: str,
    verification_id: str,
    expires_at: int,
    settings: ResendSettings | None = None,
) -> ResendEmailResult:
    result = await send_resend_email(
        email=email,
        subject="Your Potato Agent verification code",
        text=_signup_verification_text(code),
        html=_signup_verification_html(code),
        idempotency_key=verification_id,
        settings=settings,
    )
    LOGGER.info(
        "Resend accepted signup verification email: status=%s email_id=%s",
        result.status_code,
        result.email_id,
    )
    return result


async def send_password_reset_email(
    *,
    email: str,
    code: str,
    verification_id: str,
    expires_at: int,
    settings: ResendSettings | None = None,
) -> ResendEmailResult:
    result = await send_resend_email(
        email=email,
        subject="Your Potato Agent password reset code",
        text=_password_reset_text(code),
        html=_password_reset_html(code),
        idempotency_key=verification_id,
        settings=settings,
    )
    LOGGER.info(
        "Resend accepted password reset email: status=%s email_id=%s",
        result.status_code,
        result.email_id,
    )
    return result


async def send_password_rotation_notice_email(
    *,
    email: str,
    username: str,
    new_password: str,
    idempotency_key: str,
    site_url: str = "",
    settings: ResendSettings | None = None,
) -> ResendEmailResult:
    return await send_resend_email(
        email=email,
        subject="Potato Agent password changed for security reasons",
        text=_password_rotation_notice_text(
            username=username,
            new_password=new_password,
            site_url=site_url.strip(),
        ),
        html=_password_rotation_notice_html(
            username=username,
            new_password=new_password,
            site_url=site_url.strip(),
        ),
        idempotency_key=idempotency_key,
        settings=settings,
    )
