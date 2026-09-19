import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import journal_config
import journal_manager


class JournalManagementTests(unittest.TestCase):
    def tearDown(self):
        journal_manager.JOB.clear()
        journal_manager.JOB.update(journal_manager.JOB_DEFAULTS)

    def test_runtime_data_bootstrap_creates_empty_persistent_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "journals.json").write_text(
                json.dumps({"journals": [{"id": "example"}]}), encoding="utf-8"
            )
            with patch.object(journal_config, "DATA_DIR", data_dir):
                journal_config.ensure_runtime_data()

            manifest = json.loads(
                (data_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["journal_count"], 1)
            self.assertEqual(manifest["available_dates"], [])
            self.assertTrue((data_dir / "days").is_dir())
            self.assertEqual(
                json.loads((data_dir / "translations.json").read_text(encoding="utf-8"))["entries"],
                {},
            )
            self.assertEqual(
                json.loads((data_dir / "filter-config.json").read_text(encoding="utf-8"))["journal_ids"],
                [],
            )
            self.assertTrue((data_dir / "title-classifications.json").exists())

    def test_filter_config_validates_ids_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            with patch.object(journal_config, "DATA_DIR", data_dir):
                saved = journal_config.update_filter_config(["one", "two", "one"], {"one", "two"})
                self.assertEqual(saved["journal_ids"], ["one", "two"])
                self.assertEqual(journal_config.filter_journal_ids(), ["one", "two"])
                journal_config.ensure_runtime_data()
                self.assertEqual(journal_config.filter_journal_ids(), ["one", "two"])
                with self.assertRaises(ValueError):
                    journal_config.update_filter_config(["missing"], {"one", "two"})

    def test_add_and_remove_user_journal_in_persistent_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "journal-config.json"
            with patch.object(journal_config, "CONFIG_PATH", config_path), patch.object(
                journal_config, "DATA_DIR", Path(directory)
            ), patch.object(journal_config, "discover_metric_url", return_value=None):
                added = journal_config.add_journal("Nature")
                self.assertEqual(added["id"], "nature")
                self.assertEqual(added["group"], "一区")
                config = json.loads(config_path.read_text(encoding="utf-8"))
                entries = [
                    entry
                    for values in config["categories"].values()
                    for entry in values
                    if entry["name"] == "Nature"
                ]
                self.assertEqual(entries[0]["origin"], "user")
                journal_config.update_filter_config(["nature"], {"nature"})

                removed = journal_config.remove_journal("nature")
                self.assertEqual(removed["name"], "Nature")
                config = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertFalse(
                    any(
                        entry["name"] == "Nature"
                        for values in config["categories"].values()
                        for entry in values
                    )
                )
                self.assertEqual(journal_config.filter_journal_ids(), [])

    def test_remove_journal_data_cleans_days_supplements_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            days_dir = data_dir / "days"
            days_dir.mkdir()
            day_path = days_dir / "2026-09-18.json"
            day_path.write_text(
                json.dumps(
                    {
                        "articles": [
                            {"id": "remove-day", "journal_id": "remove-me"},
                            {"id": "keep-day", "journal_id": "keep-me"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (data_dir / "supplements.json").write_text(
                json.dumps(
                    {
                        "late_additions": [{"id": "remove-late", "journal_id": "remove-me"}],
                        "date_pending": [{"id": "keep-pending", "journal_id": "keep-me"}],
                    }
                ),
                encoding="utf-8",
            )
            (data_dir / "collection-status.json").write_text(
                json.dumps({"remove-me": {"status": "ok"}, "keep-me": {"status": "ok"}}),
                encoding="utf-8",
            )
            (data_dir / "title-classifications.json").write_text(
                json.dumps(
                    {
                        "entries": {
                            "remove-only": {"journal_ids": ["remove-me"]},
                            "shared": {"journal_ids": ["remove-me", "keep-me"]},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(journal_config, "DATA_DIR", data_dir):
                removed_ids = journal_config.remove_journal_data("remove-me")

            self.assertEqual(set(removed_ids), {"remove-day", "remove-late"})
            day = json.loads(day_path.read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in day["articles"]], ["keep-day"])
            supplements = json.loads(
                (data_dir / "supplements.json").read_text(encoding="utf-8")
            )
            self.assertEqual(supplements["late_additions"], [])
            statuses = json.loads(
                (data_dir / "collection-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(statuses), {"keep-me"})
            classifications = json.loads(
                (data_dir / "title-classifications.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(classifications["entries"]), {"shared"})
            self.assertEqual(classifications["entries"]["shared"]["journal_ids"], ["keep-me"])

    def test_next_run_is_one_am_beijing(self):
        timezone = ZoneInfo("Asia/Shanghai")
        before = datetime(2026, 9, 19, 0, 30, tzinfo=timezone)
        after = datetime(2026, 9, 19, 1, 30, tzinfo=timezone)
        self.assertEqual(
            journal_manager.next_run(before),
            datetime(2026, 9, 19, 1, 0, tzinfo=timezone),
        )
        self.assertEqual(
            journal_manager.next_run(after),
            datetime(2026, 9, 20, 1, 0, tzinfo=timezone),
        )

    def test_add_pipeline_runs_complete_targeted_workflow(self):
        with patch.object(journal_manager, "run_command") as run_command:
            journal_manager.add_pipeline("nature")

        self.assertEqual(
            [call.args[0] for call in run_command.call_args_list],
            [
                ["python3", "scripts/sync_cas.py"],
                ["python3", "scripts/update_metrics.py", "--journal-id", "nature"],
                [
                    "python3",
                    "scripts/collect.py",
                    "--mode",
                    "backfill",
                    "--journal-id",
                    "nature",
                ],
                [
                    "python3",
                    "scripts/translate.py",
                    "--limit",
                    "2000",
                    "--batch-size",
                    "8",
                    "--workers",
                    "2",
                ],
                ["python3", "scripts/validate_data.py"],
            ],
        )

    def test_restore_job_preserves_result_and_marks_running_as_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "task-status.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "kind": "daily",
                        "message": "running",
                        "started_at": "2026-09-19T01:00:00+08:00",
                        "finished_at": None,
                        "log": ["started"],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(journal_manager, "DATA_DIR", data_dir):
                journal_manager.restore_job()

            self.assertEqual(journal_manager.JOB["status"], "failed")
            self.assertIn("中断", journal_manager.JOB["message"])
            persisted = json.loads(
                (data_dir / "task-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["status"], "failed")

    def test_daily_catchup_detects_stale_manifest(self):
        timezone = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 9, 19, 2, 0, tzinfo=timezone)
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            path = data_dir / "manifest.json"
            path.write_text(json.dumps({"default_date": "2026-09-17"}), encoding="utf-8")
            with patch.object(journal_manager, "DATA_DIR", data_dir):
                self.assertTrue(journal_manager.daily_catchup_due(now))
                path.write_text(json.dumps({"default_date": "2026-09-18"}), encoding="utf-8")
                self.assertFalse(journal_manager.daily_catchup_due(now))

    def test_weekly_reconciliation_due_uses_persistent_date(self):
        with tempfile.TemporaryDirectory() as directory:
            maintenance = Path(directory) / "maintenance-status.json"
            with patch.object(journal_manager, "MAINTENANCE_PATH", maintenance):
                self.assertTrue(journal_manager.reconciliation_due(date(2026, 9, 19)))
                maintenance.write_text(
                    json.dumps({"last_reconciled_date": "2026-09-13"}), encoding="utf-8"
                )
                self.assertFalse(journal_manager.reconciliation_due(date(2026, 9, 19)))
                maintenance.write_text(
                    json.dumps({"last_reconciled_date": "2026-09-12"}), encoding="utf-8"
                )
                self.assertTrue(journal_manager.reconciliation_due(date(2026, 9, 19)))


if __name__ == "__main__":
    unittest.main()
