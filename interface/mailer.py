from __future__ import annotations

import html
import logging
import os
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


async def send_signup_verification_email(
    *,
    email: str,
    code: str,
    verification_id: str,
    expires_at: int,
    settings: ResendSettings | None = None,
) -> ResendEmailResult:
    resend_settings = settings or get_resend_settings()
    payload: dict[str, object] = {
        "from": resend_settings.mail_from,
        "to": [email],
        "subject": "Your Potato Agent verification code",
        "text": _signup_verification_text(code),
        "html": _signup_verification_html(code),
    }
    if resend_settings.reply_to:
        payload["reply_to"] = resend_settings.reply_to

    headers = {
        "Authorization": f"Bearer {resend_settings.api_key}",
        "User-Agent": USER_AGENT,
        "Idempotency-Key": verification_id,
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
            "Resend rejected signup verification email: status=%s error_type=%s",
            response.status_code,
            error_type,
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
        "Resend accepted signup verification email: status=%s email_id=%s",
        response.status_code,
        email_id,
    )
    return ResendEmailResult(email_id=email_id, status_code=response.status_code)
