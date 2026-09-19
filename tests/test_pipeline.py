import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import collect
import sync_cas
import title_filter
import update_metrics
from collect import CrossrefClient, article_from_item, classify_title, gather_for_journal, publication_date
from update_metrics import parse_metric


JOURNAL = {"id": "example", "url": "https://example.com"}


class PipelineTests(unittest.TestCase):
    def article(self, published_date=None):
        return {
            "id": "10.1000/example",
            "doi": "10.1000/example",
            "journal_id": "example",
            "title_en": "Example title",
            "title_zh": None,
            "translation_status": "pending",
            "translation_engine": None,
            "url": "https://doi.org/10.1000/example",
            "published_date": published_date,
            "date_precision": "date" if published_date else "month",
            "date_source": "published-online",
            "first_discovered_at": "2026-09-17T01:00:00+08:00",
            "last_seen_at": "2026-09-17T01:00:00+08:00",
            "content_type": "article",
            "crossref_type": "journal-article",
        }

    def prepare_collection_data(self, root):
        days = root / "days"
        days.mkdir()
        (root / "journals.json").write_text(
            json.dumps(
                {
                    "journals": [
                        {
                            "id": "example",
                            "name": "Example",
                            "url": "https://example.com",
                            "issns": ["1234-5678"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (root / "translations.json").write_text(json.dumps({"entries": {}}), encoding="utf-8")
        (root / "collection-status.json").write_text(
            json.dumps({"example": {"status": "ok"}}), encoding="utf-8"
        )
        return days

    def run_collection(self, root, items, target=date(2026, 9, 18)):
        days = root / "days"
        with patch.object(collect, "DATA_DIR", root), patch.object(
            collect, "DAYS_DIR", days
        ), patch.object(
            collect, "iter_day_files", side_effect=lambda: sorted(days.glob("????-??-??.json"))
        ), patch.object(
            collect, "gather_for_journal", return_value=(items, "1234-5678")
        ):
            collect.collect("daily", target)

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

    def test_translation_cache_keeps_only_titles_in_retained_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day_path = root / "days" / "2026-09-18.json"
            day_path.parent.mkdir()
            day_path.write_text(
                json.dumps({"articles": [{"title_en": "Retained day title"}]}),
                encoding="utf-8",
            )
            (root / "supplements.json").write_text(
                json.dumps(
                    {
                        "late_additions": [{"title_en": "Retained supplement title"}],
                        "date_pending": [],
                    }
                ),
                encoding="utf-8",
            )
            (root / "translations.json").write_text(
                json.dumps(
                    {
                        "entries": {
                            "day": {"source": "Retained day title"},
                            "supplement": {"source": "Retained supplement title"},
                            "expired": {"source": "Expired title"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(collect, "DATA_DIR", root), patch.object(
                collect, "iter_day_files", return_value=[day_path]
            ):
                removed = collect.prune_translation_cache()

            self.assertEqual(removed, 1)
            cache = json.loads((root / "translations.json").read_text(encoding="utf-8"))
            self.assertEqual(set(cache["entries"]), {"day", "supplement"})

    def test_sync_cas_preserves_verified_metric_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_metric = {
                "value": 9.9,
                "year": 2025,
                "status": "verified",
                "source_url": "https://example.com/metric",
                "checked_at": "2026-08-01T01:00:00+08:00",
            }
            (root / "journals.json").write_text(
                json.dumps(
                    {
                        "metrics_checked_at": "2026-08-01T02:00:00+08:00",
                        "journals": [
                            {"id": "nature-communications", "impact_factor": old_metric}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "categories": {
                    "顶刊": [
                        {
                            "name": "Nature Communications",
                            "url": "https://www.nature.com/ncomms/",
                            "if": 1.0,
                            "if_year": 2024,
                        }
                    ]
                }
            }
            with patch.object(sync_cas, "DATA_DIR", root), patch.object(
                sync_cas, "ensure_config", return_value=config
            ):
                sync_cas.main()

            payload = json.loads((root / "journals.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["journals"][0]["impact_factor"], old_metric)
            self.assertEqual(payload["metrics_checked_at"], "2026-08-01T02:00:00+08:00")

    def test_targeted_metric_check_does_not_advance_global_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "journals.json"
            path.write_text(
                json.dumps(
                    {
                        "metrics_checked_at": "2026-06-01T01:00:00+08:00",
                        "journals": [
                            {"id": "example", "name": "Example", "impact_factor": {}}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            def mark_checked(journal):
                journal["impact_factor"]["status"] = "verified"

            with patch.object(update_metrics, "DATA_DIR", root), patch.object(
                update_metrics, "update_journal", side_effect=mark_checked
            ):
                update_metrics.main("example")

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["metrics_checked_at"], "2026-06-01T01:00:00+08:00")

    def test_pending_article_moves_to_confirmed_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_collection_data(root)
            (root / "supplements.json").write_text(
                json.dumps({"late_additions": [], "date_pending": [self.article()]}),
                encoding="utf-8",
            )
            item = {
                "DOI": "10.1000/example",
                "title": ["Example title"],
                "published-online": {"date-parts": [[2026, 9, 18]]},
                "type": "journal-article",
            }
            self.run_collection(root, [item])

            supplements = json.loads((root / "supplements.json").read_text(encoding="utf-8"))
            day = json.loads((root / "days" / "2026-09-18.json").read_text(encoding="utf-8"))
            self.assertEqual(supplements["date_pending"], [])
            self.assertEqual([article["doi"] for article in day["articles"]], ["10.1000/example"])

    def test_article_date_change_removes_old_day_and_preserves_old_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            days = self.prepare_collection_data(root)
            old_path = days / "2026-09-17.json"
            old_path.write_text(
                json.dumps(
                    {
                        "date": "2026-09-17",
                        "articles": [self.article("2026-09-17")],
                        "journal_status": {"example": {"status": "historical"}},
                    }
                ),
                encoding="utf-8",
            )
            (root / "supplements.json").write_text(
                json.dumps({"late_additions": [], "date_pending": []}), encoding="utf-8"
            )
            item = {
                "DOI": "10.1000/example",
                "title": ["Example title"],
                "published-online": {"date-parts": [[2026, 9, 18]]},
                "type": "journal-article",
            }
            self.run_collection(root, [item])

            old = json.loads(old_path.read_text(encoding="utf-8"))
            new = json.loads((days / "2026-09-18.json").read_text(encoding="utf-8"))
            self.assertEqual(old["articles"], [])
            self.assertEqual(old["journal_status"]["example"]["status"], "historical")
            self.assertEqual(len(new["articles"]), 1)

    def test_selected_journal_filters_before_day_and_translation_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_collection_data(root)
            (root / "filter-config.json").write_text(
                json.dumps({"journal_ids": ["example"]}), encoding="utf-8"
            )
            item = {
                "DOI": "10.1000/medical",
                "title": ["Clinical treatment of a cardiac disease"],
                "published-online": {"date-parts": [[2026, 9, 18]]},
                "type": "journal-article",
            }
            config = title_filter.LLMConfig(
                "https://example.test/v1", "secret", "luna"
            )
            with patch.object(
                title_filter, "llm_is_configured", return_value=True
            ), patch.object(
                title_filter.TitleFilter, "_load_llm_config", return_value=config
            ), patch.object(
                title_filter,
                "classify_llm_batch",
                return_value=[{"category": "medicine", "confidence": 0.99}],
            ):
                self.run_collection(root, [item])

            day = json.loads((root / "days" / "2026-09-18.json").read_text(encoding="utf-8"))
            supplements = json.loads((root / "supplements.json").read_text(encoding="utf-8"))
            translations = json.loads((root / "translations.json").read_text(encoding="utf-8"))
            self.assertEqual(day["articles"], [])
            self.assertEqual(supplements["late_additions"], [])
            self.assertEqual(supplements["date_pending"], [])
            self.assertEqual(translations["entries"], {})
            self.assertEqual(day["journal_status"]["example"]["title_filter"]["excluded"], 1)
            excluded = json.loads(
                (root / "excluded-papers.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(excluded["entries"]), 1)
            record = next(iter(excluded["entries"].values()))
            self.assertEqual(record["doi"], "10.1000/medical")

    def test_unselected_journal_bypasses_title_classifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_collection_data(root)
            (root / "filter-config.json").write_text(
                json.dumps({"journal_ids": []}), encoding="utf-8"
            )
            item = {
                "DOI": "10.1000/medical",
                "title": ["Clinical treatment of a cardiac disease"],
                "published-online": {"date-parts": [[2026, 9, 18]]},
                "type": "journal-article",
            }
            with patch.object(title_filter, "classify_llm_batch") as classify:
                self.run_collection(root, [item])

            classify.assert_not_called()
            day = json.loads((root / "days" / "2026-09-18.json").read_text(encoding="utf-8"))
            self.assertEqual(len(day["articles"]), 1)


if __name__ == "__main__":
    unittest.main()
