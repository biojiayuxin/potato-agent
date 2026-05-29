from __future__ import annotations

import json
import os
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
    load_model_proxy_config,
    username_from_local_token,
)


DEFAULT_UPSTREAM_TIMEOUT_SECONDS = float(
    os.getenv("POTATO_MODEL_PROXY_UPSTREAM_TIMEOUT_SECONDS") or "600"
)
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


def _require_username(request: Request) -> str:
    username = username_from_local_token(_extract_bearer_token(request))
    if username is None:
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    if MappingStore(_mapping_path()).get_target_by_username(username) is None:
        raise HTTPException(status_code=403, detail="Unknown user")
    return username


def _authorized_model_names(username: str) -> set[str]:
    del username
    try:
        options = normalize_model_options(load_mapping(_mapping_path(), resolve_env=True))
    except (RuntimeError, ModelOptionsError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Invalid model whitelist configuration: {exc}"
        ) from exc
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


async def _iter_response_sse_bytes(response: httpx.Response):
    buffer = b""
    state: dict[str, Any] = {}
    async for chunk in response.aiter_bytes():
        buffer += chunk
        while True:
            lf_pos = buffer.find(b"\n\n")
            crlf_pos = buffer.find(b"\r\n\r\n")
            positions = [pos for pos in (lf_pos, crlf_pos) if pos >= 0]
            if not positions:
                break
            pos = min(positions)
            sep_len = 4 if pos == crlf_pos else 2
            frame = buffer[:pos]
            buffer = buffer[pos + sep_len :]
            yield _normalize_sse_frame(frame, state) + b"\n\n"
    if buffer:
        yield _normalize_sse_frame(buffer, state)


def _select_model_for_body(username: str, body: bytes) -> tuple[ProxyModel, Any]:
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    model_name = str(payload.get("model") or "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model is required")

    if model_name not in _authorized_model_names(username):
        raise HTTPException(status_code=403, detail="Model is not allowed")

    try:
        models = _load_proxy_models()
    except (ModelProxyConfigError, ModelProxyError) as exc:
        raise HTTPException(status_code=500, detail=f"Invalid proxy config: {exc}") from exc

    model = models.get(model_name)
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown model")
    return model, payload


def _response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }


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
    username = _require_username(request)
    body = await request.body()
    model, payload = _select_model_for_body(username, body)
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
    response = await client.send(req, stream=True)
    should_normalize_sse = (
        endpoint.strip("/") == "responses"
        and "text/event-stream" in str(response.headers.get("content-type") or "").lower()
    )

    async def body_iter():
        try:
            body_source = (
                _iter_response_sse_bytes(response)
                if should_normalize_sse
                else response.aiter_bytes()
            )
            async for chunk in body_source:
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    response_headers = _response_headers(response.headers)
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
    username = _require_username(request)
    allowed = _authorized_model_names(username)
    try:
        models = _load_proxy_models()
    except (ModelProxyConfigError, ModelProxyError) as exc:
        raise HTTPException(status_code=500, detail=f"Invalid proxy config: {exc}") from exc
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
    username = _require_username(request)
    allowed = _authorized_model_names(username)
    if model_name not in allowed:
        raise HTTPException(status_code=403, detail="Model is not allowed")
    try:
        model = _load_proxy_models().get(model_name)
    except (ModelProxyConfigError, ModelProxyError) as exc:
        raise HTTPException(status_code=500, detail=f"Invalid proxy config: {exc}") from exc
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown model")
    return model.to_models_api_item()


@app.api_route("/v1/chat/completions", methods=["POST"])
async def chat_completions(request: Request) -> Response:
    return await _forward_model_request(request, "chat/completions")


@app.api_route("/v1/responses", methods=["POST"])
async def responses(request: Request) -> Response:
    return await _forward_model_request(request, "responses")


@app.exception_handler(ModelProxyConfigError)
async def config_error_handler(_request: Request, exc: ModelProxyConfigError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})
