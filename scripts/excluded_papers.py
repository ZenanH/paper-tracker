from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from common import article_key, ensure_publication_date, iso_now, now_beijing, read_json, retention_start, write_json


def excluded_path(data_dir: Path) -> Path:
    return data_dir / "excluded-papers.json"


def load_excluded(data_dir: Path) -> dict[str, Any]:
    payload = read_json(excluded_path(data_dir), {})
    if not isinstance(payload, dict) or not isinstance(payload.get("entries", {}), dict):
        return {"updated_at": None, "entries": {}}
    payload.setdefault("entries", {})
    return payload


def _record_date(record: dict[str, Any]) -> date | None:
    published = record.get("published_date")
    if published:
        try:
            return date.fromisoformat(published)
        except (TypeError, ValueError):
            return None
    discovered = record.get("first_discovered_at") or record.get("excluded_at")
    try:
        return datetime.fromisoformat(discovered).date()
    except (TypeError, ValueError):
        return None


def save_excluded_records(
    data_dir: Path, records: list[dict[str, Any]], reference: date
) -> None:
    payload = load_excluded(data_dir)
    entries = payload["entries"]
    for record in records:
        entries[article_key(record)] = record
    cutoff = retention_start(reference)
    retained = {}
    for key, record in entries.items():
        record_date = _record_date(record)
        if record_date is not None and record_date >= cutoff:
            retained[key] = record
    payload["entries"] = retained
    payload["updated_at"] = iso_now()
    write_json(excluded_path(data_dir), payload)


def remove_excluded_journal(data_dir: Path, journal_id: str) -> None:
    payload = load_excluded(data_dir)
    retained = {
        key: record
        for key, record in payload["entries"].items()
        if record.get("journal_id") != journal_id
    }
    if len(retained) == len(payload["entries"]):
        return
    payload["entries"] = retained
    payload["updated_at"] = iso_now()
    write_json(excluded_path(data_dir), payload)


def public_excluded_records(data_dir: Path) -> list[dict[str, Any]]:
    payload = load_excluded(data_dir)
    records = []
    for key, record in payload["entries"].items():
        if not isinstance(record, dict):
            continue
        records.append({"filter_id": key, **record})
    return sorted(records, key=lambda item: item.get("excluded_at", ""), reverse=True)


def _clean_article(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if not key.startswith("filter_") and key not in {"excluded_at", "filter_id"}
    }


def restore_excluded_records(data_dir: Path, filter_ids: Any) -> dict[str, Any]:
    if not isinstance(filter_ids, list) or any(not isinstance(item, str) for item in filter_ids):
        raise ValueError("Restore IDs must be an array of strings.")
    selected = list(dict.fromkeys(filter_ids))
    payload = load_excluded(data_dir)
    entries = payload["entries"]
    today = now_beijing().date()
    window_start = retention_start(today)
    window_end = today
    journal_ids = {
        journal["id"]
        for journal in read_json(data_dir / "journals.json", {}).get("journals", [])
    }
    restored = []
    skipped = []
    changed_days: dict[str, dict[str, dict[str, Any]]] = {}

    for filter_id in selected:
        record = entries.get(filter_id)
        if not isinstance(record, dict) or record.get("journal_id") not in journal_ids:
            skipped.append(filter_id)
            continue
        article = ensure_publication_date(_clean_article(record), today)
        published = article.get("published_date")
        try:
            published_date = date.fromisoformat(published)
        except (TypeError, ValueError):
            skipped.append(filter_id)
            continue
        if not window_start <= published_date <= window_end:
            skipped.append(filter_id)
            continue
        changed_days.setdefault(published, {})[article_key(article)] = article
        restored.append(filter_id)

    for day, additions in changed_days.items():
        path = data_dir / "days" / f"{day}.json"
        current = read_json(
            path,
            {
                "date": day,
                "timezone": "Asia/Shanghai",
                "generated_at": iso_now(),
                "articles": [],
                "journal_status": {},
            },
        )
        merged = {article_key(item): item for item in current.get("articles", [])}
        merged.update(additions)
        current["articles"] = sorted(
            merged.values(), key=lambda item: (item["journal_id"], item["title_en"].lower())
        )
        current["generated_at"] = iso_now()
        write_json(path, current)

    if restored:
        for filter_id in restored:
            entries.pop(filter_id, None)
        payload["updated_at"] = iso_now()
        write_json(excluded_path(data_dir), payload)
    return {"restored": restored, "skipped": skipped}
