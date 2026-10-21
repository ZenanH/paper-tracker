"""Offline cloud tests: no Vercel, Crossref, GitHub or model credentials used."""
import copy
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.storage import MemoryStore, Conflict, StorageUnavailable, Snapshot, encode, decode
from cloud import workspace as w
from cloud.runtime import Runtime, queue_message
from cloud.tasks import process_chunk


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"CRON_SECRET": "offline-test-secret-" * 3})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = MemoryStore()
        self.sent = []
        self.runtime = Runtime(self.store, self.sent.append)

    def only_nature(self):
        document = self.runtime.ensure()
        with w.bind(document) as work:
            config = w.journal_config.ensure_config()
            config["categories"] = {"顶刊": [{"name": "Nature", "url": "https://www.nature.com/nature/"}]}
            w.common.write_json(work.data / "journal-config.json", config)
            w.sync_cas.main()
            w.common.write_json(work.data / "journals.json", {**w.common.read_json(work.data / "journals.json"), "metrics_checked_at": w.common.iso_now()})
            w.collect.save_manifest(w.common.read_json(work.data / "journals.json")["journals"], w.common.now_beijing().date(), collected=False)
            document["files"] = work.snapshot_files()
        self.store.save(document, self.store.load().etag)

    def test_snapshot_roundtrip_and_cas_conflict(self):
        snapshot = self.store.load()
        document = snapshot.document
        document["files"]["data/file.json"] = '{"中文":"标题"}'
        self.assertEqual(decode(encode(document)), document)
        with self.assertRaises(StorageUnavailable):
            decode(b"broken")
        self.store.save(document, snapshot.etag)
        with self.assertRaises(Conflict):
            self.store.save(document, snapshot.etag)

    def test_32_journal_bootstrap_is_persistent_and_only_public_data_is_readable(self):
        status, payload, _ = self.runtime.request("GET", "/data/journals.json")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["journals"]), 32)
        first = self.store.load().etag
        self.runtime.ensure()
        self.assertEqual(self.store.load().etag, first)
        self.assertEqual(self.runtime.request("GET", "/data/excluded-papers.json")[0], 404)
        self.assertEqual(self.runtime.request("GET", "/data/../secrets/llm-config.json")[0], 404)
        self.assertEqual(self.runtime.request("GET", "/api/admin/journals")[0], 200)

    def test_refresh_queues_and_rejects_concurrent_settings_and_preserves_favorites(self):
        self.only_nature()
        status, payload, _ = self.runtime.request("POST", "/api/admin/run", {"mode": "daily"})
        self.assertEqual(status, 202)
        self.assertEqual(payload["job"]["status"], "running")
        self.assertEqual(len(self.sent), 1)
        control = self.store.load().document["control"]
        self.assertEqual(control["kind"], "manual")
        self.assertEqual(self.runtime.request("POST", "/api/admin/run", {"mode": "daily"})[0], 400)
        self.assertEqual(self.runtime.request("POST", "/api/admin/filter", {"journal_ids": []})[0], 400)
        article = {"id": "favorite", "journal_id": "nature", "date": w.common.now_beijing().date().isoformat(), "title_en": "Saved mechanics"}
        self.assertEqual(self.runtime.request("POST", "/api/archive", {"action": "favorite", "items": [article]}, {"prefer": "return=minimal"})[0], 200)

        def chunk(work, task, seconds):
            # A concurrent browser mutation commits while the worker is running.
            with patch.object(w.journal_manager, "JOB", dict(w.journal_manager.JOB)):
                self.runtime.request("POST", "/api/archive", {"action": "favorite", "items": [{**article, "id": "second"}]})
            w.journal_manager.set_job(status="success", finished_at=w.common.iso_now())
            return True

        with patch("cloud.tasks.process_chunk", side_effect=chunk):
            self.runtime.worker(queue_message(control))
        saved = self.store.load().document
        self.assertIsNone(saved["control"])
        self.assertEqual(set(json.loads(saved["files"]["archive/archive.json"])["favorites"]), {"favorite", "second"})

    def test_source_cursor_is_saved_and_watermark_advances_only_when_journal_finishes(self):
        self.only_nature()
        self.runtime.request("POST", "/api/admin/run", {"mode": "daily"})
        document = self.store.load().document
        task = document["control"]
        task["journal_ids"] = ["nature"]
        target = w.common.now_beijing().date()
        items = [{"DOI": "10.1234/one", "title": ["Solid mechanics"], "published-online": {"date-parts": [[target.year, target.month, target.day]]}}]
        from cloud.tasks import _journal_page
        with w.bind(document) as work, patch.object(w.collect.CrossrefClient, "get", return_value={"items": items, "next-cursor": "unused"}):
            _journal_page(work, task)
            self.assertEqual(task["source"]["query"], 1)
            _journal_page(work, task)
            status = w.common.read_json(work.data / "collection-status.json")["nature"]
            self.assertIsNone(status.get("last_success_at"))
            for _ in range(8):
                _journal_page(work, task)
                if task["phase"] == "translate":
                    break
            status = w.common.read_json(work.data / "collection-status.json")["nature"]
            self.assertEqual(status["status"], "ok")
            self.assertTrue(status["last_success_at"])
            self.assertIsNone(w.common.read_json(work.data / "manifest.json")["collection_checked_at"])
            self.assertEqual(sum(len(w.common.read_json(path)["articles"]) for path in work.data.glob("days/*.json")), 1)

    def test_worker_failure_retains_existing_state_and_reports_failure(self):
        self.only_nature()
        self.runtime.request("POST", "/api/admin/run", {"mode": "daily"})
        task = self.store.load().document["control"]
        prior = self.store.load().document["files"]["data/journals.json"]
        with patch("cloud.tasks.process_chunk", side_effect=RuntimeError("credential-secret-never-return")):
            self.runtime.worker(queue_message(task))
        document = self.store.load().document
        self.assertIsNone(document["control"])
        self.assertEqual(document["files"]["data/journals.json"], prior)
        job = json.loads(document["files"]["data/task-status.json"])
        self.assertEqual(job["status"], "failed")
        self.assertNotIn("credential-secret", job["message"])

    def test_worker_lease_and_duplicate_delivery_are_safe(self):
        self.only_nature()
        self.runtime.request("POST", "/api/admin/run", {"mode": "daily"})
        task = self.store.load().document["control"]
        message = queue_message(task)
        self.store.update(lambda document: document["control"].update(lease_until=10**12))
        with self.assertRaises(Conflict):
            self.runtime.worker(message)
        self.store.update(lambda document: document["control"].update(lease_until=0))
        with patch("cloud.tasks.process_chunk", return_value=False):
            self.runtime.worker(message)
        document = self.store.load().document
        self.assertEqual(document["control"]["step"], 1)
        with patch("cloud.tasks.process_chunk") as chunk:
            self.runtime.worker(message)
        chunk.assert_not_called()

    def test_unfollow_after_cloud_restart_keeps_favorites_and_reuses_id(self):
        self.only_nature()
        paper = {"id": "old-favorite", "journal_id": "nature", "title_en": "Retained", "date": "2020-01-01"}
        self.runtime.request("POST", "/api/archive", {"action": "favorite", "items": [paper]})
        self.assertEqual(self.runtime.request("DELETE", "/api/admin/journals/nature")[0], 202)
        document = self.store.load().document
        with w.bind(document) as work:
            self.assertTrue(process_chunk(work, document["control"], seconds=120))
            document["files"] = work.snapshot_files()
        document["control"] = None
        self.store.save(document, self.store.load().etag)
        restarted = Runtime(self.store, self.sent.append)
        status, state, _ = restarted.request("GET", "/api/archive")
        self.assertEqual(status, 200)
        self.assertIn("old-favorite", state["favorites"])
        self.assertEqual(restarted.request("GET", "/data/journals.json")[1]["journals"], [])
        self.assertEqual(restarted.request("POST", "/api/admin/journals", {"name": "Nature", "filter_enabled": False})[0], 202)
        self.assertEqual(self.store.load().document["control"]["journal_ids"], ["nature"])


