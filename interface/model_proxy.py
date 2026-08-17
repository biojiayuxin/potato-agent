from __future__ import annotations

import json
import hmac
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from interface.mapping import DEFAULT_MAPPING_PATH, MappingStore, load_mapping
from interface.model_options import ModelOptionsError, normalize_model_options
from interface.model_proxy_config import (
    DEFAULT_MODEL_PROXY_CONFIG_PATH,
    ModelProxyConfigError,
    local_model_proxy_token,
    load_model_proxy_config,
)
from interface.secret_config import SecretConfigurationError, load_secret
from interface import token_usage_store
from interface.request_limits import RequestBodyLimitMiddleware
from interface import web_search


logger = logging.getLogger(__name__)

DEFAULT_UPSTREAM_TIMEOUT_SECONDS = float(
    os.getenv("POTATO_MODEL_PROXY_UPSTREAM_TIMEOUT_SECONDS") or "600"
)
DEFAULT_MODEL_PROXY_REQUEST_LIMIT_BYTES = 128 * 1024 * 1024
HARD_MODEL_PROXY_REQUEST_LIMIT_BYTES = 192 * 1024 * 1024
MAX_SSE_BUFFER_BYTES = 2 * 1024 * 1024
MAX_SSE_FRAME_BYTES = 1024 * 1024
MAX_USAGE_CAPTURE_BYTES = 4 * 1024 * 1024
MAX_NON_SSE_RESPONSE_BYTES = 64 * 1024 * 1024
REDACTED_SECRET_BYTES = b"[REDACTED]"
DAILY_UPDATES_SERVICE_PRINCIPAL = "daily-updates-service"
DAILY_UPDATES_SERVICE_TOKEN_ENVIRONMENT = "POTATO_DAILY_UPDATES_MODEL_PROXY_TOKEN"
DAILY_UPDATES_SERVICE_TOKEN_CREDENTIAL = "daily-updates-model-proxy-token"
DAILY_UPDATES_SERVICE_TOKEN_MIN_BYTES = 32
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
SAFE_UPSTREAM_RESPONSE_HEADERS = {
    "cache-control",
    "content-type",
    "openai-processing-ms",
    "openai-request-id",
    "request-id",
    "retry-after",
    "x-request-id",
    "x-should-retry",
}
SAFE_UPSTREAM_RESPONSE_HEADER_PREFIXES = (
    "anthropic-ratelimit-",
    "x-ratelimit-",
)


@dataclass(frozen=True)
class ProxyModel:
    id: str
    name: str
    provider: str
    model: str
    base_url: str
    api_key: str
    api_mode: str | None = None
    context_length: int | None = None
    reasoning_effort: str | None = None

    def to_models_api_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": self.name or self.model,
            "object": "model",
            "owned_by": self.provider or "custom",
        }
        if self.name and self.name != self.model:
            item["name"] = self.name
        if self.context_length is not None:
            item["context_length"] = self.context_length
        return item


class ModelProxyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProxyPrincipal:
    name: str
    is_daily_updates_service: bool = False


@dataclass
class UsageTelemetry:
    mapping_username: str
    endpoint: str
    route_model: str
    upstream_model: str
    provider: str
    api_mode: str
    status_code: int
    streaming: bool
    started_at: float
    raw_usage: dict[str, Any] | None = None


def _config_path() -> Path:
    return Path(
        os.getenv("POTATO_MODEL_PROXY_CONFIG_PATH") or DEFAULT_MODEL_PROXY_CONFIG_PATH
    )


def _mapping_path() -> Path:
    return Path(os.getenv("POTATO_AGENT_MAPPING_PATH") or DEFAULT_MAPPING_PATH)


