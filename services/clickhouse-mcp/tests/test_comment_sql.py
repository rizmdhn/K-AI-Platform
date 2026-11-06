import asyncio
import json
import unittest
from unittest import mock

import comment_sql
from tool_errors import GatewayError

SCHEMA = {"database": "kai_demo", "table": "tiket", "engine": "MergeTree",
          "columns": [{"name": "ticket_id", "type": "UInt32", "comment": ""},
                      {"name": "pesan", "type": "String", "comment": "lama"}]}


def suggest(**kwargs):
    async def fake_schema(database, table):
        return json.dumps(SCHEMA)
    with mock.patch.object(comment_sql, "get_table_schema", fake_schema):
        return json.loads(asyncio.run(comment_sql.suggest_comment_sql("kai_demo", "tiket", **kwargs)))


class CommentSqlTests(unittest.TestCase):
    def test_formats_escaped_statements_and_flags_replaced_comments(self):
        result = suggest(table_comment="Tiket pelanggan", column_comments={"pesan": "Isi pesan; it's \\ teks\nbebas"})
        self.assertEqual(result["statements"][0], "ALTER TABLE `kai_demo`.`tiket` MODIFY COMMENT 'Tiket pelanggan';")
        self.assertEqual(result["statements"][1], "ALTER TABLE `kai_demo`.`tiket` COMMENT COLUMN `pesan` 'Isi pesan; it\\'s \\\\ teks bebas';")
        self.assertEqual(result["replaces_existing_column_comments"], ["pesan"])

    def test_unknown_columns_empty_or_oversized_descriptions_are_refused(self):
        for kwargs in ({"column_comments": {"tidak_ada": "x"}}, {}, {"table_comment": "x" * 501},
                       {"column_comments": {"pesan": "   "}}):
            with self.subTest(kwargs=str(kwargs)[:40]), self.assertRaises(GatewayError):
                suggest(**kwargs)

    def test_identifier_with_backtick_cannot_break_out(self):
        self.assertEqual(comment_sql._identifier("a`; DROP"), "`a\\`; DROP`")


if __name__ == "__main__":
    unittest.main()
