import unittest
from unittest.mock import patch

import cli.analysis_utils  # Ensure submodule is importable for patch()


class TestCliMultiTickerAnalyzeDispatch(unittest.TestCase):
    @patch("cli.analysis_utils.run_single_ticker_analysis")
    @patch("cli.analysis_utils.get_user_selections")
    def test_run_analysis_dispatches_over_tickers(self, mock_get_user_selections, mock_run_one):
        mock_get_user_selections.return_value = {
            "analysis_mode": "single",
            "tickers": ["AAPL", "MSFT"],
            "ticker": "AAPL",
        }
        mock_run_one.return_value = {}

        from cli.analysis_utils import run_analysis

        run_analysis()

        self.assertEqual(mock_run_one.call_count, 2)
        first_call = mock_run_one.call_args_list[0]
        second_call = mock_run_one.call_args_list[1]

        self.assertEqual(first_call.args[0]["ticker"], "AAPL")
        self.assertEqual(second_call.args[0]["ticker"], "MSFT")


if __name__ == "__main__":
    unittest.main()