def _load_proxy_models() -> dict[str, ProxyModel]:
    config = load_model_proxy_config(_config_path())
    raw_models = config.get("models")
    if not isinstance(raw_models, list):
        raise ModelProxyError("model_proxy.yaml models must be a list.")

    models: dict[str, ProxyModel] = {}
    for index, item in enumerate(raw_models):
        if not isinstance(item, dict):
            raise ModelProxyError(f"models[{index}] must be a mapping/object.")
        model = str(item.get("model") or item.get("id") or "").strip()
        base_url = str(item.get("base_url") or "").strip().rstrip("/")
        api_key = str(item.get("api_key") or "").strip()
        missing = [
            field
            for field, value in (
                ("model", model),
                ("base_url", base_url),
                ("api_key", api_key),
            )
            if not value
        ]
        if missing:
            raise ModelProxyError(
                f"models[{index}] is missing required field(s): {', '.join(missing)}."
            )
        route_name = str(item.get("name") or model).strip()
        if route_name in models:
            raise ModelProxyError(f"Duplicate proxy model name: {route_name}")
        context_length = item.get("context_length")
        models[route_name] = ProxyModel(
            id=str(item.get("id") or model).strip(),
            name=route_name,
            provider=str(item.get("provider") or "custom").strip(),
            model=model,
            base_url=base_url,
            api_key=api_key,
            api_mode=str(item.get("api_mode") or "").strip() or None,
            context_length=context_length if isinstance(context_length, int) else None,
            reasoning_effort=str(item.get("reasoning_effort") or "").strip() or None,
        )
    return models


def _extract_bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return token.strip()


def _load_daily_updates_service_token() -> str | None:
    try:
        token = load_secret(
            DAILY_UPDATES_SERVICE_TOKEN_ENVIRONMENT,
            credential_name=DAILY_UPDATES_SERVICE_TOKEN_CREDENTIAL,
        )
    except SecretConfigurationError as exc:
        logger.error("Unable to load model proxy service credential")
        raise HTTPException(
            status_code=503, detail="Model proxy is unavailable"
        ) from exc
    if token is not None and len(token.encode("utf-8")) < DAILY_UPDATES_SERVICE_TOKEN_MIN_BYTES:
        logger.error("Model proxy service credential does not meet length requirements")
        raise HTTPException(status_code=503, detail="Model proxy is unavailable")
    return token


def _require_principal(request: Request) -> ProxyPrincipal:
    supplied_token = _extract_bearer_token(request)
    try:
        targets = MappingStore(_mapping_path()).load_targets()
    except RuntimeError:
        logger.error("Unable to load model proxy client configuration")
        raise HTTPException(status_code=503, detail="Model proxy is unavailable")

    user_tokens: list[tuple[str, str]] = []
    for target in targets:
        try:
            expected_token = local_model_proxy_token(
                target.username, target.model_proxy_token
            )
        except ModelProxyConfigError:
            continue
        user_tokens.append((target.username, expected_token))

    service_token = _load_daily_updates_service_token()
    if service_token is not None and any(
        target.username == DAILY_UPDATES_SERVICE_PRINCIPAL for target in targets
    ):
        logger.error("Model proxy service principal conflicts with a mapped principal")
        raise HTTPException(status_code=503, detail="Model proxy is unavailable")
    service_token_collisions = (
        [
            hmac.compare_digest(service_token, user_token)
            for _, user_token in user_tokens
        ]
        if service_token is not None
        else []
    )
    if any(service_token_collisions):
        logger.error("Model proxy service credential conflicts with a user credential")
        raise HTTPException(status_code=503, detail="Model proxy is unavailable")

    matches = [
        ProxyPrincipal(name=username)
        for username, expected_token in user_tokens
        if hmac.compare_digest(supplied_token, expected_token)
    ]
    if service_token is not None and hmac.compare_digest(
        supplied_token, service_token
    ):
        matches.append(
            ProxyPrincipal(
                name=DAILY_UPDATES_SERVICE_PRINCIPAL,
                is_daily_updates_service=True,
            )
        )
    if len(matches) != 1:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    return matches[0]


def _authorized_model_names(principal: ProxyPrincipal) -> set[str]:
    try:
        options = normalize_model_options(load_mapping(_mapping_path(), resolve_env=True))
    except (RuntimeError, ModelOptionsError) as exc:
        logger.error("Unable to load model whitelist configuration")
        raise HTTPException(
            status_code=503, detail="Model proxy is unavailable"
        ) from exc
    if principal.is_daily_updates_service:
        return {options.primary.name}
    return {option.name for option in options.options}


