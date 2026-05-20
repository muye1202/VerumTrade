import questionary
from typing import List

from rich.console import Console

from cli.models import AnalystType

console = Console()

ANALYST_ORDER = [
    ("Catalyst / Event-Risk Analyst", AnalystType.CATALYST),
    ("Market Analyst", AnalystType.MARKET),
    ("Social Media Analyst", AnalystType.SOCIAL),
    ("News Analyst", AnalystType.NEWS),
    ("Fundamentals Analyst", AnalystType.FUNDAMENTALS),
]


def get_ticker() -> str:
    """Prompt the user to enter a ticker symbol."""
    ticker = questionary.text(
        "Enter the ticker symbol to analyze:",
        validate=lambda x: len(x.strip()) > 0 or "Please enter a valid ticker symbol.",
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if not ticker:
        console.print("\n[red]No ticker symbol provided. Exiting...[/red]")
        exit(1)

    return ticker.strip().upper()


def get_analysis_date() -> str:
    """Prompt the user to enter a date in YYYY-MM-DD format."""
    import re
    from datetime import datetime

    def validate_date(date_str: str) -> bool:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return False
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    date = questionary.text(
        "Enter the analysis date (YYYY-MM-DD):",
        validate=lambda x: validate_date(x.strip())
        or "Please enter a valid date in YYYY-MM-DD format.",
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if not date:
        console.print("\n[red]No date provided. Exiting...[/red]")
        exit(1)

    return date.strip()


def select_analysts() -> List[AnalystType]:
    """Select analysts using an interactive checkbox."""
    choices = questionary.checkbox(
        "Select Your [Analysts Team]:",
        choices=[
            questionary.Choice(display, value=value) for display, value in ANALYST_ORDER
        ],
        instruction="\n- Press Space to select/unselect analysts\n- Press 'a' to select/unselect all\n- Press Enter when done",
        validate=lambda x: len(x) > 0 or "You must select at least one analyst.",
        style=questionary.Style(
            [
                ("checkbox-selected", "fg:green"),
                ("selected", "fg:green noinherit"),
                ("highlighted", "noinherit"),
                ("pointer", "noinherit"),
            ]
        ),
    ).ask()

    if not choices:
        console.print("\n[red]No analysts selected. Exiting...[/red]")
        exit(1)

    return choices


def select_research_depth() -> int:
    """Select research depth using an interactive selection."""

    # Define research depth options with their corresponding values
    DEPTH_OPTIONS = [
        ("Shallow - Quick research, few debate and strategy discussion rounds", 1),
        ("Medium - Middle ground, moderate debate rounds and strategy discussion", 3),
        ("Deep - Comprehensive research, in depth debate and strategy discussion", 5),
    ]

    choice = questionary.select(
        "Select Your [Research Depth]:",
        choices=[
            questionary.Choice(display, value=value) for display, value in DEPTH_OPTIONS
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:yellow noinherit"),
                ("highlighted", "fg:yellow noinherit"),
                ("pointer", "fg:yellow noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]No research depth selected. Exiting...[/red]")
        exit(1)

    return choice


def select_time_horizon() -> str:
    """
    Select a target holding period / time horizon for this run.

    Returns the normalized key (ASCII) stored in agent state, e.g. "1-2 months".
    """
    # Put the default choice first to avoid relying on questionary's optional `default=` API.
    choices = [
        questionary.Choice("1–2 weeks (5–10 trading days) (Default)", value="1-2 weeks"),
        questionary.Choice("2–4 weeks (10–20 trading days)", value="2-4 weeks"),
        questionary.Choice("1–2 months (20–42 trading days)", value="1-2 months"),
        questionary.Choice("2–3 months (42–63 trading days)", value="2-3 months"),
    ]

    choice = questionary.select(
        "Select [Holding Period / Time Horizon]:",
        choices=choices,
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:yellow noinherit"),
                ("highlighted", "fg:yellow noinherit"),
                ("pointer", "fg:yellow noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]No holding period selected. Exiting...[/red]")
        exit(1)

    return str(choice)


def select_shallow_thinking_agent(provider) -> str:
    """Select shallow thinking llm engine using an interactive selection."""

    # Define shallow thinking llm engine options with their corresponding model names
    SHALLOW_AGENT_OPTIONS = {
        # "glm": [
        #     ("GLM-4.7-Flash - Fast, cost-effective", "glm-4.7-flash"),
        # ],
        "qwen3-cn": [
            ("Qwen3.6-Flash", "qwen3.6-flash"),
        ],
        # "deepseek": [
        #     ("DeepSeek Chat", "deepseek-chat"),
        # ],
        "anthropic": [
            ("Claude 4.6 Sonnet", "claude-sonnet-4-6"),
            ("Claude 4.5 Haiku", "claude-haiku-4-5-20251001"),
        ],
        # "openrouter": [
        #     ("Step 3.5 Flash (free)", "stepfun/step-3.5-flash:free"),
        #     ("Qwen3-235B-A22B Thinking (2507)", "qwen/qwen3-235b-a22b-thinking-2507"),
        #     ("GLM-4.5-Air (free)", "z-ai/glm-4.5-air:free"),
        # ],
    }

    choice = questionary.select(
        "Select Your [Quick-Thinking LLM Engine]:",
        choices=[
            questionary.Choice(display, value=value)
            for display, value in SHALLOW_AGENT_OPTIONS[provider.lower()]
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print(
            "\n[red]No shallow thinking llm engine selected. Exiting...[/red]"
        )
        exit(1)

    return choice


def select_deep_thinking_agent(provider) -> str:
    """Select deep thinking llm engine using an interactive selection."""

    # Define deep thinking llm engine options with their corresponding model names
    DEEP_AGENT_OPTIONS = {
        "glm": [
            ("GLM-4.7-Flash - Fast, cost-effective", "glm-4.7-flash"),
        ],
        "qwen3-cn": [
            ("Qwen3.5-Plus", "qwen3.5-plus"),
            ("Qwen3.6-Plus", "qwen3.6-plus"),
        ],
        "deepseek": [
            ("DeepSeek Reasoner", "deepseek-reasoner"),
        ],
        "anthropic": [
            ("Claude 4.6 Opus", "claude-opus-4-6"),
            ("Claude 4.6 Sonnet", "claude-sonnet-4-6"),
        ],
        # "openrouter": [
        #     ("Step 3.5 Flash (free)", "stepfun/step-3.5-flash:free"),
        #     ("Qwen3-235B-A22B Thinking (2507)", "qwen/qwen3-235b-a22b-thinking-2507"),
        #     ("GLM-4.5-Air (free)", "z-ai/glm-4.5-air:free"),
        # ],
    }

    choice = questionary.select(
        "Select Your [Deep-Thinking LLM Engine]:",
        choices=[
            questionary.Choice(display, value=value)
            for display, value in DEEP_AGENT_OPTIONS[provider.lower()]
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]No deep thinking llm engine selected. Exiting...[/red]")
        exit(1)

    return choice

def select_llm_provider() -> tuple[str, str]:
    """Select the OpenAI api url using interactive selection."""
    # Define OpenAI api options with their corresponding endpoints
    BASE_URLS = [
        ("Google", "openai", "http://192.168.123.81:8045/v1"),   # NOTE: Hankun's Antigravity Tool
        ("Qwen3-CN (DashScope)", "qwen3-cn", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("DeepSeek", "deepseek", "https://api.deepseek.com/v1"),
        ("GLM (ZhipuAI)", "glm", "https://open.bigmodel.cn/api/paas/v4"),
        ("Anthropic", "anthropic", "http://ai.tachira.cn/api"),
        ("OpenRouter", "openrouter", "https://openrouter.ai/api/v1"),
    ]

    choice = questionary.select(
        "Select your LLM Provider:",
        choices=[
            questionary.Choice(display, value=(provider, url))
            for display, provider, url in BASE_URLS
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()
    
    if choice is None:
        console.print("\n[red]no OpenAI backend selected. Exiting...[/red]")
        exit(1)
    
    provider, url = choice
    print(f"You selected: {provider}\tURL: {url}")
    
    return provider, url
