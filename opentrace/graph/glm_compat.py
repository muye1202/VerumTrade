import json
from typing import Any, Dict

from .openai_compat import normalize_tool_arguments


def _coerce_content_to_text(content: Any) -> str:
    """Best-effort normalization for providers that return non-string `message.content`.

    GLM endpoints are often OpenAI-compatible but may still return structured content blocks
    or dict payloads instead of a plain string.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        # Common patterns: {"text": "..."} or {"content": "..."}.
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), str):
            return content["content"]
        # Nested message-like dict.
        if isinstance(content.get("message"), dict):
            return _coerce_content_to_text(content.get("message"))
        try:
            return json.dumps(content, ensure_ascii=False)
        except Exception:
            return str(content)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                # Anthropic-style blocks: {"type":"text","text":"..."}
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                    continue
                # OpenAI-style content parts: {"type":"text","text":"..."} / {"type":"input_text",...}
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                    continue
                if isinstance(item.get("content"), str):
                    parts.append(item["content"])
                    continue
            parts.append(str(item))
        return " ".join(p for p in parts if p)
    return str(content)


def sanitize_glm_chat_completion_response_dict(response_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Mutate a GLM ChatCompletion-like dict into something LangChain can parse reliably."""
    for choice in response_dict.get("choices", []) or []:
        if not isinstance(choice, dict):
            continue

        msg = choice.get("message")
        if not isinstance(msg, dict):
            continue

        # Vendor-specific fields that can break older parsers.
        msg.pop("reasoning_content", None)

        # Ensure message.content is a string (LangChain expects this for chat models).
        if "content" in msg and not isinstance(msg.get("content"), str):
            msg["content"] = _coerce_content_to_text(msg.get("content"))

        # Convert legacy `function_call` into modern `tool_calls` if present.
        if msg.get("function_call") and not msg.get("tool_calls"):
            fc = msg.get("function_call")
            if isinstance(fc, dict):
                name = fc.get("name")
                arguments = fc.get("arguments")
                if isinstance(arguments, (dict, list)):
                    try:
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    except Exception:
                        arguments = str(arguments)
                if isinstance(arguments, str):
                    arguments = normalize_tool_arguments(arguments)
                if name:
                    msg["tool_calls"] = [
                        {
                            "id": fc.get("id") or "call_0",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments or ""},
                        }
                    ]

        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue

        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                continue
            if "arguments" not in fn:
                continue

            args = fn.get("arguments")
            # Some providers return parsed objects; LangChain expects JSON string.
            if isinstance(args, (dict, list)):
                try:
                    fn["arguments"] = json.dumps(args, ensure_ascii=False)
                except Exception:
                    fn["arguments"] = str(args)
                continue
            fn["arguments"] = normalize_tool_arguments(args)

    return response_dict