def _sanitize_schema_required_fields(schema: Any) -> tuple[Any, bool]:
    if isinstance(schema, list):
        changed = False
        sanitized_items: list[Any] = []
        for item in schema:
            sanitized_item, item_changed = _sanitize_schema_required_fields(item)
            sanitized_items.append(sanitized_item)
            changed = changed or item_changed
        return sanitized_items, changed

    if not isinstance(schema, dict):
        return schema, False

    changed = False
    has_required = False
    sanitized: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "required":
            has_required = True
            if isinstance(value, list):
                required = [item for item in value if isinstance(item, str)]
                sanitized[key] = required
                changed = changed or len(required) != len(value)
            else:
                sanitized[key] = []
                changed = True
            continue

        sanitized_value, value_changed = _sanitize_schema_required_fields(value)
        sanitized[key] = sanitized_value
        changed = changed or value_changed

    is_object_schema = (
        sanitized.get("type") == "object"
        or isinstance(sanitized.get("properties"), dict)
    )
    if is_object_schema and not has_required:
        sanitized["required"] = []
        changed = True

    return sanitized, changed


def _sanitize_tool_schema(tool: Any) -> tuple[Any, bool]:
    if not isinstance(tool, dict):
        return tool, False

    changed = False
    sanitized = dict(tool)

    parameters = sanitized.get("parameters")
    if isinstance(parameters, dict):
        sanitized_parameters, params_changed = _sanitize_schema_required_fields(parameters)
        if params_changed:
            sanitized["parameters"] = sanitized_parameters
            changed = True

    function = sanitized.get("function")
    if isinstance(function, dict) and isinstance(function.get("parameters"), dict):
        sanitized_function = dict(function)
        sanitized_parameters, params_changed = _sanitize_schema_required_fields(
            sanitized_function["parameters"]
        )
        if params_changed:
            sanitized_function["parameters"] = sanitized_parameters
            sanitized["function"] = sanitized_function
            changed = True

    return sanitized, changed


def _sanitize_outbound_model_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return payload, False

    changed = False
    sanitized_tools: list[Any] = []
    for tool in tools:
        sanitized_tool, tool_changed = _sanitize_tool_schema(tool)
        sanitized_tools.append(sanitized_tool)
        changed = changed or tool_changed

    if not changed:
        return payload, False

    sanitized_payload = dict(payload)
    sanitized_payload["tools"] = sanitized_tools
    return sanitized_payload, True


def _responses_sse_text(state: dict[str, Any]) -> str:
    done_text = state.get("done_text")
    if isinstance(done_text, str) and done_text:
        return done_text
    parts = state.get("text_parts")
    if isinstance(parts, list):
        return "".join(part for part in parts if isinstance(part, str))
    return ""


def _is_valid_responses_output(output: Any) -> bool:
    if not isinstance(output, list):
        return False
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message" and not isinstance(item.get("content"), list):
            return False
    return True


def _synthesized_responses_output(state: dict[str, Any]) -> list[dict[str, Any]]:
    done_items = state.get("done_items")
    if isinstance(done_items, list):
        valid_items = [item for item in done_items if isinstance(item, dict)]
        if valid_items and _is_valid_responses_output(valid_items):
            return valid_items

    text = _responses_sse_text(state)
    if not text:
        return []
    item_id = state.get("last_item_id")
    output_item: dict[str, Any] = {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }
    if isinstance(item_id, str) and item_id.strip():
        output_item["id"] = item_id.strip()
    return [output_item]