try:
    from fastapi.testclient import TestClient
    from cloud import web
except (ImportError, RuntimeError):
    TestClient = None


@unittest.skipIf(TestClient is None, "Install project cloud dependencies for cloud HTTP tests")
class CloudHTTPTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"PAPER_TRACKER_PASSWORD": "offline-password-123456789", "CRON_SECRET": "cron-test-" * 5})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = MemoryStore()
        self.runtime = Runtime(self.store, lambda _: None)
        self.patch = patch.object(web, "runtime", return_value=self.runtime)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = TestClient(web.app, follow_redirects=False)
        self.addCleanup(self.client.close)

    def test_login_all_routes_protected_and_secrets_not_public(self):
        self.assertEqual(self.client.get("/").status_code, 303)
        for path in ("/api/archive", "/api/admin/journals", "/data/journals.json"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/login", data={"password": "wrong"}).status_code, 401)
        response = self.client.post("/login", data={"password": "offline-password-123456789"})
        self.assertEqual(response.status_code, 303)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/data/journals.json").status_code, 200)
        self.assertEqual(self.client.get("/data/excluded-papers.json").status_code, 404)
        self.assertEqual(self.client.get("/secrets/llm-config.json").status_code, 404)
        self.assertEqual(self.client.post("/api/admin/run", json={"mode": "daily"}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/cron/morning").status_code, 401)

    def test_http_refresh_post_and_prefer_minimal(self):
        self.client.post("/login", data={"password": "offline-password-123456789"})
        self.assertEqual(self.client.post("/api/admin/run", json={"mode": "daily"}).status_code, 202)
        response = self.client.post("/api/archive", json={"action": "favorite", "items": [{"id": "one", "title_en": "Solid mechanics", "journal_id": "nature"}]}, headers={"Prefer": "return=minimal"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"updated_at"})
        self.assertEqual(response.headers["Preference-Applied"], "return=minimal")


if __name__ == "__main__":
    unittest.main()
