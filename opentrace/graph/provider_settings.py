import os
from typing import Any, Callable, Dict, Mapping, Optional


_PROVIDER_ENV: Dict[str, Dict[str, Any]] = {
    "openai": {
        "api_keys": ("OPENAI_API_KEY",),
        "base_urls": ("OPENAI_BASE_URL",),
        "default_base_url": "https://api.openai.com/v1",
    },
    "azure-foundry": {
        "api_keys": ("AZURE_FOUNDRY_API_KEY",),
        "base_urls": ("AZURE_FOUNDRY_BASE_URL",),
        "default_base_url": None,
    },
    "anthropic": {
        "api_keys": ("ANTHROPIC_API_KEY",),
        "base_urls": ("ANTHROPIC_BASE_URL",),
        "default_base_url": None,
    },
    "google": {
        "api_keys": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "base_urls": (),
        "default_base_url": None,
    },
    "glm": {
        "api_keys": ("ZHIPUAI_API_KEY", "GLM_API_KEY", "OPENAI_API_KEY"),
        "base_urls": ("ZHIPUAI_BASE_URL", "GLM_BASE_URL"),
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    "deepseek": {
        "api_keys": ("DEEPSEEK_API_KEY",),
        "base_urls": ("DEEPSEEK_BASE_URL",),
        "default_base_url": "https://api.deepseek.com/v1",
    },
    "qwen3-cn": {
        "api_keys": ("DASHSCOPE_API_KEY",),
        "base_urls": ("DASHSCOPE_BASE_URL",),
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "openrouter": {
        "api_keys": ("OPENROUTER_API_KEY",),
        "base_urls": ("OPENROUTER_BASE_URL",),
        "default_base_url": "https://openrouter.ai/api/v1",
    },
    "ollama": {
        "api_keys": (),
        "base_urls": ("OLLAMA_BASE_URL",),
        "default_base_url": "http://localhost:11434/v1",
    },
}


def _clean(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _provider_key(provider: str) -> str:
    return (provider or "").strip().lower()


def azure_foundry_reasoning_mode(model_name: str) -> str:
    """Return Azure Foundry reasoning mode: effort, native, or none."""
    name = (model_name or "").strip().lower()
    if not name:
        return "none"
    if "deepseek" in name or "r1" in name:
        return "native"
    if name.startswith(("gpt-5", "o1", "o3", "o4")):
        return "effort"
    return "none"


def _request_settings_for_provider(
    provider: str,
    provider_settings: Any,
) -> Mapping[str, Any]:
    if not isinstance(provider_settings, Mapping):
        return {}

    provider_key = _provider_key(provider)
    for raw_key, settings in provider_settings.items():
        if _provider_key(str(raw_key)) != provider_key:
            continue
        if isinstance(settings, Mapping):
            return settings
        if hasattr(settings, "model_dump"):
            dumped = settings.model_dump(exclude_none=True)
            return dumped if isinstance(dumped, Mapping) else {}
        if hasattr(settings, "dict"):
            dumped = settings.dict(exclude_none=True)
            return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _first_env(
    names: tuple[str, ...],
    getenv: Callable[[str], Optional[str]],
) -> Optional[str]:
    for name in names:
        value = _clean(getenv(name))
        if value:
            return value
    return None


def resolve_llm_endpoint(
    provider: str,
    config: Mapping[str, Any],
    getenv: Callable[[str], Optional[str]] = os.getenv,
) -> Dict[str, Optional[str]]:
    """Resolve per-request LLM endpoint settings without mutating process env."""
    provider_key = _provider_key(provider)
    request_settings = _request_settings_for_provider(
        provider_key,
        config.get("provider_settings") if isinstance(config, Mapping) else None,
    )
    metadata = _PROVIDER_ENV.get(provider_key, _PROVIDER_ENV["openai"])

    request_api_key = _clean(request_settings.get("api_key"))
    request_base_url = _clean(request_settings.get("base_url"))
    legacy_backend_url = _clean(config.get("backend_url")) if isinstance(config, Mapping) else None

    api_key = request_api_key or _first_env(metadata["api_keys"], getenv)
    if provider_key == "ollama":
        api_key = api_key or "ollama"

    base_url = (
        request_base_url
        or legacy_backend_url
        or _first_env(metadata["base_urls"], getenv)
        or metadata["default_base_url"]
    )

    return {"api_key": api_key, "base_url": base_url}


def serialize_provider_settings(provider_settings: Any) -> Dict[str, Dict[str, str]]:
    """Convert request model settings into a config-safe dict."""
    if not isinstance(provider_settings, Mapping):
        return {}

    serialized: Dict[str, Dict[str, str]] = {}
    for raw_provider, raw_settings in provider_settings.items():
        provider = _provider_key(str(raw_provider))
        if not provider:
            continue
        if isinstance(raw_settings, Mapping):
            settings = raw_settings
        elif hasattr(raw_settings, "model_dump"):
            settings = raw_settings.model_dump(exclude_none=True)
        elif hasattr(raw_settings, "dict"):
            settings = raw_settings.dict(exclude_none=True)
        else:
            continue

        clean_settings = {
            key: value
            for key in ("api_key", "base_url")
            if (value := _clean(settings.get(key))) is not None
        }
        if clean_settings:
            serialized[provider] = clean_settings
    return serialized
