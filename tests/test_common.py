import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import article_key, normalize_doi, parse_date_parts, retention_start, subtract_months


class CommonTests(unittest.TestCase):
    def test_calendar_month_retention(self):
        self.assertEqual(subtract_months(date(2026, 5, 31), 3), date(2026, 2, 28))
        self.assertEqual(retention_start(date(2026, 9, 16)), date(2026, 6, 16))

    def test_doi_normalization_and_key(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1000/ABC"), "10.1000/abc")
        left = article_key({"doi": "doi:10.1000/ABC"})
        right = article_key({"doi": "https://doi.org/10.1000/abc"})
        self.assertEqual(left, right)

    def test_date_precision(self):
        self.assertEqual(parse_date_parts({"date-parts": [[2026, 9, 16]]}), ("2026-09-16", "date"))
        self.assertEqual(parse_date_parts({"date-parts": [[2026, 9]]}), (None, "month"))
        self.assertEqual(
            parse_date_parts({"date-parts": [[2026, 9, 16]], "date-time": "2026-09-15T16:30:00Z"}),
            ("2026-09-16", "datetime"),
        )
        self.assertEqual(parse_date_parts(None), (None, "missing"))


if __name__ == "__main__":
    unittest.main()
