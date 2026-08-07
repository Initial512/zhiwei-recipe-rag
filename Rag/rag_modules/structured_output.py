"""Safe parsing helpers for structured LLM responses."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any


class StructuredOutputParseError(ValueError):
    """Raised when an LLM response cannot be interpreted as a JSON object."""

    def __init__(self, reason: str, response_length: int):
        super().__init__(f"{reason} (response_length={response_length})")
        self.reason = reason
        self.response_length = response_length


def parse_json_object(content: object) -> tuple[dict[str, Any], str]:
    """Parse a JSON object from raw text, a Markdown fence, or surrounding prose."""
    text = str(content or "").strip()
    if not text:
        raise StructuredOutputParseError("empty_response", 0)

    try:
        return _as_object(json.loads(text), len(text)), "raw_json"
    except json.JSONDecodeError:
        pass

    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL):
        candidate = match.group(1).strip()
        try:
            return _as_object(json.loads(candidate), len(text)), "markdown_fence"
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return _as_object(value, len(text)), "embedded_json"

    raise StructuredOutputParseError("no_json_object", len(text))


def string_list(value: object) -> list[str]:
    """Normalize an LLM JSON array to non-empty strings."""
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    """Convert numeric model output while keeping query bounds safe."""
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _as_object(value: object, response_length: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StructuredOutputParseError("json_not_object", response_length)
    return value
