import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import journal_config
import journal_manager
from common import read_json


class AdminRoutesTests(unittest.TestCase):
    def post(self, path, payload):
        handler = object.__new__(journal_manager.Handler)
        handler.path = path
        handler.read_payload = Mock(return_value=payload)
        handler.send_json = Mock()
        handler.do_POST()
        return handler.send_json.call_args.args

    def test_resolve_returns_verified_snapshot_without_following(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            journal_config, "CONFIG_PATH", Path(directory) / "config.json"
        ):
            status, payload = self.post("/api/admin/journals/resolve", {"name": "Nature"})
            self.assertEqual(status, 200)
            self.assertIn("0028-0836", payload["issns"])
            self.assertEqual(payload["cas"]["zone"], 1)
            self.assertFalse(journal_config.CONFIG_PATH.exists())

    def test_removed_backup_and_authorization_routes_return_not_found(self):
        with patch.object(journal_manager, "start_job") as start:
            for route in ("auth/start", "auth/poll", "disconnect", "export", "preview", "import"):
                with self.subTest(route=route):
                    self.assertEqual(self.post("/api/admin/gist/" + route, {})[0], 404)
            start.assert_not_called()

    def test_ambiguous_and_missing_names_do_not_follow(self):
        for name in ["Journal", "Definitely not a scientific journal 12345", 123]:
            with self.subTest(name=name):
                status, payload = self.post("/api/admin/journals/resolve", {"name": name})
                self.assertEqual(status, 400)
                self.assertIn("error", payload)

    def test_filter_cannot_change_while_job_is_running(self):
        with patch.dict(journal_manager.JOB, {"status": "running"}), patch.object(
            journal_manager, "update_filter_config"
        ) as update:
            status, _ = self.post("/api/admin/filter", {"journal_ids": [], "prompt": "changed"})
            self.assertEqual(status, 400)
            update.assert_not_called()

    def test_metrics_update_is_mutually_exclusive(self):
        with patch.dict(journal_manager.JOB, {"status": "running"}):
            status, payload = self.post("/api/admin/metrics/update", {})
        self.assertEqual(status, 400)
        self.assertIn("already running", payload["error"])

    def test_backfill_scope_is_validated_and_run_exclusively(self):
        self.assertEqual(self.post("/api/admin/run", {"mode": {}})[0], 400)
        with patch.object(journal_manager, "read_json", return_value={"journals": [{"id": "one"}]}), patch.object(journal_manager, "journal_payload", return_value={}), patch.object(journal_manager, "start_job") as start, patch.object(journal_manager, "backfill_pipeline") as backfill:
            self.assertEqual(self.post("/api/admin/run", {"mode": "backfill", "journal_ids": ["bad"]})[0], 400)
            start.assert_not_called()
            self.assertEqual(self.post("/api/admin/run", {"mode": "backfill", "journal_ids": ["one", "one"]})[0], 202)
            kind, callback = start.call_args.args
            self.assertEqual(kind, "backfill")
            callback()
            backfill.assert_called_once_with(["one"])
        with patch.dict(journal_manager.JOB, {"status": "running"}):
            self.assertEqual(self.post("/api/admin/run", {"mode": "backfill"})[0], 400)

    def test_refresh_runs_full_daily_pipeline_and_rejects_duplicate_jobs(self):
        with patch.object(journal_manager, "journal_payload", return_value={"job": {"status": "running"}}), patch.object(
            journal_manager, "start_job"
        ) as start, patch.object(journal_manager, "daily_pipeline") as daily:
            status, payload = self.post("/api/admin/run", {"mode": "daily"})
            self.assertEqual(status, 202)
            self.assertEqual(payload["job"]["status"], "running")
            kind, callback = start.call_args.args
            self.assertEqual(kind, "manual")
            callback()
            daily.assert_called_once_with()
        with patch.dict(journal_manager.JOB, {"status": "running"}), patch.object(journal_manager, "daily_pipeline") as daily:
            status, payload = self.post("/api/admin/run", {"mode": "daily"})
            self.assertEqual(status, 400)
            self.assertIn("already running", payload["error"])
            daily.assert_not_called()

    def test_canonical_alias_cannot_follow_the_same_journal_twice(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            journal_config, "CONFIG_PATH", Path(directory) / "config.json"
        ):
            with self.assertRaisesRegex(ValueError, "already follow"):
                journal_config.add_journal(
                    "Future Generation Computer Systems-The International Journal of eScience"
                )

    def test_invalid_prompt_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            journal_config, "DATA_DIR", Path(directory)
        ):
            journal_config.update_filter_config(["example"], {"example"}, "保留力学")
            before = read_json(Path(directory) / "filter-config.json")
            with self.assertRaises(ValueError):
                journal_config.update_filter_config([], set(), {"bad": "not text"})
            self.assertEqual(before, read_json(Path(directory) / "filter-config.json"))


if __name__ == "__main__":
    unittest.main()
