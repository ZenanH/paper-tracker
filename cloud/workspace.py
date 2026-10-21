"""Hydrate a private snapshot into an isolated, disposable working directory.

Only a successful Blob CAS commits the result. Local files and flock are never
used for cross-invocation persistence or concurrency. Existing domain modules
run synchronously under a per-process binding lock, with no background threads.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import collect
import common
import excluded_papers
import gist_sync
import journal_config
import journal_manager
import llm_settings
import sync_cas
import title_filter
import translate
import update_metrics
import user_backup
import validate_data

spec = importlib.util.spec_from_file_location("cloud_archive", ROOT / "server/archive-api.py")
archive_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_api)
BINDING_LOCK = threading.RLock()
DATA_MODULES = (common, collect, journal_config, journal_manager, sync_cas, translate, update_metrics, user_backup, validate_data)


def valid_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        isinstance(value, str) and not path.is_absolute() and len(path.parts) >= 2
        and path.parts[0] in {"data", "archive", "secrets"}
        and not any(part in {"..", "."} or "\\" in part for part in path.parts)
        and path.suffix == ".json"
    )


class Workspace:
    def __init__(self, root: Path, document: dict) -> None:
        self.root, self.document = root, document
        self.data = root / "data"
        self.archive = root / "archive" / "archive.json"
        for path in (self.data, self.archive.parent, root / "secrets"):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name, content in document["files"].items():
            if not valid_path(name) or not isinstance(content, str):
                raise ValueError("The saved cloud snapshot contains an invalid file.")
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_text(content, encoding="utf-8")
            path.chmod(0o600)

    def snapshot_files(self) -> dict[str, str]:
        return {
            str(path.relative_to(self.root)): path.read_text(encoding="utf-8")
            for directory in (self.data, self.archive.parent, self.root / "secrets")
            for path in directory.rglob("*.json") if path.is_file()
        }

    def initialize(self) -> bool:
        if (self.data / "manifest.json").exists():
            return False
        journal_config.ensure_config()
        sync_cas.main()
        journal_config.ensure_runtime_data()
        collect.save_manifest(common.read_json(self.data / "journals.json")["journals"], common.now_beijing().date(), collected=False)
        return True


@contextmanager
def bind(document: dict):
    with BINDING_LOCK, tempfile.TemporaryDirectory(prefix="paper-tracker-cloud-") as directory, ExitStack() as stack:
        work = Workspace(Path(directory), document)
        for module in DATA_MODULES:
            stack.enter_context(patch.object(module, "DATA_DIR", work.data))
        for module in (common, collect):
            stack.enter_context(patch.object(module, "DAYS_DIR", work.data / "days"))
        config = work.data / "journal-config.json"
        stack.enter_context(patch.object(journal_config, "CONFIG_PATH", config))
        stack.enter_context(patch.object(journal_manager, "CONFIG_PATH", config))
        stack.enter_context(patch.object(journal_manager, "MAINTENANCE_PATH", work.data / "maintenance-status.json"))
        stack.enter_context(patch.object(llm_settings, "CONFIG_PATH", work.root / "secrets/llm-config.json"))
        stack.enter_context(patch.object(gist_sync, "CONFIG_PATH", work.root / "secrets/github-gist.json"))
        stack.enter_context(patch.object(gist_sync, "FLOWS", document.setdefault("auth_flows", {})))
        stack.enter_context(patch.object(user_backup, "ARCHIVE_FILE", work.archive))
        stack.enter_context(patch.object(archive_api, "ARCHIVE_FILE", work.archive))
        stack.enter_context(patch.object(archive_api, "MANIFEST_FILE", work.data / "manifest.json"))
        job = common.read_json(work.data / "task-status.json", journal_manager.JOB_DEFAULTS)
        stack.enter_context(patch.object(journal_manager, "JOB", {**journal_manager.JOB_DEFAULTS, **job}))
        yield work


def public_data(document: dict, path: str) -> dict | None:
    import re
    if path not in {"manifest.json", "journals.json"} and not re.fullmatch(r"days/\d{4}-\d{2}-\d{2}\.json", path):
        return None
    raw = document["files"].get("data/" + path)
    return json.loads(raw) if raw is not None else None


def merge_files(document: dict, before: dict, after: dict, *, preserve_archive: bool = False) -> None:
    """Merge worker output without losing concurrent favorite/read mutations."""
    for path in before.keys() | after.keys():
        if before.get(path) == after.get(path):
            continue
        if preserve_archive and path.startswith("archive/"):
            continue
        if document["files"].get(path) != before.get(path):
            from cloud.storage import Conflict
            raise Conflict("These records changed while the task was running. Please retry.")
        if path in after:
            document["files"][path] = after[path]
        else:
            document["files"].pop(path, None)
