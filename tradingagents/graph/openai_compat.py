import json
from typing import Any


def normalize_tool_arguments(arguments: Any) -> Any:
    """Normalize provider quirks where `function.arguments` is double JSON-encoded.

    Some OpenAI-compatible providers return `arguments` as a JSON string literal of a JSON object
    (e.g. `"{"k": 1}"`), which LangChain parses into a Python `str` instead of a `dict`.
    """
    if not isinstance(arguments, str):
        return arguments

    original = arguments.strip()
    if not original:
        return original

    current = original
    # Unwrap up to 2 layers: '"{...}"' -> '{...}' -> dict
    for _ in range(2):
        if not current or current[0] not in ('"', "{", "["):
            return current
        try:
            parsed = json.loads(current)
        except Exception:
            return current

        if isinstance(parsed, str):
            inner = parsed.strip()
            if inner and inner[0] in ("{", "["):
                current = inner
                continue
            return original

        # Parsed to an object: return the JSON text representation, not the object,
        # since LangChain expects the raw OpenAI field to be a JSON string.
        return current

    return current


def sanitize_openai_compatible_response_dict(response_dict: dict) -> dict:
    """Mutate an OpenAI(-compatible) response dict into something LangChain can parse."""
    for choice in response_dict.get("choices", []) or []:
        msg = choice.get("message")
        if not isinstance(msg, dict):
            continue

        msg.pop("reasoning_content", None)

        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                continue
            if "arguments" in fn:
                fn["arguments"] = normalize_tool_arguments(fn.get("arguments"))
    return response_dict

