import json
import unittest

from result_limiter import ResultLimitError, limit_result


def source(rows, columns=None):
    return json.dumps({"columns": ["value"] if columns is None else columns, "rows": rows})


class ResultLimiterTests(unittest.TestCase):
    def test_empty_and_exact_row_limit_are_complete(self):
        for rows in ([], [[1]], [[1], [2]]):
            with self.subTest(rows=rows):
                result = json.loads(limit_result(source(rows), max_rows=2))
                self.assertEqual(result, {"columns": ["value"], "rows": rows,
                                          "truncated": False, "row_count": len(rows)})

    def test_row_cap_preserves_order_and_marks_truncation(self):
        result = json.loads(limit_result(source([[3], [1], [2]]), max_rows=2))
        self.assertEqual(result, {"columns": ["value"], "rows": [[3], [1]],
                                  "truncated": True, "row_count": 2})

    def test_exact_utf8_byte_cap_includes_envelope_and_escaping(self):
        rows = [["雪\n\""], ["two"]]
        expected = '{"columns":["value"],"rows":[["雪\\n\\\""]],"truncated":true,"row_count":1}'
        budget = len(expected.encode("utf-8"))
        result = limit_result(source(rows), max_bytes=budget)
        self.assertEqual(result, expected)
        self.assertEqual(len(result.encode("utf-8")), budget)
        smaller = json.loads(limit_result(source(rows), max_bytes=budget - 1))
        self.assertEqual(smaller["rows"], [])
        self.assertTrue(smaller["truncated"])

    def test_exact_complete_payload_budget_does_not_truncate(self):
        expected = '{"columns":["value"],"rows":[[1]],"truncated":false,"row_count":1}'
        self.assertEqual(limit_result(source([[1]]), max_bytes=len(expected)), expected)

    def test_trace_id_is_inside_exact_byte_budget(self):
        expected = '{"columns":["value"],"request_id":"trace","rows":[[1]],"truncated":true,"row_count":1}'
        self.assertEqual(limit_result(source([[1], [2]]), request_id="trace", max_bytes=len(expected)), expected)
        smaller = json.loads(limit_result(source([[1], [2]]), request_id="trace", max_bytes=len(expected) - 1))
        self.assertEqual(smaller["rows"], [])
        self.assertEqual(smaller["request_id"], "trace")

    def test_oversized_first_row_does_not_return_partial_values(self):
        result = limit_result(source([["private-value" * 100]]), max_bytes=100)
        self.assertLessEqual(len(result.encode()), 100)
        self.assertEqual(json.loads(result)["rows"], [])
        self.assertTrue(json.loads(result)["truncated"])
        self.assertNotIn("private-value", result)

    def test_oversized_columns_fail_without_echoing_values(self):
        with self.assertRaises(ResultLimitError) as caught:
            limit_result(source([], columns=["private-column" * 100]), max_bytes=100)
        self.assertNotIn("private-column", str(caught.exception))

    def test_invalid_payloads_fail_closed(self):
        for value in ("not JSON", "[]", '{}', '{"columns":[],"rows":{}}',
                      '{"columns":"bad","rows":[]}', '{"columns":[],"rows":[1]}',
                      '{"columns":[],"rows":[[NaN]]}'):
            with self.subTest(value=value), self.assertRaises(ResultLimitError):
                limit_result(value)

    def test_default_limits_bound_large_results(self):
        many = json.loads(limit_result(source([[i] for i in range(1001)])))
        self.assertEqual(many["row_count"], 1000)
        self.assertTrue(many["truncated"])
        large = limit_result(source([["雪" * 2000]] * 1000))
        self.assertLessEqual(len(large.encode()), 1_048_576)
        self.assertTrue(json.loads(large)["truncated"])

    def test_deeply_nested_result_uses_safe_error(self):
        text = '{"columns":["value"],"rows":[' + '[' * 10000 + '1' + ']' * 10000 + ']}'
        with self.assertRaises(ResultLimitError):
            limit_result(text)


if __name__ == "__main__":
    unittest.main()
