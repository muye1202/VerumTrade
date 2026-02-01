import unittest


from tradingagents.graph.openai_compat import normalize_tool_arguments


class TestNormalizeOpenAIToolArguments(unittest.TestCase):
    def test_leaves_valid_object_json_untouched(self):
        self.assertEqual(normalize_tool_arguments('{"a": 1}'), '{"a": 1}')

    def test_unwraps_double_encoded_json_object(self):
        self.assertEqual(
            normalize_tool_arguments('"{\\"symbol\\": \\"GOOGL\\", \\"n\\": 1}"'),
            '{"symbol": "GOOGL", "n": 1}',
        )

    def test_does_not_unwrap_non_object_string_literal(self):
        self.assertEqual(normalize_tool_arguments('"hello"'), '"hello"')


if __name__ == "__main__":
    unittest.main()
