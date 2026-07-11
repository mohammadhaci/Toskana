"""Vision-LLM backends for the AI Event Refiner (no model training involved).

After a crossing event is counted, the :class:`~toskana.refiner_engine.
RefinerEngine` sends the event's snapshot (cropped to the item's bounding
box) to a vision LLM which verifies/corrects the category and optionally
names the exact menu item. Two backends implement the same
:class:`RefinerBackend` protocol:

* :class:`AnthropicBackend` — the Claude API (``POST /v1/messages``).
* :class:`OpenAICompatBackend` — any OpenAI-compatible server
  (``POST {base_url}/chat/completions``): Ollama, LM Studio, vLLM, …

Both are plain ``httpx`` calls (60 s timeout, one retry on transport
errors) so the same request/response shapes work against local servers and
are trivially testable with ``httpx.MockTransport``. Backend failures raise
:class:`RefinerError`; the engine catches, counts and logs them — a broken
or unreachable LLM must never take the counting app down.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60.0
# Generous ceiling so reasoning models (which spend tokens "thinking" before
# they answer) still have room to emit the final JSON. Non-reasoning models
# stop as soon as the short JSON is done, so a high ceiling costs nothing.
MAX_TOKENS = 2048


class RefinerError(Exception):
    """A backend call failed (network, HTTP error, or unparseable reply)."""


@dataclass(frozen=True)
class RefinerCategory:
    """One category offered to the LLM: stable ``key`` + display name."""

    key: str
    name: str


@dataclass(frozen=True)
class RefinerResult:
    """Parsed LLM verdict for one event snapshot."""

    category_key: str | None
    menu_item_name: str | None
    confidence: float
    is_item: bool
    raw_response: str


class RefinerBackend(Protocol):
    """Common interface of the Anthropic and OpenAI-compatible backends."""

    #: short id used in ``refiner_note`` / status ("anthropic", "openai_compatible").
    provider: str
    model: str

    def refine(
        self,
        image_jpeg: bytes,
        categories: list[RefinerCategory],
        menu_items: list[str],
    ) -> RefinerResult:
        """Classify one cropped item image; raises :class:`RefinerError`."""
        ...


# -- prompt & parsing (shared by both backends) -----------------------------------


def build_prompt(categories: list[RefinerCategory], menu_items: list[str]) -> str:
    """The text instruction sent alongside the image."""
    category_lines = "\n".join(f"- {c.key}: {c.name}" for c in categories)
    prompt = (
        "You verify items counted by a restaurant camera. The image shows one "
        "item (cropped) that just crossed the pass-through counter.\n"
        f"Categories:\n{category_lines}\n"
    )
    if menu_items:
        menu_lines = "\n".join(f"- {name}" for name in menu_items)
        prompt += f"Menu (optional, pick only if clearly recognizable):\n{menu_lines}\n"
    prompt += (
        "Reply ONLY with JSON: "
        '{"category_key": <one of the category keys or null>, '
        '"menu_item_name": <exact menu name or null>, '
        '"confidence": <0-1>, "is_item": <true|false>}. '
        "is_item=false if the image shows no food/drink item."
    )
    return prompt


def parse_refiner_response(text: str) -> RefinerResult:
    """Parse the LLM reply as JSON, tolerating code fences and extra prose.

    Raises :class:`RefinerError` when no JSON object can be extracted.
    """
    candidate = text.strip()
    # Strip Markdown code fences (```json ... ```).
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```\s*$", candidate, flags=re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        # Dirty reply: extract the first balanced {...} block.
        data = _first_json_object(candidate)
        if data is None:
            raise RefinerError(f"refiner reply is not JSON: {text[:200]!r}") from None
    if not isinstance(data, dict):
        raise RefinerError(f"refiner reply is not a JSON object: {text[:200]!r}")

    category_key = data.get("category_key")
    menu_item_name = data.get("menu_item_name")
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return RefinerResult(
        category_key=str(category_key) if category_key not in (None, "") else None,
        menu_item_name=str(menu_item_name) if menu_item_name not in (None, "") else None,
        confidence=max(0.0, min(1.0, confidence)),
        is_item=bool(data.get("is_item", True)),
        raw_response=text,
    )


def _first_json_object(text: str) -> Any | None:
    """The first balanced ``{...}`` block in ``text`` parsed as JSON, or None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break  # try the next '{'
        start = text.find("{", start + 1)
    return None