def _normalize_responses_stream_payload(
    payload: Any, state: dict[str, Any]
) -> tuple[Any, bool]:
    if not isinstance(payload, dict):
        return payload, False

    event_type = str(payload.get("type") or "")
    if event_type == "response.output_text.delta":
        delta = payload.get("delta")
        if isinstance(delta, str) and delta:
            state.setdefault("text_parts", []).append(delta)
        item_id = payload.get("item_id")
        if isinstance(item_id, str) and item_id.strip():
            state["last_item_id"] = item_id.strip()
        return payload, False

    if event_type == "response.output_text.done":
        text = payload.get("text")
        if isinstance(text, str) and text:
            state["done_text"] = text
        item_id = payload.get("item_id")
        if isinstance(item_id, str) and item_id.strip():
            state["last_item_id"] = item_id.strip()
        return payload, False

    if event_type == "response.output_item.done":
        item = payload.get("item")
        if isinstance(item, dict):
            state.setdefault("done_items", []).append(item)
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id.strip():
                state["last_item_id"] = item_id.strip()
        return payload, False

    if event_type not in {
        "response.completed",
        "response.incomplete",
        "response.failed",
    }:
        return payload, False

    response = payload.get("response")
    if not isinstance(response, dict):
        return payload, False

    output = response.get("output")
    if _is_valid_responses_output(output):
        return payload, False

    normalized_payload = dict(payload)
    normalized_response = dict(response)
    normalized_response["output"] = _synthesized_responses_output(state)
    text = _responses_sse_text(state)
    if text and not isinstance(normalized_response.get("output_text"), str):
        normalized_response["output_text"] = text
    normalized_payload["response"] = normalized_response
    return normalized_payload, True


def _normalize_sse_frame(frame: bytes, state: dict[str, Any]) -> bytes:
    if not frame.strip():
        return frame
    try:
        text = frame.decode("utf-8")
    except UnicodeDecodeError:
        return frame

    lines = text.splitlines()
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if not data_lines:
        return frame

    data = "\n".join(data_lines)
    if data.strip() == "[DONE]":
        return frame
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return frame

    normalized_payload, changed = _normalize_responses_stream_payload(payload, state)
    if not changed:
        return frame

    normalized_data = json.dumps(
        normalized_payload, ensure_ascii=False, separators=(",", ":")
    )
    rebuilt: list[str] = []
    replaced_data = False
    for line in lines:
        if line.startswith("data:"):
            if not replaced_data:
                rebuilt.append(f"data: {normalized_data}")
                replaced_data = True
            continue
        rebuilt.append(line)
    return "\n".join(rebuilt).encode("utf-8")


def _sse_frame_payload(frame: bytes) -> Any | None:
    if not frame.strip():
        return None
    try:
        text = frame.decode("utf-8")
    except UnicodeDecodeError:
        return None

    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if not data_lines:
        return None

    data = "\n".join(data_lines)
    if data.strip() == "[DONE]":
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def _capture_usage_from_payload(
    payload: Any, telemetry: UsageTelemetry | None
) -> None:
    if telemetry is None or not isinstance(payload, dict):
        return
    usage = payload.get("usage")
    if isinstance(usage, dict):
        telemetry.raw_usage = usage
        return
    response = payload.get("response")
    if isinstance(response, dict) and isinstance(response.get("usage"), dict):
        telemetry.raw_usage = response["usage"]


def _capture_usage_from_sse_frame(
    frame: bytes, telemetry: UsageTelemetry | None
) -> None:
    _capture_usage_from_payload(_sse_frame_payload(frame), telemetry)


async def _iter_response_sse_bytes(
    response: httpx.Response,
    *,
    normalize_responses: bool = True,
    telemetry: UsageTelemetry | None = None,
):
    buffer = b""
    state: dict[str, Any] = {}
    async for chunk in response.aiter_bytes():
        for offset in range(0, len(chunk), 64 * 1024):
            buffer += chunk[offset : offset + 64 * 1024]
            while True:
                lf_pos = buffer.find(b"\n\n")
                crlf_pos = buffer.find(b"\r\n\r\n")
                positions = [pos for pos in (lf_pos, crlf_pos) if pos >= 0]
                if not positions:
                    break
                pos = min(positions)
                if pos > MAX_SSE_FRAME_BYTES:
                    raise RuntimeError("upstream SSE frame exceeded the size limit")
                sep_len = 4 if pos == crlf_pos else 2
                frame = buffer[:pos]
                buffer = buffer[pos + sep_len :]
                _capture_usage_from_sse_frame(frame, telemetry)
                if normalize_responses:
                    frame = _normalize_sse_frame(frame, state)
                yield frame + b"\n\n"
            if len(buffer) > MAX_SSE_BUFFER_BYTES:
                raise RuntimeError("upstream SSE buffer exceeded the size limit")
    if buffer:
        if len(buffer) > MAX_SSE_FRAME_BYTES:
            raise RuntimeError("upstream SSE frame exceeded the size limit")
        _capture_usage_from_sse_frame(buffer, telemetry)
        if normalize_responses:
            buffer = _normalize_sse_frame(buffer, state)
        yield buffer


