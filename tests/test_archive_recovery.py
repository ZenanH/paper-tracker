"""Local compatibility recovery preserves existing data after upgrades."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import archive_recovery


class ArchiveRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.archive = self.root / "archive/archive.json"
        self.backup = self.root / "archive/backups" / ("backup-" + "a" * 32)
        self.backup.mkdir(parents=True)
        self.marker = self.archive.parent / ".restore-pending.json"
        for name, value in (("DATA_DIR", self.root), ("ARCHIVE_FILE", self.archive)):
            patcher = patch.object(archive_recovery, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.archive.write_text(json.dumps({"favorites": {"saved": {"title_en": "Saved paper"}}}))
        (self.root / "secrets").mkdir()
        self.secret = self.root / "secrets/llm-config.json"
        self.secret.write_text("protected-settings")

    def stage(self, index):
        self.marker.write_text(json.dumps({"backup": self.backup.name}))
        (self.backup / "index.json").write_text(json.dumps(index))

    def test_no_marker_does_not_change_existing_files(self):
        before = self.archive.read_bytes()
        archive_recovery.recover_restore()
        self.assertEqual(self.archive.read_bytes(), before)
        self.assertEqual(self.secret.read_text(), "protected-settings")

    def test_interrupted_restore_recovers_once_without_touching_secrets(self):
        original = self.archive.read_bytes()
        (self.backup / "0.json").write_bytes(original)
        (self.backup / "1.json").write_text('{"categories":{}}')
        self.stage([{"copy": "0.json", "target": "archive"}, {"copy": "1.json", "target": "journal-config.json"}])
        self.archive.write_text('{"favorites":{}}')
        (self.root / "days").mkdir()
        (self.root / "days/2026-10-21.json").write_text("{}")
        archive_recovery.recover_restore()
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertEqual(json.loads((self.root / "journal-config.json").read_text()), {"categories": {}})
        self.assertFalse((self.root / "days/2026-10-21.json").exists())
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.backup.exists())
        self.assertEqual(self.secret.read_text(), "protected-settings")
        archive_recovery.recover_restore()
        self.assertEqual(self.archive.read_bytes(), original)

    def test_invalid_or_incomplete_backup_is_rejected_before_writes(self):
        original = self.archive.read_bytes()
        (self.backup / "0.json").write_text('{"favorites":{}}')
        for entry in ({"copy": "0.json", "target": "../secrets/llm-config.json"},
                      {"copy": "1.json", "target": "archive"}):
            with self.subTest(entry=entry):
                self.stage([{"copy": "0.json", "target": "journal-config.json"}, entry])
                with self.assertRaises(ValueError):
                    archive_recovery.recover_restore()
                self.assertEqual(self.archive.read_bytes(), original)
                self.assertFalse((self.root / "journal-config.json").exists())
                self.assertTrue(self.marker.exists())
                self.assertEqual(self.secret.read_text(), "protected-settings")

    def test_empty_or_corrupt_backup_cannot_erase_current_files(self):
        original = self.archive.read_bytes()
        for index in ([], [{"copy": "0.json", "target": "archive"}]):
            with self.subTest(index=index):
                (self.backup / "0.json").write_text("not-json")
                self.stage(index)
                with self.assertRaises(ValueError):
                    archive_recovery.recover_restore()
                self.assertEqual(self.archive.read_bytes(), original)
                self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
