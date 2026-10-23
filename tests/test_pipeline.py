import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import collect
import sync_cas
import title_filter
import update_metrics
from collect import CrossrefClient, article_from_item, classify_title, gather_for_journal, publication_date
from update_metrics import parse_metric


JOURNAL = {"id": "example", "url": "https://example.com"}


class PipelineTests(unittest.TestCase):
    def test_metrics_progress_counts_real_checks_and_keeps_old_values_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journals = [{"id": str(index), "name": f"Journal {index}", "url": "https://example.com", "impact_factor": {"value": 4.2, "year": 2024}} for index in range(4)]
            path = root / "journals.json"
            path.write_text(json.dumps({"journals": journals}))
            progress_path = root / "metrics-progress.json"
            events = []
            responses = iter(["Journal Impact Factor: 5.1 (2025)", ConnectionResetError("Disconnected"), "No annual metric", "Journal Impact Factor: 5.8"])

            def fetch(url):
                progress = json.loads(progress_path.read_text())
                self.assertEqual(progress["completed"], len(events))
                self.assertEqual(progress["current_journal"], journals[len(events)]["name"])
                events.append(progress)
                response = next(responses)
                if isinstance(response, Exception):
                    raise response
                return response

            with patch.dict(os.environ, {"PAPER_TRACKER_METRICS_PROGRESS": str(progress_path), "PAPER_TRACKER_METRICS_TOKEN": "metrics-run"}), patch.object(update_metrics, "DATA_DIR", root), patch.object(update_metrics, "fetch", side_effect=fetch):
                update_metrics.main()
            self.assertEqual(len(events), 4)
            self.assertEqual(json.loads(progress_path.read_text()), {"token": "metrics-run", "completed": 4, "total": 4, "failed": 1, "unconfirmed": 2, "current_journal": None})
            saved = json.loads(path.read_text())["journals"]
            self.assertEqual(saved[0]["impact_factor"]["value"], 5.1)
            for journal in saved[1:]:
                self.assertEqual((journal["impact_factor"]["value"], journal["impact_factor"]["year"]), (4.2, 2024))

    def test_metrics_progress_handles_no_followed_journals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "journals.json").write_text(json.dumps({"journals": []}))
            progress_path = root / "metrics-progress.json"
            with patch.dict(os.environ, {"PAPER_TRACKER_METRICS_PROGRESS": str(progress_path)}), patch.object(update_metrics, "DATA_DIR", root), patch.object(update_metrics, "fetch") as fetch:
                update_metrics.main()
                fetch.assert_not_called()
            progress = json.loads(progress_path.read_text())
            self.assertEqual((progress["completed"], progress["total"]), (0, 0))

    def test_progress_counts_journals_in_order_and_includes_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_collection_data(root)
            journals = [{"id": key, "name": key, "issns": ["1234-5678"]} for key in ("first", "second", "third")]
            (root / "journals.json").write_text(json.dumps({"journals": journals}))
            progress_path = root / "collection-progress.json"
            events = []
            real_report = collect.report_progress

            def record(*args):
                real_report(*args)
                events.append(json.loads(progress_path.read_text()))

            def gather(client, journal, mode, target):
                self.assertEqual(events[-1]["current_journal"], journal["name"])
                self.assertEqual(events[-1]["completed"], journals.index(journal))
                if journal["id"] == "second":
                    raise RuntimeError("Offline")
                return [], "1234-5678"

            with patch.dict(os.environ, {"PAPER_TRACKER_COLLECTION_PROGRESS": str(progress_path), "PAPER_TRACKER_COLLECTION_TOKEN": "this-run"}), patch.object(collect, "DATA_DIR", root), patch.object(collect, "DAYS_DIR", root / "days"), patch.object(collect, "iter_day_files", side_effect=lambda: sorted((root / "days").glob("????-??-??.json"))), patch.object(collect, "gather_for_journal", side_effect=gather), patch.object(collect, "report_progress", side_effect=record):
                collect.collect("daily", date(2026, 9, 18))
            self.assertEqual(events[0]["completed"], 0)
            self.assertEqual([event["completed"] for event in events if event["current_journal"] is None], [0, 1, 2, 3])
            self.assertEqual(events[-1], {"token": "this-run", "completed": 3, "total": 3, "failed": 1, "current_journal": None})

    def test_crossref_pagination_stops_on_short_page_without_truncating_full_page(self):
        client = CrossrefClient(delay=0)
        pages = [{"items": [{"DOI": str(i)} for i in range(500)], "next-cursor": "page-two"},
                 {"items": [{"DOI": "last"}], "next-cursor": "unused"}]
        with patch.object(client, "get", side_effect=pages) as get:
            records = client.works("1234-5678", ["type:journal-article"])
        self.assertEqual(len(records), 501)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[1].args[1]["cursor"], "page-two")
        self.assertEqual(get.call_args_list[1].args[1]["filter"], "type:journal-article")
        with patch.object(client, "get", return_value={"items": [{"DOI": "one"}], "next-cursor": "unused"}) as get:
            self.assertEqual(len(client.works("1234-5678", [])), 1)
            get.assert_called_once()

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

    def run_collection(self, root, items, target=date(2026, 9, 18), mode="daily"):
        days = root / "days"
        with patch.object(collect, "DATA_DIR", root), patch.object(
            collect, "DAYS_DIR", days
        ), patch.object(
            collect, "iter_day_files", side_effect=lambda: sorted(days.glob("????-??-??.json"))
        ), patch.object(
            collect, "gather_for_journal", return_value=(items, "1234-5678")
        ), patch.object(
            collect, "iso_now", return_value=f"{target.isoformat()}T13:00:00+08:00"
        ), patch.object(
            collect, "now_beijing", return_value=datetime.combine(target, datetime.min.time(), ZoneInfo("Asia/Shanghai"))
        ):
            collect.collect(mode, target)

    def test_initial_backfill_assigns_current_month_start_to_partial_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_collection_data(root)
            (root / "collection-status.json").write_text('{}')
            item = {"DOI": "10.1000/month", "title": ["Real title with a month-only date"], "published": {"date-parts": [[2026, 9]]}, "type": "journal-article"}
            self.run_collection(root, [item], mode="backfill")
            article = json.loads((root / "days" / "2026-09-01.json").read_text())["articles"][0]
            self.assertEqual(article["published_date"], "2026-09-01")
            self.assertTrue(article["date_inferred"])
            self.assertEqual(article["date_precision"], "month")
            self.assertEqual(article["raw_publication_date"], item["published"])

    def test_online_date_wins(self):
        item = {
            "published-online": {"date-parts": [[2026, 9, 15]]},
            "published-print": {"date-parts": [[2027, 1, 1]]},
        }
        self.assertEqual(publication_date(item), ("2026-09-15", "date", "published-online"))

    def test_partial_online_date_is_not_replaced_by_future_print_issue(self):
        item = {"title": ["Online-first paper"], "published-online": {"date-parts": [[2026, 9]]}, "published-print": {"date-parts": [[2027, 1, 12]]}}
        self.assertEqual(publication_date(item), (None, "month", "published-online"))
        self.assertEqual(article_from_item(item, JOURNAL, "2026-09-18T13:00:00+08:00")["published_date"], "2026-09-01")

    def test_unknown_date_uses_beijing_current_month_not_source_month(self):
        for value in ({"date-parts": [[2020, 5]]}, {"date-parts": [[2020]]}, None):
            item = {"DOI": "10.1000/unknown", "title": ["Unknown date"], "published": value}
            article = article_from_item(item, JOURNAL, "2026-09-30T17:00:00Z")
            self.assertEqual(article["published_date"], "2026-10-01")
            self.assertTrue(article["date_inferred"])
            self.assertEqual(article["raw_publication_date"], value)

    def test_old_partial_source_period_is_excluded_from_current_window(self):
        cutoff = date(2026, 10, 1)
        old_month = article_from_item({"title": ["Old month"], "published": {"date-parts": [[1984, 12]]}}, JOURNAL, "2026-10-23T10:00:00+08:00")
        old_year = article_from_item({"title": ["Old year"], "published": {"date-parts": [[2025]]}}, JOURNAL, "2026-10-23T10:00:00+08:00")
        recent_month = article_from_item({"title": ["Recent month"], "published": {"date-parts": [[2026, 10]]}}, JOURNAL, "2026-10-23T10:00:00+08:00")
        missing = article_from_item({"title": ["Missing date"]}, JOURNAL, "2026-10-23T10:00:00+08:00")
        self.assertTrue(collect.source_period_before_window(old_month, cutoff))
        self.assertTrue(collect.source_period_before_window(old_year, cutoff))
        self.assertFalse(collect.source_period_before_window(recent_month, cutoff))
        self.assertFalse(collect.source_period_before_window(missing, cutoff))

    def test_collection_removes_old_partial_index_records_but_keeps_recent_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            days = self.prepare_collection_data(root)
            old = self.article("2026-10-01")
            old.update({"id": "10.1000/old-index", "doi": "10.1000/old-index",
                        "date_precision": "month", "date_source": "published",
                        "raw_publication_date": {"date-parts": [[1984, 12]]},
                        "date_inferred": True, "date_inference": "current-month-start"})
            (days / "2026-10-01.json").write_text(json.dumps({"articles": [old]}))
            items = [
                {"DOI": "10.1000/old-index", "title": [old["title_en"]],
                 "published": {"date-parts": [[1984, 12]]}, "type": "journal-article"},
                {"DOI": "10.1000/recent", "title": ["Recent month-only paper"],
                 "published": {"date-parts": [[2026, 10]]}, "type": "journal-article"},
            ]
            self.run_collection(root, items, target=date(2026, 10, 23))
            saved = json.loads((days / "2026-10-01.json").read_text())["articles"]
            self.assertEqual([article["id"] for article in saved], ["10.1000/recent"])

    def test_purge_old_partial_records_removes_legacy_inferred_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            days = self.prepare_collection_data(root)
            old = self.article("2026-10-01")
            old.update({"date_precision": "month", "raw_publication_date": {"date-parts": [[1984, 12]]}})
            recent = self.article("2026-10-01")
            recent.update({"id": "10.1000/recent", "doi": "10.1000/recent", "date_precision": "month", "raw_publication_date": {"date-parts": [[2026, 10]]}})
            (days / "2026-10-01.json").write_text(json.dumps({"articles": [old, recent]}))
            with patch.object(collect, "DAYS_DIR", days), patch.object(
                collect, "iter_day_files", return_value=[days / "2026-10-01.json"]
            ):
                self.assertEqual(collect.purge_old_partial_records(date(2026, 10, 1)), 1)
            saved = json.loads((days / "2026-10-01.json").read_text())["articles"]
            self.assertEqual([a["id"] for a in saved], ["10.1000/recent"])

    def test_inferred_date_stays_stable_then_moves_to_confirmed_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            days = self.prepare_collection_data(root)
            item = {"DOI": "10.1000/unknown", "title": ["Unknown date"]}
            self.run_collection(root, [item])
            self.run_collection(root, [item], target=date(2026, 10, 18))
            self.assertFalse((days / "2026-10-01.json").exists())
            self.assertFalse((days / "2026-09-01.json").exists())
            item["published-online"] = {"date-parts": [[2026, 9, 16]]}
            self.run_collection(root, [item], target=date(2026, 10, 18))
            self.assertFalse((days / "2026-09-16.json").exists())

    def test_legacy_migration_preserves_translation_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            days = self.prepare_collection_data(root)
            record = {**self.article(), "title_zh": "Saved translation", "translation_status": "translated"}
            (root / "supplements.json").write_text(json.dumps({"date_pending": [record], "late_additions": []}))
            self.run_collection(root, [])
            article = json.loads((days / "2026-09-01.json").read_text())["articles"][0]
            self.assertEqual(article["id"], record["id"])
            self.assertEqual(article["title_zh"], record["title_zh"])
            legacy = json.loads((root / "supplements.json").read_text(encoding="utf-8"))
            self.assertEqual(legacy["late_additions"], [])
            self.assertEqual(legacy["date_pending"], [])
            self.run_collection(root, [])
            self.assertEqual(len(json.loads((days / "2026-09-01.json").read_text())["articles"]), 1)

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

    def test_metric_reads_split_html_and_excludes_five_year_values(self):
        self.assertEqual(parse_metric('<dt>Journal Impact Factor</dt><dd>4.5 (2025)</dd><dt>5-year Journal Impact Factor</dt><dd>4.1 (2025)</dd>'), (4.5, 2025))
        self.assertIsNone(parse_metric('5-year Journal Impact Factor: 8.2 (2025)'))
        self.assertEqual(parse_metric('Journal Impact Factor: 4.5 Copyright 2026'), (4.5, None))
        self.assertIsNone(parse_metric('Journal Impact Factor: 4.5 (2025) Journal Impact Factor: 9.1 (2025)'))
        self.assertEqual(parse_metric('4.5 Impact Factor'), (4.5, None))

    def test_nature_metrics_use_dedicated_page_and_do_not_downgrade_year(self):
        journal = {"url": "https://www.nature.com/ncomms/", "impact_factor": {"value": 18.1, "year": 2025}}
        with patch.object(update_metrics, "fetch", return_value="Journal Impact Factor: 15.7 (2024)") as fetch:
            update_metrics.update_journal(journal)
        fetch.assert_called_once_with("https://www.nature.com/ncomms/journal-impact")
        self.assertEqual(journal["impact_factor"]["year"], 2025)
        self.assertEqual(journal["impact_factor"]["value"], 18.1)
        self.assertEqual(journal["impact_factor"]["status"], "outdated_source")

    def test_periodic_query_covers_full_window_and_indexes_without_date_filter(self):
        calls = []

        class FakeClient:
            def works(self, issn, filters):
                calls.append(filters)
                return []

        gather_for_journal(
            FakeClient(), {"issns": ["1234-5678"]}, "daily", __import__("datetime").date(2026, 9, 16)
        )
        self.assertIn("from-pub-date:2026-06-15", calls[0])
        self.assertIn("until-pub-date:2026-09-16", calls[0])
        self.assertNotIn("from-index-date", calls[0])
        self.assertIn("from-index-date:2026-06-16", calls[1])
        self.assertFalse(any("pub-date" in value for value in calls[1]))

    def test_initial_daily_run_also_finds_undated_indexed_records(self):
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
        )
        self.assertEqual(len(calls), 2)

    def test_index_watermark_uses_last_success_and_overlaps_one_day(self):
        calls = []
        class Client:
            def works(self, issn, filters):
                calls.append(filters)
                return []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "collection-status.json").write_text(json.dumps({"example": {"status": "failed", "checked_at": "2026-09-18T13:00:00+08:00", "last_success_at": "2026-09-16T13:00:00+08:00"}}))
            with patch.object(collect, "DATA_DIR", root):
                gather_for_journal(Client(), {"id": "example", "issns": ["1234-5678"]}, "daily", date(2026, 9, 19))
        self.assertIn("from-index-date:2026-09-15", calls[1])

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

            self.assertEqual(removed, 2)
            cache = json.loads((root / "translations.json").read_text(encoding="utf-8"))
            self.assertEqual(set(cache["entries"]), {"day"})

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
            translations = json.loads((root / "translations.json").read_text(encoding="utf-8"))
            self.assertEqual(day["articles"], [])
            self.assertEqual(translations["entries"], {})
            self.assertEqual(day["journal_status"]["example"]["title_filter"]["excluded"], 1)
            excluded = json.loads(
                (root / "excluded-papers.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(excluded["entries"]), 1)
            record = next(iter(excluded["entries"].values()))
            self.assertEqual(record["doi"], "10.1000/medical")

    def test_filter_skips_out_of_window_and_nonresearch_index_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_collection_data(root)
            (root / "filter-config.json").write_text(json.dumps({"journal_ids": ["example"]}))
            items = [
                {"DOI": f"10.1000/{key}", "title": [title],
                 "published-online": {"date-parts": [parts]}, "type": "journal-article"}
                for key, title, parts in [
                    ("old", "Old indexed clinical study", [2020, 1, 1]),
                    ("future", "Future clinical study", [2027, 1, 1]),
                    ("editorial", "Editorial: clinical research", [2026, 9, 18]),
                    ("current", "Current engineering study", [2026, 9, 18]),
                    ("undated", "Undated engineering study", [2026, 9]),
                ]
            ]
            seen = []

            def classify(config, titles):
                seen.extend(titles)
                return [{"category": "keep", "confidence": 1.0} for _ in titles]

            config = title_filter.LLMConfig("https://example.test/v1", "secret", "luna")
            with patch.object(title_filter, "llm_is_configured", return_value=True), \
                 patch.object(title_filter.TitleFilter, "_load_llm_config", return_value=config), \
                 patch.object(title_filter, "classify_llm_batch", side_effect=classify):
                self.run_collection(root, items)
            self.assertEqual(seen, ["Current engineering study", "Undated engineering study"])
            saved = [article for path in (root / "days").glob("*.json")
                     for article in json.loads(path.read_text())["articles"]]
            self.assertEqual({a["id"] for a in saved}, {"10.1000/current", "10.1000/undated"})

    def test_filtered_journal_still_removes_a_corrected_out_of_window_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            days = self.prepare_collection_data(root)
            (root / "filter-config.json").write_text(json.dumps({"journal_ids": ["example"]}))
            previous = self.article("2026-09-17")
            (days / "2026-09-17.json").write_text(json.dumps({"articles": [previous]}))
            corrected = {"DOI": previous["id"], "title": [previous["title_en"]],
                         "published-online": {"date-parts": [[2020, 1, 1]]}, "type": "journal-article"}
            with patch.object(title_filter, "classify_llm_batch") as classify:
                self.run_collection(root, [corrected])
            classify.assert_not_called()
            self.assertEqual(json.loads((days / "2026-09-17.json").read_text())["articles"], [])

    def test_collection_detail_requires_current_task_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            path.write_text(json.dumps({"token": "old", "completed": 1}))
            with patch.dict(os.environ, {"PAPER_TRACKER_COLLECTION_PROGRESS": str(path),
                                         "PAPER_TRACKER_COLLECTION_TOKEN": "new"}):
                collect.update_collection_detail(phase="filter", completed=64, total=128)
                self.assertNotIn("detail", json.loads(path.read_text()))
                path.write_text(json.dumps({"token": "new", "completed": 1}))
                collect.update_collection_detail(phase="filter", completed=64, total=128)
                saved = json.loads(path.read_text())
                self.assertEqual(saved["completed"], 1)
                self.assertEqual(saved["detail"], {"phase": "filter", "completed": 64, "total": 128})

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

    def test_historical_exclusions_are_removed_from_all_visible_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            days = self.prepare_collection_data(root)
            old_article = self.article("2026-09-17")
            old_article["doi"] = "10.1000/old-day"
            old_article["id"] = old_article["doi"]
            late_article = self.article("2026-09-16")
            late_article["doi"] = "10.1000/old-late"
            late_article["id"] = late_article["doi"]
            pending_article = self.article()
            pending_article["doi"] = "10.1000/old-pending"
            pending_article["id"] = pending_article["doi"]
            (days / "2026-09-17.json").write_text(
                json.dumps({"date": "2026-09-17", "articles": [old_article], "journal_status": {}}),
                encoding="utf-8",
            )
            (root / "supplements.json").write_text(
                json.dumps(
                    {
                        "late_additions": [{**late_article, "supplement_type": "late_addition"}],
                        "date_pending": [{**pending_article, "published_date": None, "supplement_type": "date_pending"}],
                    }
                ),
                encoding="utf-8",
            )
            entries = {}
            for article in (old_article, late_article, pending_article):
                entries[collect.article_key(article)] = {
                    **article,
                    "filter_category": "medicine",
                    "filter_confidence": 0.99,
                    "filter_engine": "openai-compatible:luna",
                    "filter_rule_version": "2026-09-19.1",
                    "excluded_at": "2026-09-18T01:00:00+08:00",
                }
            (root / "excluded-papers.json").write_text(
                json.dumps({"entries": entries}), encoding="utf-8"
            )

            self.run_collection(root, [])

            day = json.loads((days / "2026-09-17.json").read_text(encoding="utf-8"))
            supplements = json.loads((root / "supplements.json").read_text(encoding="utf-8"))
            self.assertEqual(day["articles"], [])
            self.assertEqual(supplements["late_additions"], [])
            self.assertEqual(supplements["date_pending"], [])
            if os.name != "nt":
                self.assertEqual(os.stat(days / "2026-09-17.json").st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()