def _json_body_usage(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    return usage if isinstance(usage, dict) else None


def _to_non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _dict_field_int(mapping: dict[str, Any] | None, *names: str) -> int:
    if not isinstance(mapping, dict):
        return 0
    for name in names:
        if name in mapping:
            value = _to_non_negative_int(mapping.get(name))
            if value:
                return value
    return 0


def _normalize_usage_tokens(
    raw_usage: dict[str, Any] | None,
    *,
    provider: str,
    api_mode: str,
) -> dict[str, int]:
    tokens = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    if not isinstance(raw_usage, dict):
        return tokens

    provider_name = provider.strip().lower()
    mode = api_mode.strip().lower()
    if "prompt_tokens" in raw_usage or "completion_tokens" in raw_usage:
        prompt_total = _dict_field_int(raw_usage, "prompt_tokens", "input_tokens")
        output_tokens = _dict_field_int(
            raw_usage, "completion_tokens", "output_tokens"
        )
        details = raw_usage.get("prompt_tokens_details")
        details_dict = details if isinstance(details, dict) else None
        cache_read_tokens = _dict_field_int(
            details_dict, "cached_tokens", "cache_read_tokens"
        ) or _dict_field_int(raw_usage, "cache_read_input_tokens", "cache_read_tokens")
        cache_write_tokens = _dict_field_int(
            details_dict,
            "cache_write_tokens",
            "cache_creation_tokens",
            "cache_creation_input_tokens",
        ) or _dict_field_int(
            raw_usage,
            "cache_creation_input_tokens",
            "cache_creation_tokens",
            "cache_write_tokens",
        )
        tokens["input_tokens"] = max(
            0, prompt_total - cache_read_tokens - cache_write_tokens
        )
        tokens["output_tokens"] = output_tokens
        tokens["cache_read_tokens"] = cache_read_tokens
        tokens["cache_write_tokens"] = cache_write_tokens
        return tokens

    if "input_tokens" in raw_usage or "output_tokens" in raw_usage:
        output_tokens = _dict_field_int(
            raw_usage, "output_tokens", "completion_tokens"
        )
        if mode == "anthropic_messages" or provider_name == "anthropic":
            tokens["input_tokens"] = _dict_field_int(raw_usage, "input_tokens")
            tokens["output_tokens"] = output_tokens
            tokens["cache_read_tokens"] = _dict_field_int(
                raw_usage, "cache_read_input_tokens", "cache_read_tokens"
            )
            tokens["cache_write_tokens"] = _dict_field_int(
                raw_usage,
                "cache_creation_input_tokens",
                "cache_creation_tokens",
                "cache_write_tokens",
            )
            return tokens

        input_total = _dict_field_int(raw_usage, "input_tokens", "prompt_tokens")
        details = raw_usage.get("input_tokens_details")
        details_dict = details if isinstance(details, dict) else None
        cache_read_tokens = _dict_field_int(
            details_dict, "cached_tokens", "cache_read_tokens"
        ) or _dict_field_int(raw_usage, "cache_read_input_tokens", "cache_read_tokens")
        cache_write_tokens = _dict_field_int(
            details_dict,
            "cache_creation_tokens",
            "cache_write_tokens",
            "cache_creation_input_tokens",
        ) or _dict_field_int(
            raw_usage,
            "cache_creation_input_tokens",
            "cache_creation_tokens",
            "cache_write_tokens",
        )
        tokens["input_tokens"] = max(
            0, input_total - cache_read_tokens - cache_write_tokens
        )
        tokens["output_tokens"] = output_tokens
        tokens["cache_read_tokens"] = cache_read_tokens
        tokens["cache_write_tokens"] = cache_write_tokens
    return tokens


def _record_completed_usage(telemetry: UsageTelemetry) -> None:
    if not (200 <= telemetry.status_code < 300):
        return
    completed_at = time.time()
    raw_usage = telemetry.raw_usage if isinstance(telemetry.raw_usage, dict) else None
    usage_status = "present" if raw_usage is not None else "missing"
    tokens = _normalize_usage_tokens(
        raw_usage, provider=telemetry.provider, api_mode=telemetry.api_mode
    )
    try:
        token_usage_store.record_usage_request(
            mapping_username=telemetry.mapping_username,
            endpoint=telemetry.endpoint,
            route_model=telemetry.route_model,
            upstream_model=telemetry.upstream_model,
            provider=telemetry.provider,
            api_mode=telemetry.api_mode,
            status_code=telemetry.status_code,
            streaming=telemetry.streaming,
            started_at=telemetry.started_at,
            completed_at=completed_at,
            duration_ms=max(0, int((completed_at - telemetry.started_at) * 1000)),
            input_tokens=tokens["input_tokens"],
            output_tokens=tokens["output_tokens"],
            cache_read_tokens=tokens["cache_read_tokens"],
            cache_write_tokens=tokens["cache_write_tokens"],
            usage_status=usage_status,
            raw_usage=raw_usage,
        )
    except Exception:
        logger.warning("Failed to record model proxy usage", exc_info=True)


def _select_model_for_body(
    principal: ProxyPrincipal, body: bytes
) -> tuple[ProxyModel, Any]:
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    model_name = str(payload.get("model") or "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model is required")

    if model_name not in _authorized_model_names(principal):
        raise HTTPException(status_code=403, detail="Model is not allowed")

    try:
        models = _load_proxy_models()
    except (ModelProxyConfigError, ModelProxyError) as exc:
        logger.error("Unable to load model proxy route configuration")
        raise HTTPException(status_code=503, detail="Model proxy is unavailable") from exc

    model = models.get(model_name)
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown model")
    return model, payload


def _enforce_user_quota(username: str) -> None:
    try:
        status = token_usage_store.get_user_quota_status(username)
    except Exception as exc:
        logger.error("Unable to evaluate model proxy quota")
        raise HTTPException(status_code=503, detail="Model proxy is unavailable") from exc
    if status["allowed"]:
        return
    reset_at = status.get("reset_at")
    retry_after = max(1, int(float(reset_at) - time.time())) if reset_at else 60
    raise HTTPException(
        status_code=429,
        detail="Model usage quota exceeded",
        headers={"Retry-After": str(retry_after)},
    )


def _redact_exact_secret(value: str, secret: str) -> str:
    return value.replace(secret, "[REDACTED]") if secret else value


def _response_headers(headers: httpx.Headers, *, upstream_api_key: str) -> dict[str, str]:
    safe_headers: dict[str, str] = {}
    for key, value in headers.items():
        normalized_key = key.lower()
        if normalized_key in HOP_BY_HOP_HEADERS:
            continue
        if (
            normalized_key not in SAFE_UPSTREAM_RESPONSE_HEADERS
            and not normalized_key.startswith(SAFE_UPSTREAM_RESPONSE_HEADER_PREFIXES)
        ):
            continue
        safe_headers[key] = _redact_exact_secret(value, upstream_api_key)
    return safe_headers


async def _redact_response_bytes(source, *, upstream_api_key: str):
    secret = upstream_api_key.encode("utf-8")
    if not secret:
        async for chunk in source:
            yield chunk
        return

    pending = b""
    keep_bytes = len(secret) - 1
    async for chunk in source:
        pending += chunk
        while True:
            secret_at = pending.find(secret)
            if secret_at < 0:
                break
            if secret_at:
                yield pending[:secret_at]
            yield REDACTED_SECRET_BYTES
            pending = pending[secret_at + len(secret) :]
        if len(pending) > keep_bytes:
            emit_length = len(pending) - keep_bytes
            yield pending[:emit_length]
            pending = pending[emit_length:]
    if pending:
        yield pending.replace(secret, REDACTED_SECRET_BYTES)


def _forward_headers(request: Request, model: ProxyModel) -> dict[str, str]:
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
        and key.lower() not in {"host", "authorization", "content-length"}
    }
    headers["authorization"] = f"Bearer {model.api_key}"
    return headers


