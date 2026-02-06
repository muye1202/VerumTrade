import json
import unittest

from tradingagents.dataflows.config import get_config, set_config


class TestAlphaVantageNewsCompact(unittest.TestCase):
    def setUp(self):
        self._orig_cfg = get_config()
        set_config({"news_max_items": 3})
        self._orig_req = None

    def tearDown(self):
        set_config(self._orig_cfg)
        if self._orig_req is not None:
            try:
                from tradingagents.dataflows import alpha_vantage_news as av_news
                av_news._make_api_request = self._orig_req
            except Exception:
                pass

    def test_news_sentiment_payload_is_compacted(self):
        try:
            from tradingagents.dataflows import alpha_vantage_news as av_news
        except Exception as e:
            self.skipTest(f"alpha_vantage_news dependencies unavailable: {e}")

        self._orig_req = av_news._make_api_request
        feed = []
        for i in range(10):
            feed.append(
                {
                    "title": f"title-{i}",
                    "source": "source-x",
                    "time_published": "20260206T120000",
                    "summary": "s" * 600,
                    "overall_sentiment_label": "Neutral",
                    "overall_sentiment_score": 0.01,
                    "url": f"https://example.com/{i}",
                }
            )
        payload = {"feed": feed}
        av_news._make_api_request = lambda *_args, **_kwargs: json.dumps(payload)

        out = av_news.get_news("MSFT", "2026-02-01", "2026-02-06")
        self.assertIn("MSFT", out)
        self.assertIn("Truncated to top 3 items", out)
        self.assertIn("sentiment:", out)
        self.assertNotIn("title-9", out)


if __name__ == "__main__":
    unittest.main()
