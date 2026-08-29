from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


ASGIApp = Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]


@dataclass(frozen=True)
class _RequestBodyError(Exception):
    status_code: int
    detail: str


def _declared_content_length(scope: dict[str, Any]) -> int | None:
    raw_values = [
        value
        for name, value in scope.get("headers", [])
        if bytes(name).lower() == b"content-length"
    ]
    if not raw_values:
        return None

    parsed_values: list[int] = []
    for raw_value in raw_values:
        try:
            text = bytes(raw_value).decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise _RequestBodyError(400, "Invalid Content-Length") from exc
        if not text or not text.isdecimal():
            raise _RequestBodyError(400, "Invalid Content-Length")
        parsed_values.append(int(text, 10))
    if len(set(parsed_values)) != 1:
        raise _RequestBodyError(400, "Conflicting Content-Length headers")
    return parsed_values[0]


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limit_for_scope: Callable[[dict[str, Any]], int],
        error_body_for_scope: Callable[
            [dict[str, Any], int, str], dict[str, Any] | None
        ]
        | None = None,
        error_headers_for_scope: Callable[
            [dict[str, Any], int], list[tuple[bytes, bytes]]
        ]
        | None = None,
    ) -> None:
        self.app = app
        self.limit_for_scope = limit_for_scope
        self.error_body_for_scope = error_body_for_scope
        self.error_headers_for_scope = error_headers_for_scope

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            limit = max(0, int(self.limit_for_scope(scope)))
            declared = _declared_content_length(scope)
            if declared is not None and declared > limit:
                raise _RequestBodyError(413, "Request body too large")

            received = 0

            async def limited_receive() -> dict[str, Any]:
                nonlocal received
                message = await receive()
                if message.get("type") != "http.request":
                    return message
                body = message.get("body", b"")
                if not isinstance(body, bytes):
                    body = bytes(body)
                received += len(body)
                if received > limit:
                    raise _RequestBodyError(413, "Request body too large")
                if declared is not None and received > declared:
                    raise _RequestBodyError(400, "Content-Length does not match request body")
                if not bool(message.get("more_body", False)):
                    if declared is not None and received != declared:
                        raise _RequestBodyError(
                            400,
                            "Content-Length does not match request body",
                        )
                return message

            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyError as exc:
            if response_started:
                raise
            content = (
                self.error_body_for_scope(scope, exc.status_code, exc.detail)
                if self.error_body_for_scope is not None
                else None
            )
            body = json.dumps(
                content if content is not None else {"detail": exc.detail},
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            extra_headers = (
                self.error_headers_for_scope(scope, exc.status_code)
                if self.error_headers_for_scope is not None
                else []
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": exc.status_code,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        *extra_headers,
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
