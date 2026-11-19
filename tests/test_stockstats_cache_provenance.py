import pandas as pd


def test_alpaca_indicator_fallback_writes_yfinance_cache_name(monkeypatch, tmp_path):
    import verumtrade.dataflows.vendors.yfinance.stockstats_utils as module

    monkeypatch.setattr(
        module,
        "get_config",
        lambda: {
            "data_vendors": {"technical_indicators": "alpaca"},
            "data_cache_dir": str(tmp_path),
        },
    )

    def fake_download(*args, **kwargs):
        return pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-28", "2026-05-29", "2026-06-01"]),
                "Open": [10.0, 11.0, 12.0],
                "High": [11.0, 12.0, 13.0],
                "Low": [9.0, 10.0, 11.0],
                "Close": [10.0, 11.0, 12.0],
                "Volume": [1000, 1100, 1200],
            }
        ).set_index("Date")

    class FakeYf:
        @staticmethod
        def download(*args, **kwargs):
            return fake_download(*args, **kwargs)

    monkeypatch.setattr(module, "yf", FakeYf)
    monkeypatch.setattr(
        "verumtrade.dataflows.vendors.alpaca.alpaca.fetch_stock_bars_df_alpaca",
        lambda *args, **kwargs: None,
    )

    value = module.StockstatsUtils.get_stock_stats("SNDK", "close_10_ema", "2026-06-01")

    assert value != "N/A: Not a trading day (weekend or holiday)"
    assert (tmp_path / "SNDK-YFin-data-2011-06-01-2026-06-01.csv").exists()
    assert not (tmp_path / "SNDK-Alpaca-data-2011-06-01-2026-06-01.csv").exists()
