import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import validate_data


class ValidateDataTests(unittest.TestCase):
    def excluded_article(self, published_date=None):
        return {
            "id": "10.1000/unknown-date",
            "doi": "10.1000/unknown-date",
            "journal_id": "example",
            "title_en": "A month precision record",
            "url": "https://doi.org/10.1000/unknown-date",
            "published_date": published_date,
            "date_precision": "date" if published_date else "month",
            "first_discovered_at": "2026-09-22T16:39:47+08:00",
            "filter_category": "medicine",
            "filter_confidence": 0.99,
            "filter_engine": "openai-compatible:luna",
            "filter_rule_version": "2026-09-19.1",
            "excluded_at": "2026-09-22T16:42:59+08:00",
        }

    def prepare(self, root, article):
        (root / "days").mkdir()
        (root / "journals.json").write_text(
            json.dumps({"journals": [{"id": "example"}]}), encoding="utf-8"
        )
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "default_date": "2026-09-21",
                    "retention": {"start": "2026-06-21", "end": "2026-09-21"},
                    "available_dates": [],
                    "late_addition_count": 0,
                    "date_pending_count": 0,
                }
            ),
            encoding="utf-8",
        )
        (root / "supplements.json").write_text(
            json.dumps({"late_additions": [], "date_pending": []}), encoding="utf-8"
        )
        (root / "excluded-papers.json").write_text(
            json.dumps({"entries": {"doi:10.1000/unknown-date": article}}),
            encoding="utf-8",
        )

    def run_validator(self, root):
        with patch.object(validate_data, "DATA_DIR", root), patch.object(
            validate_data, "iter_day_files", return_value=[]
        ):
            return validate_data.main()

    def test_missing_publication_date_is_not_compared_with_yesterday(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, self.excluded_article())

            self.assertEqual(self.run_validator(root), 0)

    def test_published_exclusion_must_stay_in_retention_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, self.excluded_article("2026-06-20"))

            with self.assertRaises(AssertionError):
                self.run_validator(root)


if __name__ == "__main__":
    unittest.main()
