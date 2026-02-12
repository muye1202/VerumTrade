from langchain_core.tools import tool
from typing import Annotated
import asyncio
from tradingagents.dataflows.interface import route_to_vendor

@tool
async def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """
    Retrieve technical indicators for a given ticker symbol.
    Uses the configured technical_indicators vendor.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        indicator (str): Technical indicator to get the analysis and report of
        curr_date (str): The current trading date you are trading on, YYYY-mm-dd
        look_back_days (int): How many days to look back, default is 30
    Returns:
        str: A formatted dataframe containing the technical indicators for the specified ticker symbol and indicator.

    Supported indicators:
    - 'close_50_sma': 50-day Simple Moving Average
    - 'close_200_sma': 200-day Simple Moving Average
    - 'close_10_ema': 10-day Exponential Moving Average
    - 'macd': Moving Average Convergence Divergence
    - 'macds': MACD Signal
    - 'macdh': MACD Histogram
    - 'rsi': Relative Strength Index
    - 'boll': Bollinger Bands Middle (20 SMA)
    - 'boll_ub': Bollinger Bands Upper
    - 'boll_lb': Bollinger Bands Lower
    - 'atr': Average True Range
    - 'vwma': Volume Weighted Moving Average
    - 'mfi': Money Flow Index
    """
    return await asyncio.to_thread(route_to_vendor, "get_indicators", symbol, indicator, curr_date, look_back_days)