async def _forward_model_request(request: Request, endpoint: str) -> Response:
    principal = _require_principal(request)
    body = await request.body()
    model, payload = _select_model_for_body(principal, body)
    _enforce_user_quota(principal.name)
    route_model = str(payload.get("model") or model.name).strip()
    sanitized_payload, payload_changed = _sanitize_outbound_model_payload(payload)
    if str(sanitized_payload.get("model") or "").strip() != model.model:
        sanitized_payload = dict(sanitized_payload)
        sanitized_payload["model"] = model.model
        payload_changed = True
    if payload_changed:
        body = json.dumps(
            sanitized_payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    upstream_url = f"{model.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = _forward_headers(request, model)

    timeout = httpx.Timeout(DEFAULT_UPSTREAM_TIMEOUT_SECONDS, connect=30.0)
    client = httpx.AsyncClient(timeout=timeout)
    req = client.build_request(
        request.method,
        upstream_url,
        content=body,
        headers=headers,
        params=request.query_params,
    )
    started_at = time.time()
    try:
        response = await client.send(req, stream=True)
    except Exception as exc:
        await client.aclose()
        logger.warning("Upstream model request failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Upstream model request failed") from exc
    response_content_type = str(response.headers.get("content-type") or "").lower()
    is_sse = "text/event-stream" in response_content_type
    should_normalize_sse = (
        endpoint.strip("/") == "responses"
        and is_sse
    )
    telemetry = UsageTelemetry(
        mapping_username=principal.name,
        endpoint=endpoint.strip("/"),
        route_model=route_model,
        upstream_model=model.model,
        provider=model.provider,
        api_mode=model.api_mode or "",
        status_code=response.status_code,
        streaming=is_sse,
        started_at=started_at,
    )

    if not 200 <= response.status_code < 300:
        status_code = response.status_code if 400 <= response.status_code <= 599 else 502
        await response.aclose()
        await client.aclose()
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "message": "Upstream model request failed",
                    "type": "upstream_error",
                }
            },
        )

    upstream_content_length = response.headers.get("content-length")
    if upstream_content_length is not None and not is_sse:
        try:
            declared_response_bytes = int(upstream_content_length)
        except (TypeError, ValueError):
            await response.aclose()
            await client.aclose()
            raise HTTPException(status_code=502, detail="Invalid upstream response")
        if declared_response_bytes < 0 or declared_response_bytes > MAX_NON_SSE_RESPONSE_BYTES:
            await response.aclose()
            await client.aclose()
            raise HTTPException(status_code=502, detail="Upstream response too large")

    async def body_iter():
        body_chunks: list[bytes] = []
        response_bytes = 0
        usage_capture_bytes = 0
        try:
            if is_sse:
                source = _iter_response_sse_bytes(
                    response,
                    normalize_responses=should_normalize_sse,
                    telemetry=telemetry,
                )
            else:
                async def non_sse_source():
                    nonlocal response_bytes, usage_capture_bytes
                    async for chunk in response.aiter_bytes():
                        response_bytes += len(chunk)
                        if response_bytes > MAX_NON_SSE_RESPONSE_BYTES:
                            raise RuntimeError("upstream response exceeded the size limit")
                        if usage_capture_bytes + len(chunk) <= MAX_USAGE_CAPTURE_BYTES:
                            body_chunks.append(chunk)
                            usage_capture_bytes += len(chunk)
                        elif body_chunks:
                            body_chunks.clear()
                        yield chunk

                source = non_sse_source()

            async for chunk in _redact_response_bytes(
                source, upstream_api_key=model.api_key
            ):
                yield chunk

            if not is_sse:
                if body_chunks:
                    telemetry.raw_usage = _json_body_usage(b"".join(body_chunks))
            _record_completed_usage(telemetry)
        finally:
            await response.aclose()
            await client.aclose()

    response_headers = _response_headers(
        response.headers, upstream_api_key=model.api_key
    )
    media_type = response_headers.pop("content-type", None)
    return StreamingResponse(
        body_iter(),
        status_code=response.status_code,
        headers=response_headers,
        media_type=media_type,
    )


