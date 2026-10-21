import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "server" / "archive-api.py"
SPEC = importlib.util.spec_from_file_location("archive_api", MODULE_PATH)
archive_api = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(archive_api)


class ArchiveApiTests(unittest.TestCase):
    def test_unified_date_policy_normalizes_saved_records_once_without_changing_states(self):
        self.manifest_file.write_text(json.dumps({"date_policy": "current-month-start", "retention": {"start": "2026-06-18"}}))
        record = {"id": "saved", "journal_id": "example", "title_en": "Saved paper", "title_zh": "Saved translation"}
        self.archive_file.write_text(json.dumps({"items": {"saved": record}, "favorites": {"saved": record}, "cleared_items": {}}))
        with patch.object(archive_api, "now_iso", return_value="2026-09-18T13:00:00+08:00"):
            state = archive_api.read_pruned_state()
        self.assertEqual(state["items"]["saved"]["date"], "2026-09-01")
        self.assertEqual(state["favorites"]["saved"]["title_zh"], "Saved translation")
        with patch.object(archive_api, "now_iso", return_value="2026-10-18T13:00:00+08:00"):
            self.assertEqual(archive_api.read_pruned_state()["favorites"]["saved"]["date"], "2026-09-01")

    def test_pending_read_state_expires_by_first_discovery_but_favorite_survives(self):
        old = {"id": "pending:old", "journal_id": "example", "title_en": "Undated work",
               "first_seen_at": "2026-05-01T12:00:00+08:00"}
        recent = {**old, "id": "pending:recent", "first_seen_at": "2026-09-01T12:00:00+08:00"}
        archive_api.apply_archive({"action": "favorite", "items": [old]})
        archive_api.apply_archive({"action": "add", "items": [old, recent]})
        state = archive_api.read_pruned_state()
        self.assertNotIn(old["id"], state["items"])
        self.assertIn(recent["id"], state["items"])
        self.assertIn(old["id"], state["favorites"])
        self.assertNotIn("date", state["items"][recent["id"]])

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.archive_file = self.root / "archive.json"
        self.manifest_file = self.root / "manifest.json"
        self.manifest_file.write_text(
            json.dumps({"retention": {"start": "2026-06-18", "end": "2026-09-18"}}),
            encoding="utf-8",
        )
        self.patches = (
            patch.object(archive_api, "ARCHIVE_FILE", self.archive_file),
            patch.object(archive_api, "MANIFEST_FILE", self.manifest_file),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temporary_directory.cleanup()

    def test_existing_dataset_id_still_rejects_stale_browser_writes(self):
        self.archive_file.write_text(json.dumps({"items": {}, "cleared_items": {},
            "favorites": {"saved": {"id": "saved", "journal_id": "example", "title_en": "Keep favorite"}},
            "dataset_id": "existing-dataset"}))
        before = self.archive_file.read_bytes()
        for dataset_id in (None, "initial"):
            payload = {"action": "unfavorite", "ids": ["saved"]}
            if dataset_id is not None:
                payload["dataset_id"] = dataset_id
            with self.assertRaisesRegex(ValueError, "dataset changed"):
                archive_api.apply_archive(payload)
            self.assertEqual(self.archive_file.read_bytes(), before)
        state = archive_api.apply_archive({"action": "unfavorite", "ids": ["saved"], "dataset_id": "existing-dataset"})
        self.assertNotIn("saved", state["favorites"])

    def test_clear_keeps_read_state_and_restore_cleared_makes_it_visible(self):
        article = {
            "id": "doi:10.1000/example",
            "journal_id": "example",
            "date": "2026-09-18",
            "title_en": "Example",
        }
        state = archive_api.apply_archive({"action": "add", "items": [article]})
        self.assertIn(article["id"], state["items"])

        state = archive_api.apply_archive({"action": "clear", "ids": [article["id"]]})
        self.assertNotIn(article["id"], state["items"])
        self.assertIn(article["id"], state["cleared_items"])
        self.assertIn("cleared_at", state["cleared_items"][article["id"]])

        state = archive_api.apply_archive(
            {"action": "restore_cleared", "ids": [article["id"]]}
        )
        self.assertIn(article["id"], state["items"])
        self.assertNotIn(article["id"], state["cleared_items"])
        self.assertNotIn("cleared_at", state["items"][article["id"]])

        state = archive_api.apply_archive({"action": "remove", "ids": [article["id"]]})
        self.assertNotIn(article["id"], state["items"])
        self.assertNotIn(article["id"], state["cleared_items"])

    def test_retention_prunes_visible_and_cleared_items(self):
        self.archive_file.write_text(
            json.dumps(
                {
                    "updated_at": None,
                    "items": {
                        "old-visible": {"id": "old-visible", "date": "2026-06-17"},
                        "current-visible": {"id": "current-visible", "date": "2026-06-18"},
                    },
                    "cleared_items": {
                        "old-cleared": {"id": "old-cleared", "date": "2026-05-01"},
                        "current-cleared": {"id": "current-cleared", "date": "2026-09-18"},
                    },
                    "favorites": {
                        "old-favorite": {"id": "old-favorite", "date": "2026-05-01"},
                    },
                }
            ),
            encoding="utf-8",
        )

        state = archive_api.read_pruned_state()
        self.assertEqual(set(state["items"]), {"current-visible"})
        self.assertEqual(set(state["cleared_items"]), {"current-cleared"})
        self.assertEqual(set(state["favorites"]), {"old-favorite"})
        persisted = json.loads(self.archive_file.read_text(encoding="utf-8"))
        self.assertEqual(set(persisted["items"]), {"current-visible"})
        self.assertEqual(set(persisted["cleared_items"]), {"current-cleared"})

    def test_old_archive_files_gain_new_buckets(self):
        self.archive_file.write_text(
            json.dumps({"updated_at": None, "items": {}}), encoding="utf-8"
        )
        state = archive_api.read_state()
        self.assertEqual(state["cleared_items"], {})
        self.assertEqual(state["favorites"], {})

    def test_corrupt_archive_is_rejected_without_overwrite(self):
        original = "{not valid json\n"
        self.archive_file.write_text(original, encoding="utf-8")

        with self.assertRaises(archive_api.ArchiveStateError):
            archive_api.read_state()
        with self.assertRaises(archive_api.ArchiveStateError):
            archive_api.apply_archive({"action": "remove", "ids": ["anything"]})

        self.assertEqual(self.archive_file.read_text(encoding="utf-8"), original)

    def test_favorite_and_unfavorite(self):
        article = {
            "id": "doi:10.1000/favorite",
            "journal_id": "example",
            "date": "2026-09-18",
            "title_en": "Favorite example",
        }
        state = archive_api.apply_archive({"action": "favorite", "items": [article]})
        self.assertIn(article["id"], state["favorites"])
        self.assertIn("favorited_at", state["favorites"][article["id"]])

        state = archive_api.apply_archive({"action": "unfavorite", "ids": [article["id"]]})
        self.assertNotIn(article["id"], state["favorites"])

    def test_minimal_mutation_response_omits_full_archive(self):
        state = {
            "updated_at": "2026-09-18T01:00:00+08:00",
            "items": {"article": {"id": "article"}},
            "cleared_items": {},
            "favorites": {"favorite": {"id": "favorite"}},
        }
        self.assertEqual(
            archive_api.response_state(state, minimal=True),
            {"updated_at": state["updated_at"]},
        )
        self.assertIs(archive_api.response_state(state), state)

    def test_favorite_is_independent_from_archive_transitions(self):
        article = {
            "id": "doi:10.1000/independent",
            "journal_id": "example",
            "date": "2026-09-18",
            "title_en": "Independent favorite",
        }
        archive_api.apply_archive({"action": "favorite", "items": [article]})
        archive_api.apply_archive({"action": "add", "items": [article]})
        archive_api.apply_archive({"action": "clear", "ids": [article["id"]]})
        archive_api.apply_archive({"action": "restore_cleared", "ids": [article["id"]]})
        state = archive_api.apply_archive({"action": "remove", "ids": [article["id"]]})
        self.assertIn(article["id"], state["favorites"])
        self.assertNotIn(article["id"], state["items"])
        self.assertNotIn(article["id"], state["cleared_items"])

    def test_destructive_remove_all_action_is_not_supported(self):
        article = {
            "id": "doi:10.1000/delete",
            "journal_id": "example",
            "date": "2026-09-18",
            "title_en": "Deleted journal article",
        }
        archive_api.apply_archive({"action": "favorite", "items": [article]})
        archive_api.apply_archive({"action": "add", "items": [article]})
        with self.assertRaisesRegex(ValueError, "action must be"):
            archive_api.apply_archive({"action": "remove_all", "ids": [article["id"]]})
        state = archive_api.read_state()
        self.assertIn(article["id"], state["favorites"])
        self.assertIn(article["id"], state["items"])


if __name__ == "__main__":
    unittest.main()
