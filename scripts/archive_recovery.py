"""Finish local rollback left by an interrupted restore in an older release."""
from __future__ import annotations

import fcntl
import os
import re
import shutil
from pathlib import Path

from common import DATA_DIR, read_json

ARCHIVE_FILE = Path(os.environ.get("PAPER_TRACKER_ARCHIVE_FILE", DATA_DIR / "archive/archive.json"))
MANAGED = ("journal-config.json", "journals.json", "manifest.json", "filter-config.json", "collection-status.json",
           "supplements.json", "translations.json", "title-classifications.json", "excluded-papers.json", "maintenance-status.json")


def recover_restore() -> None:
    marker = ARCHIVE_FILE.parent / ".restore-pending.json"
    if not marker.exists():
        return
    with (ARCHIVE_FILE.parent / ".archive.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        payload = read_json(marker)
        name = payload.get("backup") if isinstance(payload, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r"backup-[a-f0-9]{32}", name):
            raise ValueError("Invalid restore recovery marker. Manual recovery is required.")
        backup = ARCHIVE_FILE.parent / "backups" / name
        index = read_json(backup / "index.json")
        if not isinstance(index, list) or not index:
            raise ValueError("Restore recovery index is missing or corrupt. Saved files were retained.")
        copies, targets = [], set()
        # Validate the complete local backup before changing any current files.
        for entry in index:
            if not isinstance(entry, dict):
                raise ValueError("Invalid restore recovery entry.")
            filename, relative = entry.get("copy"), entry.get("target")
            if not isinstance(filename, str) or not re.fullmatch(r"\d+\.json", filename):
                raise ValueError("Invalid restore recovery file.")
            if not isinstance(relative, str) or (relative not in (*MANAGED, "archive") and not re.fullmatch(r"days/\d{4}-\d{2}-\d{2}\.json", relative)):
                raise ValueError("Invalid restore recovery target.")
            source = backup / filename
            target = ARCHIVE_FILE if relative == "archive" else DATA_DIR / relative
            if target in targets or not source.is_file():
                raise ValueError("Restore recovery files are missing or duplicated. Saved files were retained.")
            if not isinstance(read_json(source), dict):
                raise ValueError("Restore recovery JSON is corrupt. Saved files were retained.")
            targets.add(target)
            copies.append((source, target))
        for source, target in copies:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + ".restore-tmp")
            shutil.copy2(source, temp)
            os.replace(temp, target)
        current = [*(DATA_DIR / filename for filename in MANAGED), *sorted((DATA_DIR / "days").glob("????-??-??.json")), ARCHIVE_FILE]
        for target in current:
            if target not in targets:
                target.unlink(missing_ok=True)
        marker.unlink()
