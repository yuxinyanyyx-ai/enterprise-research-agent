from __future__ import annotations

import json
from typing import Any


ALLOWED_MEMORY_KEYS = frozenset(
    {"language", "response_style", "output_format", "salutation"}
)
SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "raw_payload",
        "conversation",
        "messages",
        "transcript",
    }
)
MAX_CONTENT_BYTES = 4_096
MAX_CONTENT_DEPTH = 4


def normalize_memory_key(memory_key: str) -> str:
    if not isinstance(memory_key, str):
        raise ValueError("memory_key must be a string")
    normalized = memory_key.strip().lower()
    if normalized not in ALLOWED_MEMORY_KEYS:
        raise ValueError(
            "memory_key must be one of: " + ", ".join(sorted(ALLOWED_MEMORY_KEYS))
        )
    return normalized


def validate_memory_content(content: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(content, dict) or not content:
        raise ValueError("content must be a non-empty object")

    def walk(value: Any, depth: int) -> None:
        if depth > MAX_CONTENT_DEPTH:
            raise ValueError("content nesting is too deep")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ValueError("content object keys must be strings")
                if key.strip().lower() in SENSITIVE_FIELDS:
                    raise ValueError(f"content contains forbidden field: {key}")
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)
        elif not isinstance(value, (str, int, float, bool)) and value is not None:
            raise ValueError("content contains an unsupported value")

    walk(content, 0)
    try:
        encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("content must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError("content is too large")
    return content
