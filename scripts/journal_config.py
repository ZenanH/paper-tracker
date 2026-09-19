from __future__ import annotations

import csv
import io
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from common import DATA_DIR, ROOT, iso_now, read_json, slugify, write_json

LOCAL_SNAPSHOT = ROOT / "scripts" / "snapshot" / "FQBJCR2025-UTF8.csv"
DEFAULT_CONFIG = ROOT / "journal.json"
CONFIG_PATH = Path(
    os.environ.get("PAPER_TRACKER_JOURNAL_CONFIG", DATA_DIR / "journal-config.json")
).expanduser().resolve()


def normalized(value: str) -> str:
    return "".join(
        character
        for character in value.lower().replace("&", "and")
        if character.isalnum()
    )


def load_snapshot_rows() -> list[dict[str, str]]:
    content = LOCAL_SNAPSHOT.read_bytes()
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))


def ensure_config() -> dict[str, Any]:
    existing = read_json(CONFIG_PATH)
    if isinstance(existing, dict) and isinstance(existing.get("categories"), dict):
        return existing
    seed = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    for entries in seed.get("categories", {}).values():
        for entry in entries:
            entry.setdefault("origin", "default")
    seed["config_updated_at"] = iso_now()
    write_json(CONFIG_PATH, seed)
    return seed


def iter_entries(config: dict[str, Any]):
    for group, entries in config.get("categories", {}).items():
        for entry in entries:
            yield group, entry


def snapshot_row_for_name(name: str) -> dict[str, str]:
    query = normalized(name)
    rows = load_snapshot_rows()
    exact = [row for row in rows if normalized(row.get("Journal", "")) == query]
    if len(exact) == 1:
        return exact[0]

    aliases = {
        normalized("Future Generation Computer Systems"): normalized(
            "Future Generation Computer Systems-The International Journal of eScience"
        )
    }
    alias = aliases.get(query)
    if alias:
        matches = [row for row in rows if normalized(row.get("Journal", "")) == alias]
        if len(matches) == 1:
            return matches[0]

    partial = [row for row in rows if query and query in normalized(row.get("Journal", ""))]
    if len(partial) == 1:
        return partial[0]
    if partial:
        candidates = ", ".join(row["Journal"] for row in partial[:5])
        raise ValueError(f"期刊名称不唯一，请填写完整名称。候选：{candidates}")
    raise ValueError("固定 2025 分区快照中未找到该期刊，请核对英文全名")


def discover_metric_url(issns: list[str]) -> str | None:
    for issn in issns:
        params = urllib.parse.urlencode(
            {"rows": 1, "sort": "published", "order": "desc", "select": "URL,resource"}
        )
        url = f"https://api.crossref.org/journals/{urllib.parse.quote(issn)}/works?{params}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "PaperTracker/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            item = ((payload.get("message") or {}).get("items") or [None])[0] or {}
            primary = ((item.get("resource") or {}).get("primary") or {}).get("URL")
            if primary or item.get("URL"):
                return primary or item["URL"]
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            continue
    return None


def default_entry(name: str) -> tuple[str, dict[str, Any]] | None:
    seed = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    query = normalized(name)
    for group, entry in iter_entries(seed):
        if normalized(entry["name"]) == query:
            return group, dict(entry)
    return None


def add_journal(name: str) -> dict[str, Any]:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("期刊名称不能为空")
    config = ensure_config()
    row = snapshot_row_for_name(name)
    canonical_name = row["Journal"].strip()
    display_name = name if normalized(name) == normalized(canonical_name) else canonical_name
    canonical_key = normalized(canonical_name)
    for _, entry in iter_entries(config):
        if normalized(entry["name"]) == canonical_key or normalized(entry["name"]) == normalized(name):
            raise ValueError("该期刊已经在追踪列表中")

    issns = list(dict.fromkeys(part.strip() for part in row["ISSN/EISSN"].split("/") if part.strip()))
    known = default_entry(name) or default_entry(canonical_name)
    if known:
        group, entry = known
    else:
        zone = int(row["大类分区"].split()[0])
        group = {1: "一区", 2: "二区", 3: "三区", 4: "四区"}.get(zone, f"{zone}区")
        entry = {
            "name": display_name,
            "url": f"https://search.crossref.org/?q={urllib.parse.quote(display_name)}",
        }
    entry["origin"] = "user"
    entry["added_at"] = iso_now()
    metric_url = discover_metric_url(issns)
    if metric_url:
        entry["metric_url"] = metric_url
    config.setdefault("categories", {}).setdefault(group, []).append(entry)
    config["config_updated_at"] = iso_now()
    write_json(CONFIG_PATH, config)
    return {
        "id": slugify(entry["name"]),
        "name": entry["name"],
        "group": group,
        "issns": issns,
    }


def remove_journal(journal_id: str) -> dict[str, Any]:
    config = ensure_config()
    removed = None
    for group, entries in list(config.get("categories", {}).items()):
        retained = []
        for entry in entries:
            if slugify(entry["name"]) == journal_id:
                removed = {"id": journal_id, "name": entry["name"], "group": group}
            else:
                retained.append(entry)
        config["categories"][group] = retained
    if not removed:
        raise ValueError("未找到要删除的期刊")
    config["config_updated_at"] = iso_now()
    write_json(CONFIG_PATH, config)
    return removed


def remove_journal_data(journal_id: str) -> list[str]:
    removed_article_ids: list[str] = []
    for path in sorted((DATA_DIR / "days").glob("????-??-??.json")):
        payload = read_json(path, {})
        articles = payload.get("articles", [])
        retained = []
        for article in articles:
            if article.get("journal_id") == journal_id:
                if article.get("id"):
                    removed_article_ids.append(article["id"])
            else:
                retained.append(article)
        if len(retained) != len(articles):
            payload["articles"] = retained
            payload["generated_at"] = iso_now()
            write_json(path, payload)

    supplements_path = DATA_DIR / "supplements.json"
    supplements = read_json(supplements_path, {})
    for bucket in ("late_additions", "date_pending"):
        retained = []
        for article in supplements.get(bucket, []):
            if article.get("journal_id") == journal_id:
                if article.get("id"):
                    removed_article_ids.append(article["id"])
            else:
                retained.append(article)
        supplements[bucket] = retained
    supplements["updated_at"] = iso_now()
    write_json(supplements_path, supplements)

    statuses_path = DATA_DIR / "collection-status.json"
    statuses = read_json(statuses_path, {})
    statuses.pop(journal_id, None)
    write_json(statuses_path, statuses)
    return list(dict.fromkeys(removed_article_ids))


def refresh_manifest() -> None:
    manifest_path = DATA_DIR / "manifest.json"
    manifest = read_json(manifest_path, {})
    journals = read_json(DATA_DIR / "journals.json", {}).get("journals", [])
    supplements = read_json(DATA_DIR / "supplements.json", {})
    statuses = read_json(DATA_DIR / "collection-status.json", {})
    manifest["journal_count"] = len(journals)
    manifest["late_addition_count"] = len(supplements.get("late_additions", []))
    manifest["date_pending_count"] = len(supplements.get("date_pending", []))
    manifest["collection_summary"] = {
        "ok": sum(1 for value in statuses.values() if value.get("status") == "ok"),
        "failed": sum(1 for value in statuses.values() if value.get("status") == "failed"),
    }
    manifest["updated_at"] = iso_now()
    write_json(manifest_path, manifest)
