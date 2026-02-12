import pandas as pd
import yfinance as yf
from stockstats import wrap
from typing import Annotated
import os
from ...config import get_config, DATA_DIR


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
    ):
        # Get config and set up data directory path
        config = get_config()
        vendor = config["data_vendors"]["technical_indicators"]
        online = vendor != "local"

        df = None
        data = None

        if not online:
            try:
                data = pd.read_csv(
                    os.path.join(
                        DATA_DIR,
                        f"{symbol}-YFin-data-2015-01-01-2025-03-25.csv",
                    )
                )
                df = wrap(data)
            except FileNotFoundError:
                raise Exception("Stockstats fail: Yahoo Finance data not fetched yet!")
        else:
            # Get today's date as YYYY-mm-dd to add to cache
            today_date = pd.Timestamp.today()
            curr_date = pd.to_datetime(curr_date)

            end_date = today_date
            start_date = today_date - pd.DateOffset(years=15)
            start_date = start_date.strftime("%Y-%m-%d")
            end_date = end_date.strftime("%Y-%m-%d")

            # Get config and ensure cache directory exists
            os.makedirs(config["data_cache_dir"], exist_ok=True)

            cache_prefix = "YFin" if vendor != "alpaca" else "Alpaca"
            data_file = os.path.join(
                config["data_cache_dir"],
                f"{symbol}-{cache_prefix}-data-{start_date}-{end_date}.csv",
            )

            if os.path.exists(data_file):
                data = pd.read_csv(data_file)
                data["Date"] = pd.to_datetime(data["Date"])
            else:
                if vendor == "alpaca":
                    from ..alpaca.alpaca import AlpacaConnectionError, fetch_stock_bars_df_alpaca

                    try:
                        bars_df = fetch_stock_bars_df_alpaca(symbol, start_date, end_date)
                    except AlpacaConnectionError as e:
                        print(
                            f"WARNING: Alpaca indicator data unavailable ({e}); falling back to yfinance. "
                            "If you set APCA_API_BASE_URL for trading, also set APCA_API_DATA_URL=https://data.alpaca.markets for market data."
                        )
                        bars_df = None

                    if bars_df is None or getattr(bars_df, "empty", False):
                        data = yf.download(
                            symbol,
                            start=start_date,
                            end=end_date,
                            multi_level_index=False,
                            progress=False,
                            auto_adjust=True,
                        )
                        data = data.reset_index()
                    else:
                        data = bars_df.reset_index()
                        data["Date"] = pd.to_datetime(data["Date"])
                else:
                    data = yf.download(
                        symbol,
                        start=start_date,
                        end=end_date,
                        multi_level_index=False,
                        progress=False,
                        auto_adjust=True,
                    )
                    data = data.reset_index()

                data.to_csv(data_file, index=False)

            df = wrap(data)
            df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
            curr_date = curr_date.strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
