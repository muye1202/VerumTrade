import sys
from pathlib import Path

import run
from verumtrade.graph.provider_settings import resolve_llm_endpoint


def test_parse_env_file_ignores_comments_and_blank_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "OPENAI_API_KEY=sk-test",
                "ANTHROPIC_API_KEY=",
                "export GOOGLE_API_KEY=gemini-test",
                "QUOTED='value with spaces'",
            ]
        ),
        encoding="utf-8",
    )

    values = run.parse_env_file(env_file)

    assert values["OPENAI_API_KEY"] == "sk-test"
    assert values["GOOGLE_API_KEY"] == "gemini-test"
    assert values["QUOTED"] == "value with spaces"
    assert "ANTHROPIC_API_KEY" not in values


def test_resolves_azure_foundry_endpoint_from_env():
    env = {
        "AZURE_FOUNDRY_API_KEY": "azure-key",
        "AZURE_FOUNDRY_BASE_URL": "https://example.services.ai.azure.com/models",
    }

    endpoint = resolve_llm_endpoint("azure-foundry", {}, getenv=env.get)

    assert endpoint == {
        "api_key": "azure-key",
        "base_url": "https://example.services.ai.azure.com/models",
    }


def test_resolves_azure_foundry_request_settings_before_env():
    env = {
        "AZURE_FOUNDRY_API_KEY": "env-key",
        "AZURE_FOUNDRY_BASE_URL": "https://env.example/models",
    }
    config = {
        "provider_settings": {
            "azure-foundry": {
                "api_key": "request-key",
                "base_url": "https://request.example/models",
            }
        }
    }

    endpoint = resolve_llm_endpoint("azure-foundry", config, getenv=env.get)

    assert endpoint == {
        "api_key": "request-key",
        "base_url": "https://request.example/models",
    }


def test_preflight_blocks_missing_frontend_dependencies(tmp_path, monkeypatch):
    repo = tmp_path
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    (repo / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")

    monkeypatch.setattr(run, "find_python_executable", lambda repo_root: sys.executable)
    monkeypatch.setattr(run, "command_version", lambda command, args: "v20.0.0")
    monkeypatch.setattr(run, "is_port_open", lambda host, port: False)
    monkeypatch.setattr(run, "can_import_backend", lambda python_exec: (True, ""))

    result = run.run_preflight(repo)

    assert not result.ok
    assert any("frontend dependencies are not installed" in issue.message.lower() for issue in result.errors)
    assert any("npm ci" in issue.fix for issue in result.errors)


def test_preflight_warns_when_env_or_llm_key_is_missing(tmp_path, monkeypatch):
    repo = tmp_path
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
    (frontend / "node_modules").mkdir()
    (repo / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    monkeypatch.setattr(run, "find_python_executable", lambda repo_root: sys.executable)
    monkeypatch.setattr(
        run,
        "command_version",
        lambda command, args: "v20.0.0" if command == "node" else "9.9.9",
    )
    monkeypatch.setattr(run, "is_port_open", lambda host, port: False)
    monkeypatch.setattr(run, "can_import_backend", lambda python_exec: (True, ""))

    result = run.run_preflight(repo)

    assert result.ok
    assert any(".env was not found" in issue.message for issue in result.warnings)
    assert any("No LLM provider API key was found" in issue.message for issue in result.warnings)


def test_preflight_accepts_azure_foundry_api_key(tmp_path, monkeypatch):
    repo = tmp_path
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
    (frontend / "node_modules").mkdir()
    (repo / ".env").write_text("AZURE_FOUNDRY_API_KEY=azure-test\n", encoding="utf-8")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    monkeypatch.setattr(run, "find_python_executable", lambda repo_root: sys.executable)
    monkeypatch.setattr(
        run,
        "command_version",
        lambda command, args: "v20.0.0" if command == "node" else "9.9.9",
    )
    monkeypatch.setattr(run, "is_port_open", lambda host, port: False)
    monkeypatch.setattr(run, "can_import_backend", lambda python_exec: (True, ""))

    result = run.run_preflight(repo)

    assert result.ok
    assert not any("No LLM provider API key was found" in issue.message for issue in result.warnings)


def test_analysis_request_accepts_azure_foundry_thinking_fields():
    from api.schemas import AnalysisRequest

    req = AnalysisRequest(
        ticker="AAPL",
        analysis_date="2026-06-02",
        llm_provider="azure-foundry",
        azure_foundry_enable_thinking=True,
        azure_foundry_reasoning_effort="high",
    )

    assert req.azure_foundry_enable_thinking is True
    assert req.azure_foundry_reasoning_effort == "high"


def test_analysis_request_rejects_azure_foundry_minimal_reasoning_effort():
    import pytest
    from pydantic import ValidationError

    from api.schemas import AnalysisRequest

    with pytest.raises(ValidationError):
        AnalysisRequest(
            ticker="AAPL",
            analysis_date="2026-06-02",
            llm_provider="azure-foundry",
            azure_foundry_enable_thinking=True,
            azure_foundry_reasoning_effort="minimal",
        )


def test_azure_foundry_reasoning_mode_distinguishes_gpt_and_deepseek():
    from verumtrade.graph.provider_settings import azure_foundry_reasoning_mode

    assert azure_foundry_reasoning_mode("gpt-5-mini") == "effort"
    assert azure_foundry_reasoning_mode("DeepSeek-R1") == "native"
    assert azure_foundry_reasoning_mode("custom-chat-model") == "none"
