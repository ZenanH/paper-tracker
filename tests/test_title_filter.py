import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import title_filter
from translate import LLMConfig, TranslationError


class TitleFilterTests(unittest.TestCase):
    def test_custom_rules_change_cache_and_are_sent_to_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "filter-config.json"
            config_path.write_text(json.dumps({"prompt": "保留力学，过滤其他方向"}))
            first = title_filter.TitleFilter(root, date(2026, 10, 19))
            config_path.write_text(json.dumps({"prompt": "保留计算机科学"}))
            second = title_filter.TitleFilter(root, date(2026, 10, 19))
            self.assertNotEqual(first.cache_key("Same title"), second.cache_key("Same title"))
            config = LLMConfig("https://example.test/v1", "secret", "model")
            payload = title_filter.build_payload(config, [{"id": "p1", "title_en": "A title"}], True, first.prompt)
            self.assertIn(first.prompt, payload["messages"][1]["content"])
            self.assertIn("uncertain", payload["messages"][0]["content"])

    def test_custom_exclusion_threshold_and_biomechanics_protection(self):
        config = LLMConfig("https://example.test/v1", "secret", "model")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "filter-config.json").write_text(json.dumps({"prompt": "过滤所有医学研究"}))
            with patch.object(title_filter, "llm_is_configured", return_value=True), patch.object(
                title_filter.TitleFilter, "_load_llm_config", return_value=config
            ), patch.object(title_filter, "classify_llm_batch", return_value=[
                {"category": "exclude", "confidence": 0.90},
                {"category": "exclude", "confidence": 0.89},
                {"category": "uncertain", "confidence": 1},
            ]) as model:
                instance = title_filter.TitleFilter(root, date(2026, 10, 19))
                retained, excluded, stats = instance.classify_articles([
                    self.article("Clinical trial"), self.article("Another trial"),
                    self.article("Uncertain work"), self.article("Cell mechanics of tissues")
                ], "example")
            self.assertEqual([a["title_en"] for a in excluded], ["Clinical trial"])
            self.assertEqual(len(retained), 3)
            self.assertEqual(stats["biomechanics_kept"], 1)
            self.assertEqual(model.call_args.args[2], instance.prompt)

    def article(self, title):
        return {"id": title, "title_en": title, "journal_id": "example"}

    def test_biomechanics_exception_is_deterministic(self):
        titles = [
            "Biomechanical analysis of gait after injury",
            "Musculoskeletal mechanics during running",
            "Mechanobiology of load-bearing bone",
        ]
        self.assertTrue(all(title_filter.is_biomechanics_title(title) for title in titles))

        with tempfile.TemporaryDirectory() as directory:
            instance = title_filter.TitleFilter(Path(directory), date(2026, 9, 18))
            with patch.object(instance, "_load_llm_config") as load_config:
                retained, excluded, stats = instance.classify_articles(
                    [self.article(titles[0])], "example"
                )
        self.assertEqual(len(retained), 1)
        self.assertEqual(excluded, [])
        self.assertEqual(stats["biomechanics_kept"], 1)
        load_config.assert_not_called()

    def test_high_confidence_excluded_categories_are_removed(self):
        decisions = [
            {"category": "medicine", "confidence": 0.98},
            {"category": "biology", "confidence": 0.72},
            {"category": "uncertain", "confidence": 0.99},
            {"category": "keep", "confidence": 0.99},
        ]
        articles = [self.article(f"Title {index}") for index in range(4)]
        config = LLMConfig("https://example.test/v1", "secret", "luna")
        with tempfile.TemporaryDirectory() as directory, patch.object(
            title_filter, "llm_is_configured", return_value=True
        ), patch.object(
            title_filter.TitleFilter, "_load_llm_config", return_value=config
        ), patch.object(title_filter, "classify_llm_batch", return_value=decisions):
            instance = title_filter.TitleFilter(Path(directory), date(2026, 9, 18))
            retained, excluded, stats = instance.classify_articles(articles, "example")

        self.assertEqual([item["title_en"] for item in retained], ["Title 1", "Title 2", "Title 3"])
        self.assertEqual([item["title_en"] for item in excluded], ["Title 0"])
        self.assertEqual(excluded[0]["filter_category"], "medicine")
        self.assertEqual(stats["excluded"], 1)

    def test_cache_avoids_reclassifying_the_same_title(self):
        config = LLMConfig("https://example.test/v1", "secret", "luna")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(
                title_filter, "llm_is_configured", return_value=True
            ), patch.object(
                title_filter.TitleFilter, "_load_llm_config", return_value=config
            ), patch.object(
                title_filter,
                "classify_llm_batch",
                return_value=[{"category": "chemistry", "confidence": 0.95}],
            ) as classify:
                first = title_filter.TitleFilter(root, date(2026, 9, 18))
                first.classify_articles([self.article("Catalytic synthesis")], "example")
                second = title_filter.TitleFilter(root, date(2026, 9, 18))
                retained, excluded, stats = second.classify_articles(
                    [self.article("Catalytic synthesis")], "example"
                )

            self.assertEqual(retained, [])
            self.assertEqual(len(excluded), 1)
            self.assertEqual(stats["cached"], 1)
            self.assertEqual(classify.call_count, 1)
            cache = json.loads((root / "title-classifications.json").read_text(encoding="utf-8"))
            self.assertEqual(next(iter(cache["entries"].values()))["journal_ids"], ["example"])

    def test_api_failure_keeps_titles(self):
        config = LLMConfig("https://example.test/v1", "secret", "luna")
        with tempfile.TemporaryDirectory() as directory, patch.object(
            title_filter, "llm_is_configured", return_value=True
        ), patch.object(
            title_filter.TitleFilter, "_load_llm_config", return_value=config
        ), patch.object(
            title_filter, "classify_llm_batch", side_effect=TranslationError("offline")
        ):
            instance = title_filter.TitleFilter(Path(directory), date(2026, 9, 18))
            retained, excluded, stats = instance.classify_articles(
                [self.article("A clinical trial")], "example"
            )
        self.assertEqual(len(retained), 1)
        self.assertEqual(excluded, [])
        self.assertEqual(stats["failed_open"], 1)

    def test_unconfigured_model_skips_filtering_without_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            title_filter, "llm_is_configured", return_value=False
        ):
            instance = title_filter.TitleFilter(Path(directory), date(2026, 9, 18))
            retained, excluded, stats = instance.classify_articles(
                [self.article("A clinical trial")], "example"
            )
        self.assertEqual(len(retained), 1)
        self.assertEqual(excluded, [])
        self.assertEqual(stats["skipped_unconfigured"], 1)

    def test_payload_validation_requires_exact_ids_and_confidence_range(self):
        items = [{"id": "p0001", "title_en": "Example"}]
        valid = {
            "classifications": [
                {"id": "p0001", "category": "keep", "confidence": 0.8}
            ]
        }
        self.assertEqual(
            title_filter.validate_classification_payload(items, valid)[0]["category"],
            "keep",
        )
        with self.assertRaises(TranslationError):
            title_filter.validate_classification_payload(
                items,
                {
                    "classifications": [
                        {"id": "p0001", "category": "keep", "confidence": 1.2}
                    ]
                },
            )
        for invalid in [[], {}, None, True]:
            with self.subTest(category=invalid), self.assertRaises(TranslationError):
                title_filter.validate_classification_payload(items, {"classifications": [
                    {"id": "p0001", "category": invalid, "confidence": 1}
                ]})


if __name__ == "__main__":
    unittest.main()
