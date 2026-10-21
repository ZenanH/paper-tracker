"""Portable user data only. No API settings, credentials, or arbitrary paths."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import time
import urllib.parse
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from common import BEIJING, DATA_DIR, ROOT, article_key, ensure_publication_date, iso_now, now_beijing, read_json, retention_start, slugify, write_json
from gist_sync import protected_write
from journal_config import snapshot_row_for_name

ARCHIVE_FILE = Path(os.environ.get("PAPER_TRACKER_ARCHIVE_FILE", ROOT / "archive/archive.json"))
MANAGED = ("journal-config.json", "journals.json", "manifest.json", "filter-config.json", "collection-status.json",
           "supplements.json", "translations.json", "title-classifications.json", "excluded-papers.json", "maintenance-status.json")
JOURNAL_FIELDS = ("id", "name", "source_name", "group", "url", "issns", "cas", "impact_factor", "origin", "added_at", "unfollowed_at")
PAPER_FIELDS = ("id", "doi", "journal_id", "title_en", "title_zh", "url", "published_date", "date_precision", "date_source", "raw_publication_date",
                "date_inferred", "date_inference", "first_discovered_at", "first_seen_at", "last_seen_at", "content_type", "translation_status", "translation_engine")


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def check_url(value) -> None:
    if not isinstance(value, str):
        raise ValueError("Invalid URL in backup.")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Invalid URL in backup.")


@contextmanager
def archive_lock():
    ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with (ARCHIVE_FILE.parent / ".archive.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_archive() -> dict:
    value = read_json(ARCHIVE_FILE, {"items": {}, "cleared_items": {}, "favorites": {}})
    if not isinstance(value, dict) or any(not isinstance(value.get(key, {}), dict) for key in ("items", "cleared_items", "favorites")):
        raise ValueError("Saved reading data is corrupt. No backup or restore was performed.")
    return value


def snapshot_files() -> dict[str, dict]:
    today = now_beijing().date()
    cutoff = retention_start(today).isoformat()
    config = read_json(DATA_DIR / "journal-config.json", {})
    metadata = read_json(DATA_DIR / "journals.json", {})
    journals = {journal["id"]: {key: journal[key] for key in JOURNAL_FIELDS if key in journal}
                for journal in [*metadata.get("journals", []), *metadata.get("unfollowed_journals", [])]}
    for journal in journals.values():
        if isinstance(journal.get("impact_factor"), dict):
            journal["impact_factor"] = {key: value for key, value in journal["impact_factor"].items()
                                        if key in {"value", "year", "status", "source_url", "checked_at", "report_version"}}
    active = {journal["id"] for journal in metadata.get("journals", [])}
    for entries in config.get("categories", {}).values():
        for entry in entries:
            identifier = slugify(entry["name"])
            if identifier in journals:
                journals[identifier]["config_entry"] = {key: entry[key] for key in ("name", "url", "if", "if_year", "origin", "added_at", "metric_url") if key in entry}
    archive = read_archive()
    records = {}
    for path in sorted((DATA_DIR / "days").glob("????-??-??.json")):
        for article in read_json(path, {}).get("articles", []):
            if cutoff <= (article.get("published_date") or "") <= today.isoformat():
                records[article["id"]] = {key: article[key] for key in PAPER_FIELDS if key in article}
    for bucket in ("cleared_items", "items", "favorites"):
        for identifier, item in archive.get(bucket, {}).items():
            if not isinstance(item, dict) or item.get("journal_id") not in journals:
                raise ValueError("Saved papers refer to an unknown journal. Repair journal metadata before exporting.")
            if bucket != "favorites" and not cutoff <= (item.get("date") or "") <= today.isoformat():
                continue
            record = records.setdefault(identifier, {key: item[key] for key in PAPER_FIELDS if key in item})
            record.setdefault("published_date", item.get("date"))
            ensure_publication_date(record, today)
            if bucket == "favorites":
                record.update(favorite=True, favorited_at=item.get("favorited_at"))
            else:
                record.update(archived=True, archive_bucket=bucket, archived_at=item.get("archived_at"))
    grouped = {identifier: [] for identifier in journals}
    for article in records.values():
        if article.get("journal_id") not in grouped:
            raise ValueError("Paper metadata contains an unknown journal.")
        grouped[article["journal_id"]].append(article)
    files, descriptors = {}, []
    for index, (identifier, journal) in enumerate(sorted(journals.items())):
        journal["followed"] = identifier in active
        # One journal can span multiple bounded files; no Gist truncation.
        chunks, chunk, chunk_size = [], [], 2
        for article in grouped[identifier]:
            article_size = len(compact(article).encode("utf-8")) + 1
            if article_size > 600000:
                raise ValueError("An article exceeds the backup record size limit.")
            if chunk and chunk_size + article_size > 650000:
                chunks.append(chunk)
                chunk, chunk_size = [], 2
            chunk.append(article)
            chunk_size += article_size
        chunks.append(chunk)
        for part, articles in enumerate(chunks):
            name = f"journal-{index:03d}-{part:03d}.json"
            content = compact({"journal": journal, "papers": articles})
            files[name] = {"content": content}
            descriptors.append({"name": name, "sha256": sha(content)})
    rules = read_json(DATA_DIR / "filter-config.json", {})
    manifest = {"format": "paper-tracker", "version": 1, "created_at": iso_now(), "files": descriptors,
                "filter": {"journal_ids": rules.get("journal_ids", []), "prompt": rules.get("prompt", "")}}
    files["paper-tracker.json"] = {"content": compact(manifest)}
    if len(files) > 200 or sum(len(item["content"].encode("utf-8")) for item in files.values()) > 64 * 1024 * 1024:
        raise ValueError("The backup exceeds the Gist size limit.")
    return files


def validate_files(files: dict[str, str]) -> dict:
    try:
        manifest = json.loads(files["paper-tracker.json"])
        if manifest.get("format") != "paper-tracker" or manifest.get("version") != 1:
            raise ValueError("Unsupported Paper Tracker backup version.")
        descriptors = manifest["files"]
        if not isinstance(descriptors, list) or len(descriptors) > 199:
            raise ValueError("Invalid backup file list.")
        names = [entry["name"] for entry in descriptors]
        if len(names) != len(set(names)) or set(files) != {*names, "paper-tracker.json"}:
            raise ValueError("Backup files are missing or duplicated.")
        journals, articles = {}, {}
        for entry in descriptors:
            name = entry["name"]
            if not re.fullmatch(r"journal-\d{3,}-\d{3,}\.json", name) or sha(files[name]) != entry["sha256"]:
                raise ValueError("Backup checksum failed.")
            part = json.loads(files[name]); journal = part["journal"]
            if set(journal) - {*JOURNAL_FIELDS, "config_entry", "followed"}:
                raise ValueError("Unexpected journal fields in backup.")
            if not isinstance(journal.get("name"), str) or len(journal["name"]) > 300 or journal.get("id") != slugify(journal["name"]) or not isinstance(journal.get("followed"), bool):
                raise ValueError("Invalid journal identity in backup.")
            row = snapshot_row_for_name(journal["name"])
            if journal.get("cas", {}).get("zone") != int(row["大类分区"].split()[0]):
                raise ValueError("The backup CAS division does not match the fixed 2025 snapshot.")
            journal["issns"] = [value.strip() for value in row["ISSN/EISSN"].split("/") if value.strip()]
            journal["cas"] = {"version": "2025升级版", "zone": int(row["大类分区"].split()[0]), "category": row["大类"], "rank": row["大类分区"], "top": row["Top"] == "是"}
            if not isinstance(journal.get("config_entry", {}), dict) or not isinstance(journal.get("group", ""), str):
                raise ValueError("Invalid journal configuration.")
            check_url(journal.get("url", "https://doi.org"))
            config = journal.get("config_entry", {})
            if set(config) - {"name", "url", "if", "if_year", "origin", "added_at", "metric_url"}:
                raise ValueError("Unexpected journal configuration fields.")
            for url_key in ("url", "metric_url"):
                if config.get(url_key):
                    check_url(config[url_key])
            metric = journal.get("impact_factor")
            if metric is not None and not isinstance(metric, dict):
                raise ValueError("Invalid journal metrics.")
            if metric and set(metric) - {"value", "year", "status", "source_url", "checked_at", "report_version"}:
                raise ValueError("Unexpected metric fields.")
            identifier = journal["id"]
            if identifier in journals and journals[identifier] != journal:
                raise ValueError("Conflicting journal parts in backup.")
            journals[identifier] = journal
            if not isinstance(part["papers"], list):
                raise ValueError("Invalid paper list.")
            for article in part["papers"]:
                if not isinstance(article, dict) or set(article) - {*PAPER_FIELDS, "favorite", "favorited_at", "archived", "archive_bucket", "archived_at"}:
                    raise ValueError("Unexpected paper fields in backup.")
                key = article.get("id")
                if not isinstance(key, str) or not key or len(key) > 1000 or key in articles or article.get("journal_id") != identifier:
                    raise ValueError("Invalid or duplicate paper identity.")
                if not isinstance(article.get("title_en"), str) or not article["title_en"].strip() or len(article["title_en"]) > 30000:
                    raise ValueError("Invalid paper title.")
                for text in ("title_zh", "translation_status", "translation_engine"):
                    if article.get(text) is not None and not isinstance(article[text], str):
                        raise ValueError("Invalid paper text fields.")
                check_url(article.get("url", ""))
                for flag in ("favorite", "archived", "date_inferred"):
                    if flag in article and not isinstance(article[flag], bool):
                        raise ValueError("Invalid reading-state flags.")
                article = ensure_publication_date(article, now_beijing().date())
                date.fromisoformat(article["published_date"])
                articles[key] = article
        rules = manifest.get("filter", {})
        if not isinstance(rules.get("prompt", ""), str) or len(rules.get("prompt", "")) > 8000 or not isinstance(rules.get("journal_ids", []), list):
            raise ValueError("Invalid filter rules.")
        active = {identifier for identifier, journal in journals.items() if journal["followed"]}
        if not set(rules.get("journal_ids", [])) <= active:
            raise ValueError("Filter rules reference unfollowed journals.")
        if len(articles) > 100000:
            raise ValueError("The backup exceeds the paper count limit.")
        return {"journals": journals, "papers": articles, "filter": rules}
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError("The Gist is not a complete Paper Tracker backup.") from None


def stage_import(files: dict[str, str]) -> dict:
    payload = validate_files(files)
    token = secrets.token_hex(24)
    path = ARCHIVE_FILE.parent / ".gist-staging" / f"{token}.json"
    # Keep only a single preview; staging is not part of the public data route.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for old in path.parent.glob("*.json"):
        old.unlink()
    protected_write(path, payload)
    return {"preview_id": token, "journals": sum(j["followed"] for j in payload["journals"].values()),
            "papers": len(payload["papers"]), "favorites": sum(bool(a.get("favorite")) for a in payload["papers"].values())}


def managed_paths() -> list[Path]:
    return [*(DATA_DIR / name for name in MANAGED), *sorted((DATA_DIR / "days").glob("????-??-??.json")), ARCHIVE_FILE]


def rollback(backup: Path) -> None:
    index = read_json(backup / "index.json")
    for target in managed_paths():
        target.unlink(missing_ok=True)
    for entry in index:
        source = backup / entry["copy"]
        target = ARCHIVE_FILE if entry["target"] == "archive" else DATA_DIR / entry["target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".restore-tmp")
        shutil.copy2(source, temp)
        os.replace(temp, target)


def recover_restore() -> None:
    marker = ARCHIVE_FILE.parent / ".restore-pending.json"
    if marker.exists():
        with archive_lock():
            payload = read_json(marker)
            name = payload.get("backup", "")
            if not re.fullmatch(r"backup-[a-f0-9]{32}", name):
                raise ValueError("Invalid restore recovery marker. Manual recovery is required.")
            rollback(ARCHIVE_FILE.parent / "backups" / name)
            marker.unlink()


def restore_preview(token: str, validator=None) -> dict:
    if not re.fullmatch(r"[a-f0-9]{48}", str(token)):
        raise ValueError("Invalid import preview.")
    path = ARCHIVE_FILE.parent / ".gist-staging" / f"{token}.json"
    if not path.exists() or time.time() - path.stat().st_mtime > 1800:
        raise ValueError("Import preview expired. Preview the Gist again.")
    payload = read_json(path)
    today = now_beijing().date(); cutoff = retention_start(today).isoformat()
    categories, followed, inactive = {}, [], []
    for journal in payload["journals"].values():
        if journal["followed"]:
            entry = {key: value for key, value in journal.get("config_entry", {}).items() if key in {"name", "url", "if", "if_year", "origin", "added_at", "metric_url"}}
            entry.update(name=journal["name"], url=journal.get("url") or "https://doi.org")
            categories.setdefault(journal.get("group") or "Other", []).append(entry)
            followed.append({key: journal[key] for key in JOURNAL_FIELDS if key in journal})
        else:
            inactive.append({key: value for key, value in journal.items() if key != "followed"})
    days, archive = {}, {"items": {}, "cleared_items": {}, "favorites": {}, "dataset_id": secrets.token_hex(16), "updated_at": iso_now()}
    active = {journal["id"] for journal in followed}
    for article in payload["papers"].values():
        date = article["published_date"]
        clean = {key: article[key] for key in PAPER_FIELDS if key in article}
        if article["journal_id"] in active and cutoff <= date <= today.isoformat():
            days.setdefault(date, []).append(clean)
        saved = {**clean, "date": date}
        if article.get("favorite"):
            archive["favorites"][article["id"]] = {**saved, "favorited_at": article.get("favorited_at") or iso_now()}
        if article.get("archived") and cutoff <= date <= today.isoformat():
            bucket = "cleared_items" if article.get("archive_bucket") == "cleared_items" else "items"
            archive[bucket][article["id"]] = {**saved, "archived_at": article.get("archived_at") or iso_now()}
    data = {"journal-config.json": {"categories": categories, "unfollowed_journals": inactive, "config_updated_at": iso_now()},
            "journals.json": {"journals": followed, "unfollowed_journals": inactive, "generated_at": iso_now()},
            "filter-config.json": {**payload["filter"], "updated_at": iso_now()}, "collection-status.json": {},
            "supplements.json": {"late_additions": [], "date_pending": []}, "translations.json": {"entries": {}},
            "title-classifications.json": {"entries": {}}, "excluded-papers.json": {"entries": {}}, "maintenance-status.json": {},
            "manifest.json": {"schema_version": 2, "date_policy": "current-month-start", "update_interval_hours": 12, "timezone": "Asia/Shanghai",
                              "default_date": today.isoformat(), "updated_at": iso_now(), "collection_checked_at": None,
                              "retention": {"start": cutoff, "end": today.isoformat()}, "available_dates": sorted(days),
                              "journal_count": len(followed), "collection_summary": {"ok": 0, "failed": 0}}}
    with archive_lock():
        read_archive()  # Never overwrite corrupt saved state.
        backup = ARCHIVE_FILE.parent / "backups" / ("backup-" + secrets.token_hex(16))
        backup.mkdir(parents=True, mode=0o700)
        index = []
        for i, target in enumerate(managed_paths()):
            if target.exists():
                name = f"{i}.json"; shutil.copy2(target, backup / name)
                index.append({"copy": name, "target": "archive" if target == ARCHIVE_FILE else str(target.relative_to(DATA_DIR))})
        write_json(backup / "index.json", index)
        marker = ARCHIVE_FILE.parent / ".restore-pending.json"
        protected_write(marker, {"backup": backup.name})
        try:
            for name, value in data.items():
                if name != "manifest.json":
                    write_json(DATA_DIR / name, value)
            for old in (DATA_DIR / "days").glob("????-??-??.json"):
                if old.stem not in days:
                    old.unlink()
            for day, articles in days.items():
                write_json(DATA_DIR / "days" / f"{day}.json", {"date": day, "articles": articles, "timezone": "Asia/Shanghai", "generated_at": iso_now()})
            write_json(ARCHIVE_FILE, archive)
            write_json(DATA_DIR / "manifest.json", data["manifest.json"])
            if validator:
                validator()
            marker.unlink()
        except Exception:
            rollback(backup)
            marker.unlink(missing_ok=True)
            raise
        path.unlink()
    return {"journals": len(followed), "papers": sum(len(items) for items in days.values()), "favorites": len(archive["favorites"])}