app = FastAPI(title="Potato Model Proxy")


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True}


@app.get("/v1/models")
async def list_models(request: Request) -> dict[str, Any]:
    principal = _require_principal(request)
    allowed = _authorized_model_names(principal)
    try:
        models = _load_proxy_models()
    except (ModelProxyConfigError, ModelProxyError) as exc:
        logger.error("Unable to load model proxy route configuration")
        raise HTTPException(status_code=503, detail="Model proxy is unavailable") from exc
    return {
        "object": "list",
        "data": [
            model.to_models_api_item()
            for model_name, model in models.items()
            if model_name in allowed
        ],
    }


@app.get("/v1/models/{model_name:path}")
async def get_model(request: Request, model_name: str) -> dict[str, Any]:
    principal = _require_principal(request)
    allowed = _authorized_model_names(principal)
    if model_name not in allowed:
        raise HTTPException(status_code=403, detail="Model is not allowed")
    try:
        model = _load_proxy_models().get(model_name)
    except (ModelProxyConfigError, ModelProxyError) as exc:
        logger.error("Unable to load model proxy route configuration")
        raise HTTPException(status_code=503, detail="Model proxy is unavailable") from exc
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown model")
    return model.to_models_api_item()


@app.api_route("/v1/chat/completions", methods=["POST"])
async def chat_completions(request: Request) -> Response:
    return await _forward_model_request(request, "chat/completions")