# -- backends --------------------------------------------------------------------


class _HttpBackend:
    """Shared httpx plumbing: one client, 60 s timeout, single retry."""

    provider = "http"

    def __init__(
        self,
        model: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _post(self, url: str, *, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        """POST with a single retry on transport errors; JSON on success."""
        last_error: httpx.TransportError | None = None
        for attempt in (1, 2):
            try:
                response = self._client.post(url, headers=headers, json=body)
                break
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning("refiner %s request failed (attempt %d): %s", url, attempt, exc)
        else:
            raise RefinerError(f"refiner backend unreachable at {url}: {last_error}")
        if response.status_code != 200:
            raise RefinerError(
                f"refiner backend returned HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            return response.json()  # type: ignore[no-any-return]
        except (json.JSONDecodeError, ValueError) as exc:
            raise RefinerError(f"refiner backend returned non-JSON body: {exc}") from exc


class AnthropicBackend(_HttpBackend):
    """Claude API vision backend (``POST https://api.anthropic.com/v1/messages``).

    Uses raw HTTP (not the SDK) so both refiner backends share one tiny
    httpx code path and the identical MockTransport test harness. The API
    key comes from ``config.refiner_api_key`` or the ``ANTHROPIC_API_KEY``
    environment variable (resolved by the caller / :func:`build_backend`).
    """

    provider = "anthropic"
    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(
        self,
        model: str = "claude-haiku-4-5",
        *,
        api_key: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(model, timeout_s=timeout_s, transport=transport)
        self._api_key = api_key

    def refine(
        self,
        image_jpeg: bytes,
        categories: list[RefinerCategory],
        menu_items: list[str],
    ) -> RefinerResult:
        body = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64.b64encode(image_jpeg).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": build_prompt(categories, menu_items)},
                    ],
                }
            ],
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }
        data = self._post(self.API_URL, headers=headers, body=body)
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text:
            raise RefinerError(f"anthropic reply contains no text block: {str(data)[:300]}")
        return parse_refiner_response(text)


class OpenAICompatBackend(_HttpBackend):
    """OpenAI-compatible chat/completions backend (Ollama, LM Studio, vLLM …)."""

    provider = "openai_compatible"

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://localhost:11434/v1",
        api_key: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(model, timeout_s=timeout_s, transport=transport)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def refine(
        self,
        image_jpeg: bytes,
        categories: list[RefinerCategory],
        menu_items: list[str],
    ) -> RefinerResult:
        data_url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode("ascii")
        body = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": build_prompt(categories, menu_items)},
                    ],
                }
            ],
        }
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        data = self._post(f"{self._base_url}/chat/completions", headers=headers, body=body)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise RefinerError(
                f"openai-compatible reply has no choices[0].message: {str(data)[:300]}"
            ) from None
        content = message.get("content") if isinstance(message, dict) else None
        if not (isinstance(content, str) and content.strip()):
            # Reasoning models (e.g. Gemma / Qwen via LM Studio) can leave the
            # standard `content` empty and put their output — including the
            # JSON verdict — under `reasoning_content` / `reasoning`.
            for key in ("reasoning_content", "reasoning"):
                alt = message.get(key) if isinstance(message, dict) else None
                if isinstance(alt, str) and alt.strip():
                    content = alt
                    break
        if not isinstance(content, str) or not content.strip():
            raise RefinerError(
                "openai-compatible reply content is empty — if this is a reasoning "
                "model, give it a higher token budget or turn reasoning off"
            )
        return parse_refiner_response(content)
