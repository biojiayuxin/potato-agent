from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gene_function_prediction.llm_client import (
    ResponsesClient,
    ResponsesConfig,
    extract_response_text,
    parse_json_object,
)


class FakeResponses:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.outputs)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses


class LLMClientTests(unittest.TestCase):
    def test_openai_env_uses_requested_model_and_reasoning(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "https://example.test/v1",
                "OPENAI_API_KEY": "key",
            },
            clear=True,
        ):
            config = ResponsesConfig.from_env()
        self.assertEqual(config.base_url, "https://example.test/v1")
        self.assertEqual(config.model, "gpt-5.6-sol")
        self.assertEqual(config.reasoning_effort, "xhigh")
        self.assertEqual(config.timeout_seconds, 180.0)

    def test_default_timeout_is_passed_to_openai_client(self):
        responses = FakeResponses(
            [SimpleNamespace(status="completed", output_text='{"ok":true}', id="r", model="m", usage=None)]
        )
        factory = Mock(return_value=FakeClient(responses))
        config = ResponsesConfig("https://example.test/v1", "key", "model")
        client = ResponsesClient(config, client_factory=factory)

        client.complete_json(
            instructions="Return JSON",
            input_payload={},
            schema_name="test",
            schema={"type": "object"},
        )

        self.assertEqual(config.timeout_seconds, 180.0)
        factory.assert_called_once_with(
            base_url="https://example.test/v1",
            api_key="key",
            timeout=180.0,
            max_retries=2,
        )

    def test_extract_nested_output_text(self):
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"ok":true}'}],
                }
            ]
        }
        self.assertEqual(extract_response_text(response), '{"ok":true}')

    def test_parse_json_with_markdown_or_leading_text(self):
        self.assertEqual(parse_json_object("```json\n{\"a\": 1}\n```"), {"a": 1})
        self.assertEqual(parse_json_object("result: {\"b\": 2}"), {"b": 2})

    def test_prompt_mode_retries_invalid_json_once(self):
        responses = FakeResponses(
            [
                SimpleNamespace(status="completed", output_text="not json", id="r1", model="m", usage=None),
                SimpleNamespace(status="completed", output_text='{\"ok\":true}', id="r2", model="m", usage=None),
            ]
        )
        config = ResponsesConfig("https://example.test/v1", "key", "model", structured_mode="prompt")
        client = ResponsesClient(config, client_factory=lambda **_: FakeClient(responses))
        result = client.complete_json(
            instructions="Return JSON",
            input_payload={"x": 1},
            schema_name="test",
            schema={"type": "object"},
            validator=lambda value: value,
        )
        self.assertEqual(result.data, {"ok": True})
        self.assertEqual(len(responses.calls), 2)
        self.assertNotIn("text", responses.calls[0])
        self.assertFalse(responses.calls[0]["store"])
        self.assertEqual(responses.calls[0]["reasoning"], {"effort": "xhigh"})
        self.assertIn("JSON Schema", responses.calls[0]["instructions"])

    def test_schema_mode_sends_responses_text_format(self):
        responses = FakeResponses(
            [SimpleNamespace(status="completed", output_text='{\"ok\":true}', id="r", model="m", usage=None)]
        )
        config = ResponsesConfig("https://example.test/v1", "key", "model", structured_mode="schema")
        client = ResponsesClient(config, client_factory=lambda **_: FakeClient(responses))
        client.complete_json(
            instructions="Return JSON",
            input_payload={},
            schema_name="test_schema",
            schema={"type": "object"},
        )
        self.assertEqual(responses.calls[0]["text"]["format"]["type"], "json_schema")


if __name__ == "__main__":
    unittest.main()
