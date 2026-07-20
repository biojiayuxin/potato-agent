#!/usr/bin/env python3
"""Small OpenAI-compatible Responses API client used by the pipeline."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar


class LLMError(RuntimeError):
    """Raised when a Responses request cannot produce valid JSON."""


T = TypeVar("T", bound=dict[str, Any])


@dataclass(frozen=True)
class ResponsesConfig:
    base_url: str
    api_key: str
    model: str
    reasoning_effort: str = "xhigh"
    concurrency: int = 4
    timeout_seconds: float = 180.0
    max_retries: int = 2
    max_output_tokens: int = 900
    structured_mode: str = "prompt"

    @classmethod
    def from_env(cls) -> "ResponsesConfig":
        base_url = _first_env("GENE_FUNCTION_LLM_BASE_URL", "OPENAI_BASE_URL")
        api_key = _first_env("GENE_FUNCTION_LLM_API_KEY", "OPENAI_API_KEY")
        model = _first_env("GENE_FUNCTION_LLM_MODEL", "OPENAI_MODEL") or "gpt-5.6-sol"
        missing = [
            name
            for name, value in (
                ("GENE_FUNCTION_LLM_BASE_URL", base_url),
                ("GENE_FUNCTION_LLM_API_KEY", api_key),
            )
            if not value or "YOUR_" in value.upper()
        ]
        if missing:
            raise LLMError("Missing LLM configuration: " + ", ".join(missing))

        structured_mode = os.getenv("GENE_FUNCTION_LLM_STRUCTURED_MODE", "prompt").strip().lower()
        if structured_mode not in {"prompt", "schema"}:
            raise LLMError("GENE_FUNCTION_LLM_STRUCTURED_MODE must be prompt or schema")
        return cls(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            reasoning_effort="xhigh",
            concurrency=_positive_env_int("GENE_FUNCTION_LLM_CONCURRENCY", 4),
            timeout_seconds=_positive_env_float("GENE_FUNCTION_LLM_TIMEOUT_SECONDS", 180.0),
            max_retries=_non_negative_env_int("GENE_FUNCTION_LLM_MAX_RETRIES", 2),
            max_output_tokens=_positive_env_int("GENE_FUNCTION_LLM_MAX_OUTPUT_TOKENS", 900),
            structured_mode=structured_mode,
        )

    def cache_identity(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "max_output_tokens": self.max_output_tokens,
            "structured_mode": self.structured_mode,
        }


@dataclass(frozen=True)
class ResponsesResult:
    data: dict[str, Any]
    response_id: str
    model: str
    usage: dict[str, Any]


class ResponsesClient:
    """Non-streaming Responses client with bounded concurrency and one JSON retry."""

    def __init__(
        self,
        config: ResponsesConfig,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory
        self._clients = threading.local()
        self._semaphore = threading.BoundedSemaphore(config.concurrency)

    def complete_json(
        self,
        *,
        instructions: str,
        input_payload: Mapping[str, Any],
        schema_name: str,
        schema: dict[str, Any],
        validator: Callable[[dict[str, Any]], T] | None = None,
    ) -> ResponsesResult:
        last_error = "unknown JSON response error"
        for semantic_attempt in range(2):
            attempt_instructions = instructions
            if self.config.structured_mode == "prompt":
                attempt_instructions += (
                    "\n\nReturn exactly one JSON object matching this JSON Schema:\n"
                    + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
                )
            if semantic_attempt:
                attempt_instructions += (
                    "\n\nThe previous response was invalid. Return exactly one JSON object "
                    "matching the requested schema, without Markdown or commentary."
                )
            response = self._request(
                    instructions=attempt_instructions,
                    input_payload=input_payload,
                    schema_name=schema_name,
                    schema=schema,
                )
            try:
                status = _get(response, "status")
                if status and status != "completed":
                    raise LLMError(f"Responses request ended with status={status}")
                parsed = parse_json_object(extract_response_text(response))
                if validator is not None:
                    parsed = validator(parsed)
                return ResponsesResult(
                    data=parsed,
                    response_id=str(_get(response, "id") or ""),
                    model=str(_get(response, "model") or self.config.model),
                    usage=_as_dict(_get(response, "usage")),
                )
            except (LLMError, ValueError, TypeError, KeyError) as exc:
                last_error = str(exc)
        raise LLMError(last_error)

    def _request(
        self,
        *,
        instructions: str,
        input_payload: Mapping[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "instructions": instructions,
            "input": json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")),
            "max_output_tokens": self.config.max_output_tokens,
            "reasoning": {"effort": self.config.reasoning_effort},
            "store": False,
        }
        if self.config.structured_mode == "schema":
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            }
        with self._semaphore:
            try:
                return self._get_client().responses.create(**kwargs)
            except Exception as exc:  # The SDK exposes provider-specific exception classes.
                raise LLMError(f"Responses API request failed: {exc}") from exc

    def _get_client(self) -> Any:
        client = getattr(self._clients, "value", None)
        if client is not None:
            return client
        factory = self._client_factory
        if factory is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise LLMError(
                    "The openai package is required; install gene_function_prediction/requirements.txt"
                ) from exc
            factory = OpenAI
        client = factory(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=self.config.timeout_seconds,
            max_retries=self.config.max_retries,
        )
        self._clients.value = client
        return client


def extract_response_text(response: Any) -> str:
    direct = _get(response, "output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    blocks: list[str] = []
    output = _get(response, "output")
    if isinstance(output, list):
        for item in output:
            if _get(item, "type") != "message":
                continue
            content = _get(item, "content")
            if not isinstance(content, list):
                continue
            for part in content:
                if _get(part, "type") not in {"output_text", "text"}:
                    continue
                text = _get(part, "text")
                if isinstance(text, str) and text.strip():
                    blocks.append(text.strip())
    if not blocks:
        raise LLMError("Responses API returned no output text")
    return "\n".join(blocks)


def parse_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LLMError("Responses output does not contain a JSON object")


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _positive_env_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise LLMError(f"{name} must be >= 1")
    return value


def _non_negative_env_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise LLMError(f"{name} must be >= 0")
    return value


def _positive_env_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise LLMError(f"{name} must be > 0")
    return value


def _get(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        result = model_dump()
        return result if isinstance(result, dict) else {}
    return {}
