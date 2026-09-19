import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from collect import CrossrefClient, article_from_item, classify_title, gather_for_journal, publication_date
from update_metrics import parse_metric


JOURNAL = {"id": "example", "url": "https://example.com"}


class PipelineTests(unittest.TestCase):
    def test_online_date_wins(self):
        item = {
            "published-online": {"date-parts": [[2026, 9, 15]]},
            "published-print": {"date-parts": [[2027, 1, 1]]},
        }
        self.assertEqual(publication_date(item), ("2026-09-15", "date", "published-online"))

    def test_article_cleaning_and_direct_link(self):
        item = {
            "DOI": "10.1000/Test",
            "title": ["A <i>useful</i> &amp; safe title"],
            "published": {"date-parts": [[2026, 9, 16]]},
            "resource": {"primary": {"URL": "https://publisher.example/article"}},
            "type": "journal-article",
        }
        article = article_from_item(item, JOURNAL, "2026-09-17T01:00:00+08:00")
        self.assertEqual(article["title_en"], "A useful & safe title")
        self.assertEqual(article["url"], "https://publisher.example/article")
        self.assertEqual(article["doi"], "10.1000/test")

    def test_non_research_classification(self):
        self.assertEqual(classify_title("Correction: A useful paper"), "correction")
        self.assertEqual(classify_title("A useful paper"), "article")

    def test_metric_requires_label_and_preserves_year(self):
        self.assertEqual(parse_metric("Journal Impact Factor (2025): 7.6"), (7.6, 2025))
        self.assertEqual(parse_metric("CiteScore: 22.1"), None)

    def test_daily_primary_query_is_exactly_one_publication_day(self):
        calls = []

        class FakeClient:
            def works(self, issn, filters):
                calls.append(filters)
                return []

        gather_for_journal(
            FakeClient(), {"issns": ["1234-5678"]}, "daily", __import__("datetime").date(2026, 9, 16)
        )
        self.assertIn("from-pub-date:2026-09-15", calls[0])
        self.assertIn("until-pub-date:2026-09-16", calls[0])
        self.assertNotIn("from-index-date", calls[0])

    def test_initial_daily_run_skips_supplement_query(self):
        calls = []

        class FakeClient:
            def works(self, issn, filters):
                calls.append(filters)
                return []

        gather_for_journal(
            FakeClient(),
            {"issns": ["1234-5678"]},
            "daily",
            __import__("datetime").date(2026, 9, 16),
            include_supplements=False,
        )
        self.assertEqual(len(calls), 1)

    def test_multiple_issns_are_merged(self):
        class FakeClient:
            def works(self, issn, filters):
                return [{"DOI": f"10.1000/{issn}"}]

        items, used = gather_for_journal(
            FakeClient(),
            {"issns": ["1234-5678", "8765-4321"]},
            "backfill",
            __import__("datetime").date(2026, 9, 16),
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(used, "1234-5678,8765-4321")


if __name__ == "__main__":
    unittest.main()
