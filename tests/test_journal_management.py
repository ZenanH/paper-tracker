import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import journal_config
import journal_manager


class JournalManagementTests(unittest.TestCase):
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

    def test_add_and_remove_user_journal_in_persistent_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "journal-config.json"
            with patch.object(journal_config, "CONFIG_PATH", config_path), patch.object(
                journal_config, "discover_metric_url", return_value=None
            ):
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


if __name__ == "__main__":
    unittest.main()