@app.api_route("/v1/responses", methods=["POST"])
async def responses(request: Request) -> Response:
    return await _forward_model_request(request, "responses")


@app.post("/v1/search")
async def search(request: Request) -> Response:
    try:
        principal = _require_principal(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return web_search.error_response(
                web_search.SearchFailure(
                    401, "unauthorized", "Web search authorization failed", False
                )
            )
        return web_search.error_response(
            web_search.SearchFailure(
                503, "search_unavailable", "Web search is unavailable", False
            )
        )
    if principal.is_daily_updates_service:
        return web_search.error_response(
            web_search.SearchFailure(
                403,
                "search_forbidden",
                "This principal cannot use web search",
                False,
            )
        )
    return await web_search.execute_search(request, username=principal.name)


def _model_proxy_request_body_limit(scope: dict[str, Any]) -> int:
    if scope.get("path") == "/v1/search":
        return web_search.SEARCH_REQUEST_LIMIT_BYTES
    raw = os.getenv("POTATO_MODEL_PROXY_MAX_REQUEST_BYTES", "").strip()
    try:
        configured = int(raw) if raw else DEFAULT_MODEL_PROXY_REQUEST_LIMIT_BYTES
    except ValueError:
        configured = DEFAULT_MODEL_PROXY_REQUEST_LIMIT_BYTES
    return min(
        max(configured, 1),
        HARD_MODEL_PROXY_REQUEST_LIMIT_BYTES,
    )


def _model_proxy_request_limit_error(
    scope: dict[str, Any], status_code: int, detail: str
) -> dict[str, Any] | None:
    del detail
    if scope.get("path") == "/v1/search":
        return web_search.request_limit_error(status_code)
    return None


app.add_middleware(
    RequestBodyLimitMiddleware,
    limit_for_scope=_model_proxy_request_body_limit,
    error_body_for_scope=_model_proxy_request_limit_error,
)


@app.exception_handler(ModelProxyConfigError)
async def config_error_handler(_request: Request, exc: ModelProxyConfigError) -> JSONResponse:
    del exc
    return JSONResponse(status_code=503, content={"detail": "Model proxy is unavailable"})
