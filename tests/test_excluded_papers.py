import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import excluded_papers
from common import article_key


class ExcludedPaperTests(unittest.TestCase):
    def article(self, published_date="2026-09-18"):
        return {
            "id": "10.1000/filtered",
            "doi": "10.1000/filtered",
            "journal_id": "example",
            "title_en": "Clinical treatment study",
            "title_zh": None,
            "translation_status": "pending",
            "translation_engine": None,
            "url": "https://doi.org/10.1000/filtered",
            "published_date": published_date,
            "date_precision": "date" if published_date else "month",
            "date_source": "published-online",
            "first_discovered_at": "2026-09-19T01:00:00+08:00",
            "last_seen_at": "2026-09-19T01:00:00+08:00",
            "content_type": "article",
            "crossref_type": "journal-article",
            "filter_category": "medicine",
            "filter_confidence": 0.98,
            "filter_engine": "openai-compatible:luna",
            "filter_rule_version": "test",
            "excluded_at": "2026-09-19T01:00:00+08:00",
        }

    def prepare(self, root):
        (root / "days").mkdir()
        (root / "journals.json").write_text(
            json.dumps({"journals": [{"id": "example"}]}), encoding="utf-8"
        )
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "retention": {"start": "2026-06-18", "end": "2026-09-18"},
                    "available_dates": [],
                }
            ),
            encoding="utf-8",
        )

    def test_filtered_record_is_restored_to_original_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            record = self.article()
            excluded_papers.save_excluded_records(root, [record], date(2026, 9, 18))
            filter_id = article_key(record)

            result = excluded_papers.restore_excluded_records(root, [filter_id])

            day = json.loads((root / "days" / "2026-09-18.json").read_text(encoding="utf-8"))
            excluded = json.loads((root / "excluded-papers.json").read_text(encoding="utf-8"))
            self.assertEqual(result["restored"], [filter_id])
            self.assertEqual(day["articles"][0]["doi"], "10.1000/filtered")
            self.assertNotIn("filter_category", day["articles"][0])
            self.assertEqual(excluded["entries"], {})

    def test_undated_restored_record_uses_current_month_not_stale_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            record = self.article(None)
            excluded_papers.save_excluded_records(root, [record], date(2026, 9, 18))
            with patch.object(
                excluded_papers,
                "now_beijing",
                return_value=datetime(2026, 10, 21, 13, tzinfo=ZoneInfo("Asia/Shanghai")),
            ):
                excluded_papers.restore_excluded_records(root, [article_key(record)])

            article = json.loads((root / "days" / "2026-10-01.json").read_text())["articles"][0]
            self.assertEqual(article["published_date"], "2026-10-01")
            self.assertTrue(article["date_inferred"])


if __name__ == "__main__":
    unittest.main()
