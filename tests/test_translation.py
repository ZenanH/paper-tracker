import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import translate


class TranslationTests(unittest.TestCase):
    def test_llm_configuration_and_endpoint(self):
        with patch.dict(
            os.environ,
            {
                "LLM_BASE_URL": "https://example.test/v1/",
                "LLM_API_KEY": "secret",
                "LLM_MODEL": "academic-model",
            },
            clear=True,
        ):
            config = translate.load_llm_config()
        self.assertEqual(config.endpoint, "https://example.test/v1/chat/completions")
        self.assertEqual(config.model, "academic-model")

    def test_llm_configuration_requires_all_values(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                translate.load_llm_config()

    def test_translation_payload_is_restored_to_input_order(self):
        items = [
            {"id": "p0001", "title_en": "First"},
            {"id": "p0002", "title_en": "Second"},
        ]
        payload = {
            "translations": [
                {"id": "p0002", "title_zh": "第二"},
                {"id": "p0001", "title_zh": "第一"},
            ]
        }
        self.assertEqual(translate.validate_translation_payload(items, payload), ["第一", "第二"])

    def test_translation_payload_rejects_missing_or_duplicate_ids(self):
        items = [
            {"id": "p0001", "title_en": "First"},
            {"id": "p0002", "title_en": "Second"},
        ]
        with self.assertRaises(translate.TranslationError):
            translate.validate_translation_payload(
                items, {"translations": [{"id": "p0001", "title_zh": "第一"}]}
            )
        with self.assertRaises(translate.TranslationError):
            translate.validate_translation_payload(
                items,
                {
                    "translations": [
                        {"id": "p0001", "title_zh": "第一"},
                        {"id": "p0001", "title_zh": "重复"},
                    ]
                },
            )

    def test_daily_skips_old_engine_but_backfill_retranslates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day_path = root / "2026-09-16.json"
            day_path.write_text(
                json.dumps(
                    {
                        "articles": [
                            {
                                "id": "old",
                                "title_en": "Already translated",
                                "translation_status": "translated",
                                "translation_engine": "old-engine",
                            },
                            {
                                "id": "new",
                                "title_en": "Needs translation",
                                "translation_status": "pending",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "supplements.json").write_text(
                json.dumps({"late_additions": [], "date_pending": []}), encoding="utf-8"
            )
            with patch.object(translate, "DATA_DIR", root), patch.object(
                translate, "iter_day_files", return_value=[day_path]
            ):
                daily_ids = [article["id"] for _, article in translate.pending_articles("new-engine")]
                backfill_ids = [
                    article["id"]
                    for _, article in translate.pending_articles(
                        "new-engine", retranslate_existing=True
                    )
                ]
            self.assertEqual(daily_ids, ["new"])
            self.assertEqual(backfill_ids, ["old", "new"])

    def test_duplicate_title_records_are_grouped_into_one_request_key(self):
        records = [
            (Path("day.json"), {"id": "day", "title_en": "Same title"}),
            (Path("supplements.json"), {"id": "supplement", "title_en": "Same title"}),
            (Path("other.json"), {"id": "other", "title_en": "Other title"}),
        ]
        grouped = translate.group_records_by_title(records, "openai-compatible:model")
        self.assertEqual(len(grouped), 2)
        self.assertEqual([len(items) for _, items in grouped], [2, 1])


if __name__ == "__main__":
    unittest.main()
