"""Refiner backends: prompt building, tolerant JSON parsing, and the exact
request shapes of the Anthropic / OpenAI-compatible HTTP calls (mocked
transport — no network), including retry-on-transport-error behavior."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from toskana.refiner import (
    AnthropicBackend,
    OpenAICompatBackend,
    RefinerCategory,
    RefinerError,
    build_prompt,
    parse_refiner_response,
)

CATEGORIES = [
    RefinerCategory(key="drink", name="Drink (Getränk)"),
    RefinerCategory(key="main", name="Main"),
]
MENU = ["Spritzer", "Wiener Schnitzel"]
JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


class TestPrompt:
    def test_contains_categories_menu_and_json_contract(self) -> None:
        prompt = build_prompt(CATEGORIES, MENU)
        assert "restaurant camera" in prompt
        assert "- drink: Drink (Getränk)" in prompt
        assert "- main: Main" in prompt
        assert "- Spritzer" in prompt
        assert "- Wiener Schnitzel" in prompt
        assert '"category_key"' in prompt
        assert '"menu_item_name"' in prompt
        assert '"is_item"' in prompt
        assert "is_item=false if the image shows no food/drink item" in prompt

    def test_menu_section_omitted_without_items(self) -> None:
        prompt = build_prompt(CATEGORIES, [])
        assert "Menu" not in prompt
        assert "- drink:" in prompt


class TestParse:
    def test_plain_json(self) -> None:
        result = parse_refiner_response(
            '{"category_key": "drink", "menu_item_name": "Spritzer",'
            ' "confidence": 0.87, "is_item": true}'
        )
        assert result.category_key == "drink"
        assert result.menu_item_name == "Spritzer"
        assert result.confidence == pytest.approx(0.87)
        assert result.is_item is True
        assert "Spritzer" in result.raw_response

    def test_fenced_json(self) -> None:
        result = parse_refiner_response(
            '```json\n{"category_key": "main", "menu_item_name": null,'
            ' "confidence": 0.5, "is_item": true}\n```'
        )
        assert result.category_key == "main"
        assert result.menu_item_name is None

    def test_dirty_reply_with_prose_around_json(self) -> None:
        result = parse_refiner_response(
            'Sure! Looking at the image: {"category_key": "drink", '
            '"menu_item_name": "spritzer", "confidence": 0.7, "is_item": true} '
            "Hope that helps."
        )
        assert result.category_key == "drink"
        assert result.menu_item_name == "spritzer"

    def test_is_item_false_and_nulls(self) -> None:
        result = parse_refiner_response(
            '{"category_key": null, "menu_item_name": null, "confidence": 0.9, "is_item": false}'
        )
        assert result.category_key is None
        assert result.menu_item_name is None
        assert result.is_item is False

    def test_confidence_clamped_and_defaulted(self) -> None:
        assert parse_refiner_response('{"confidence": 7}').confidence == 1.0
        assert parse_refiner_response('{"confidence": "n/a"}').confidence == 0.0
        assert parse_refiner_response("{}").confidence == 0.0

    def test_non_json_raises(self) -> None:
        with pytest.raises(RefinerError):
            parse_refiner_response("I cannot tell what this is.")

    def test_json_array_raises(self) -> None:
        with pytest.raises(RefinerError):
            parse_refiner_response('["drink"]')


def _anthropic_reply(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _openai_reply(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class TestAnthropicBackend:
    def test_request_shape_and_result(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json=_anthropic_reply(
                    '{"category_key": "drink", "menu_item_name": "Spritzer",'
                    ' "confidence": 0.9, "is_item": true}'
                ),
            )

        backend = AnthropicBackend(
            "claude-haiku-4-5", api_key="sk-test", transport=httpx.MockTransport(handler)
        )
        result = backend.refine(JPEG, CATEGORIES, MENU)

        (request,) = seen
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "sk-test"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["model"] == "claude-haiku-4-5"
        assert body["max_tokens"] == 300
        (message,) = body["messages"]
        assert message["role"] == "user"
        image_block, text_block = message["content"]
        assert image_block["type"] == "image"
        assert image_block["source"]["type"] == "base64"
        assert image_block["source"]["media_type"] == "image/jpeg"
        assert base64.b64decode(image_block["source"]["data"]) == JPEG
        assert text_block["type"] == "text"
        assert text_block["text"] == build_prompt(CATEGORIES, MENU)

        assert result.category_key == "drink"
        assert result.menu_item_name == "Spritzer"

    def test_transport_error_retries_once_then_raises(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("connection refused")

        backend = AnthropicBackend(
            "claude-haiku-4-5", api_key="sk-test", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(RefinerError, match="unreachable"):
            backend.refine(JPEG, CATEGORIES, MENU)
        assert attempts == 2  # one retry

    def test_transport_error_then_success(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ReadTimeout("timed out")
            return httpx.Response(200, json=_anthropic_reply('{"category_key": "main"}'))

        backend = AnthropicBackend(
            "claude-haiku-4-5", api_key="sk-test", transport=httpx.MockTransport(handler)
        )
        assert backend.refine(JPEG, CATEGORIES, []).category_key == "main"
        assert attempts == 2

    def test_http_error_raises_clear_message(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})

        backend = AnthropicBackend(
            "claude-haiku-4-5", api_key="bad", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(RefinerError, match="HTTP 401"):
            backend.refine(JPEG, CATEGORIES, [])

    def test_reply_without_text_block_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"content": []})

        backend = AnthropicBackend(
            "claude-haiku-4-5", api_key="sk-test", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(RefinerError, match="no text block"):
            backend.refine(JPEG, CATEGORIES, [])


class TestOpenAICompatBackend:
    def test_request_shape_and_result(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json=_openai_reply(
                    '```json\n{"category_key": "main", "menu_item_name": "wiener schnitzel",'
                    ' "confidence": 0.66, "is_item": true}\n```'
                ),
            )

        backend = OpenAICompatBackend(
            "qwen2.5vl",
            base_url="http://localhost:11434/v1",
            transport=httpx.MockTransport(handler),
        )
        result = backend.refine(JPEG, CATEGORIES, MENU)

        (request,) = seen
        assert str(request.url) == "http://localhost:11434/v1/chat/completions"
        assert "authorization" not in request.headers  # no key -> no bearer header
        body = json.loads(request.content)
        assert body["model"] == "qwen2.5vl"
        assert body["max_tokens"] == 300
        (message,) = body["messages"]
        assert message["role"] == "user"
        image_block, text_block = message["content"]
        assert image_block["type"] == "image_url"
        url = image_block["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == JPEG
        assert text_block == {"type": "text", "text": build_prompt(CATEGORIES, MENU)}

        assert result.category_key == "main"
        assert result.menu_item_name == "wiener schnitzel"
        assert result.confidence == pytest.approx(0.66)

    def test_bearer_header_when_api_key_set(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=_openai_reply('{"category_key": "drink"}'))

        backend = OpenAICompatBackend(
            "gpt-4o-mini",
            base_url="http://localhost:1234/v1/",  # trailing slash is normalized
            api_key="lm-studio",
            transport=httpx.MockTransport(handler),
        )
        backend.refine(JPEG, CATEGORIES, [])
        (request,) = seen
        assert str(request.url) == "http://localhost:1234/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer lm-studio"

    def test_missing_choices_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        backend = OpenAICompatBackend("qwen2.5vl", transport=httpx.MockTransport(handler))
        with pytest.raises(RefinerError, match="choices"):
            backend.refine(JPEG, CATEGORIES, [])

    def test_transport_error_counts_as_backend_failure_not_crash(self) -> None:
        """Transport failures surface as RefinerError (the engine counts them);
        nothing else escapes."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no server on :11434")

        backend = OpenAICompatBackend("qwen2.5vl", transport=httpx.MockTransport(handler))
        with pytest.raises(RefinerError):
            backend.refine(JPEG, CATEGORIES, [])
