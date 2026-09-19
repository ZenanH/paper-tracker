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

    def test_remove_all_cleans_archive_and_favorite_state(self):
        article = {
            "id": "doi:10.1000/delete",
            "journal_id": "example",
            "date": "2026-09-18",
            "title_en": "Deleted journal article",
        }
        archive_api.apply_archive({"action": "favorite", "items": [article]})
        archive_api.apply_archive({"action": "add", "items": [article]})
        state = archive_api.apply_archive({"action": "remove_all", "ids": [article["id"]]})
        self.assertNotIn(article["id"], state["favorites"])
        self.assertNotIn(article["id"], state["items"])


if __name__ == "__main__":
    unittest.main()
