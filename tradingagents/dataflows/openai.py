import os
from openai import OpenAI
from .config import get_config


def _require_openai_web_tools() -> None:
    """OpenAI vendor methods in this file rely on OpenAI-only tooling (Responses API + web_search_preview)."""
    config = get_config()
    llm_provider = config.get("llm_provider", "").lower()
    backend_url = config.get("backend_url", "")
    if llm_provider != "openai" or not backend_url.startswith("https://api.openai.com/"):
        raise RuntimeError(
            "OpenAI news tools require llm_provider='openai' and backend_url='https://api.openai.com/v1'"
        )


def _get_openai_client() -> OpenAI:
    _require_openai_web_tools()
    config = get_config()
    api_key = None
    # On OpenAI, allow OPENAI_API_KEY default resolution.
    return OpenAI(base_url=config["backend_url"], api_key=api_key)


def get_stock_news_openai(query, start_date, end_date):
    config = get_config()
    client = _get_openai_client()

    response = client.responses.create(
        model=config["quick_think_llm"],
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"Can you search Social Media for {query} from {start_date} to {end_date}? Make sure you only get the data posted during that period.",
                    }
                ],
            }
        ],
        text={"format": {"type": "text"}},
        reasoning={},
        tools=[
            {
                "type": "web_search_preview",
                "user_location": {"type": "approximate"},
                "search_context_size": "low",
            }
        ],
        temperature=1,
        max_output_tokens=4096,
        top_p=1,
        store=True,
    )

    return response.output[1].content[0].text


def get_global_news_openai(curr_date, look_back_days=7, limit=5):
    config = get_config()
    client = _get_openai_client()

    response = client.responses.create(
        model=config["quick_think_llm"],
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"Can you search global or macroeconomics news from {look_back_days} days before {curr_date} to {curr_date} that would be informative for trading purposes? Make sure you only get the data posted during that period. Limit the results to {limit} articles.",
                    }
                ],
            }
        ],
        text={"format": {"type": "text"}},
        reasoning={},
        tools=[
            {
                "type": "web_search_preview",
                "user_location": {"type": "approximate"},
                "search_context_size": "low",
            }
        ],
        temperature=1,
        max_output_tokens=4096,
        top_p=1,
        store=True,
    )

    return response.output[1].content[0].text


def get_fundamentals_openai(ticker, curr_date):
    config = get_config()
    client = _get_openai_client()

    response = client.responses.create(
        model=config["quick_think_llm"],
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"Can you search Fundamental for discussions on {ticker} during of the month before {curr_date} to the month of {curr_date}. Make sure you only get the data posted during that period. List as a table, with PE/PS/Cash flow/ etc",
                    }
                ],
            }
        ],
        text={"format": {"type": "text"}},
        reasoning={},
        tools=[
            {
                "type": "web_search_preview",
                "user_location": {"type": "approximate"},
                "search_context_size": "low",
            }
        ],
        temperature=1,
        max_output_tokens=4096,
        top_p=1,
        store=True,
    )

    return response.output[1].content[0].text
