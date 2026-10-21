#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import http.client
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common import (
    BEIJING,
    DATA_DIR,
    DAYS_DIR,
    article_key,
    clean_text,
    ensure_publication_date,
    iso_now,
    iter_day_files,
    normalize_doi,
    now_beijing,
    parse_date_parts,
    read_json,
    retention_start,
    title_cache_key,
    translation_engine,
    write_json,
)
from excluded_papers import load_excluded, save_excluded_records
from llm_settings import configured_llm_model
from title_filter import TitleFilter

API = "https://api.crossref.org"
MAILTO = os.environ.get("CROSSREF_MAILTO", "").strip()
USER_AGENT = f"PaperTracker/1.0 (mailto:{MAILTO})" if MAILTO else "PaperTracker/1.0"
EXCLUDED_TITLE_PATTERNS = {
    "correction": re.compile(r"^(correction|corrigendum|erratum|publisher correction)\b", re.I),
    "retraction": re.compile(r"^(retraction|retracted|withdrawal notice)\b", re.I),
    "editorial": re.compile(r"^(editorial|editor's note|introduction to the special issue)\b", re.I),
}


class CrossrefClient:
    def __init__(self, delay: float = 0.12, retries: int = 4) -> None:
        self.delay = delay
        self.retries = retries

    def get(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        params = {**params, **({"mailto": MAILTO} if MAILTO else {})}
        url = f"{API}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    payload = json.load(response)
                time.sleep(self.delay)
                return payload["message"]
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                http.client.RemoteDisconnected,
                ConnectionResetError,
                TimeoutError,
                json.JSONDecodeError,
            ) as exc:
                if attempt + 1 == self.retries:
                    raise RuntimeError(f"Crossref request failed: {url}: {exc}") from exc
                time.sleep(2**attempt)
        raise AssertionError("unreachable")

    def works(self, issn: str, filters: list[str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor = "*"
        while cursor:
            message = self.get(
                f"/journals/{urllib.parse.quote(issn)}/works",
                {
                    "filter": ",".join(filters),
                    "rows": 500,
                    "cursor": cursor,
                    "select": (
                        "DOI,title,URL,published-online,published-print,published,created,indexed,"
                        "type,resource,container-title"
                    ),
                },
            )
            batch = message.get("items", [])
            items.extend(batch)
            next_cursor = message.get("next-cursor")
            # Crossref's documented end condition avoids an extra empty-page
            # request for every short result set (especially 12-hour updates).
            if len(batch) < 500 or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return items


def publication_date(item: dict[str, Any]) -> tuple[str | None, str, str]:
    partial = None
    for field in ("published-online", "published", "published-print"):
        value, precision = parse_date_parts(item.get(field))
        # Online publication remains authoritative even if only its month is
        # known; a future print-issue date must not hide an online-first paper.
        if value or precision not in {"missing", "invalid"}:
            return value, precision, field
        if partial is None and precision != "missing":
            partial = (None, precision, field)
    return partial or (None, "missing", "none")


def classify_title(title: str) -> str:
    for category, pattern in EXCLUDED_TITLE_PATTERNS.items():
        if pattern.search(title):
            return category
    return "article"


def article_from_item(item: dict[str, Any], journal: dict[str, Any], discovered_at: str) -> dict[str, Any] | None:
    title = clean_text((item.get("title") or [""])[0])
    if not title:
        return None
    published_date, precision, source = publication_date(item)
    doi = normalize_doi(item.get("DOI"))
    resource_url = ((item.get("resource") or {}).get("primary") or {}).get("URL")
    article = {
        "id": doi or title_cache_key(f"{journal['id']}\n{title}\n{item.get('URL', '')}"),
        "doi": doi or None,
        "journal_id": journal["id"],
        "title_en": title,
        "title_zh": None,
        "translation_status": "pending",
        "translation_engine": None,
        "url": resource_url or (f"https://doi.org/{doi}" if doi else item.get("URL")) or journal["url"],
        "published_date": published_date,
        "date_precision": precision,
        "date_source": source,
        "raw_publication_date": item.get(source),
        "first_discovered_at": discovered_at,
        "last_seen_at": discovered_at,
        "content_type": classify_title(title),
        "crossref_type": item.get("type"),
    }
    reference = datetime.fromisoformat(discovered_at.replace("Z", "+00:00"))
    if reference.tzinfo is not None:
        reference = reference.astimezone(BEIJING)
    return ensure_publication_date(article, reference.date())


def migrate_legacy_records(reference: date) -> int:
    """Move the old supplement buckets into ordinary day files, idempotently."""
    path = DATA_DIR / "supplements.json"
    legacy = read_json(path, {})
    records = [item for bucket in ("late_additions", "date_pending") for item in legacy.get(bucket, [])]
    if not records:
        return 0
    dated = {}
    for day_path in iter_day_files():
        for article in read_json(day_path, {}).get("articles", []):
            dated[article_key(article)] = article
    additions = {}
    for record in records:
        key = article_key(record)
        article = {**record, **dated.get(key, {})}
        if not article.get("title_zh") and record.get("title_zh"):
            article["title_zh"] = record["title_zh"]
            article["translation_status"] = record.get("translation_status", "translated")
            article["translation_engine"] = record.get("translation_engine")
        ensure_publication_date(article, reference)
        additions.setdefault(article["published_date"], {})[key] = article
    for day, incoming in additions.items():
        day_path = DAYS_DIR / f"{day}.json"
        current = read_json(day_path, {})
        merged = {article_key(item): item for item in current.get("articles", [])}
        merged.update(incoming)
        write_json(day_path, day_payload(day, list(merged.values()), current.get("journal_status", {})))
    # Empty compatibility file for older maintenance tools; no new records enter it.
    write_json(path, {"updated_at": iso_now(), "late_additions": [], "date_pending": []})
    return len(records)


def load_existing() -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    articles: dict[str, dict[str, Any]] = {}
    locations: dict[str, set[str]] = {}
    for path in iter_day_files():
        day = read_json(path, {})
        for article in day.get("articles", []):
            key = article_key(article)
            articles[key] = article
            locations.setdefault(key, set()).add(path.stem)
    return articles, locations


def day_payload(day: str, articles: list[dict[str, Any]], journal_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": day,
        "timezone": "Asia/Shanghai",
        "generated_at": iso_now(),
        "articles": sorted(articles, key=lambda item: (item["journal_id"], item["title_en"].lower())),
        "journal_status": journal_status,
    }


def save_manifest(journals: list[dict[str, Any]], target: date, collected: bool = True) -> None:
    available = [path.stem for path in iter_day_files()]
    statuses = read_json(DATA_DIR / "collection-status.json", {})
    previous = read_json(DATA_DIR / "manifest.json", {})
    payload = {
        "schema_version": 2,
        "date_policy": "current-month-start",
        "update_interval_hours": 12,
        "collection_checked_at": iso_now() if collected else previous.get("collection_checked_at"),
        "timezone": "Asia/Shanghai",
        "default_date": target.isoformat(),
        "updated_at": iso_now(),
        "retention": {"start": retention_start(target).isoformat(), "end": target.isoformat()},
        "available_dates": available,
        "journal_count": len(journals),
        "collection_summary": {
            "ok": sum(1 for value in statuses.values() if value.get("status") == "ok"),
            "failed": sum(1 for value in statuses.values() if value.get("status") == "failed"),
        },
    }
    write_json(DATA_DIR / "manifest.json", payload)


def prune(reference: date) -> None:
    cutoff = retention_start(reference)
    for path in iter_day_files():
        if date.fromisoformat(path.stem) < cutoff:
            path.unlink()
    prune_translation_cache()


def prune_translation_cache() -> int:
    translations_path = DATA_DIR / "translations.json"
    translations = read_json(translations_path, {})
    entries = translations.get("entries") if isinstance(translations, dict) else None
    if not isinstance(entries, dict):
        return 0

    active_titles = set()
    for path in iter_day_files():
        payload = read_json(path, {})
        active_titles.update(
            article.get("title_en")
            for article in payload.get("articles", [])
            if article.get("title_en")
        )
    retained = {
        key: entry
        for key, entry in entries.items()
        if isinstance(entry, dict) and entry.get("source") in active_titles
    }
    removed = len(entries) - len(retained)
    if removed:
        translations["entries"] = retained
        translations["updated_at"] = iso_now()
        write_json(translations_path, translations)
    return removed


def gather_for_journal(
    client: CrossrefClient, journal: dict[str, Any], mode: str, target: date
) -> tuple[list[dict[str, Any]], str]:
    # Recheck the complete publication window on every 12-hour run. The index
    # query also catches newly deposited works with missing publication dates.
    cutoff = retention_start(target)
    indexed_from = cutoff
    if mode != "backfill":
        status = read_json(DATA_DIR / "collection-status.json", {}).get(journal.get("id"), {})
        checked = status.get("last_success_at") or (status.get("checked_at") if status.get("status") == "ok" else None)
        try:
            parsed = datetime.fromisoformat(checked.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(BEIJING)
            indexed_from = max(cutoff, min(target, parsed.date()) - timedelta(days=1))
        except (TypeError, ValueError, AttributeError):
            pass
    filter_sets = [
        [f"from-pub-date:{(cutoff - timedelta(days=1)).isoformat()}",
         f"until-pub-date:{target.isoformat()}", "type:journal-article"],
        [f"from-index-date:{indexed_from.isoformat()}",
         f"until-index-date:{target.isoformat()}", "type:journal-article"],
    ]
    errors = []
    successful_issns = []
    merged: dict[str, dict[str, Any]] = {}
    for issn in journal["issns"]:
        try:
            for filters in filter_sets:
                for item in client.works(issn, filters):
                    doi = normalize_doi(item.get("DOI"))
                    key = doi or json.dumps(item.get("title", []), ensure_ascii=False)
                    merged[key] = item
            successful_issns.append(issn)
        except RuntimeError as exc:
            errors.append(str(exc))
    if successful_issns:
        return list(merged.values()), ",".join(successful_issns)
    raise RuntimeError("; ".join(errors))


def collect(mode: str, target: date, journal_id: str | None = None) -> int:
    journal_data = read_json(DATA_DIR / "journals.json")
    if not journal_data:
        raise SystemExit("data/journals.json is missing; run scripts/sync_cas.py first")
    all_journals = journal_data["journals"]
    journals = [journal for journal in all_journals if not journal_id or journal["id"] == journal_id]
    if journal_id and not journals:
        raise SystemExit(f"Unknown journal id: {journal_id}")
    migrate_legacy_records(now_beijing().date())
    existing, locations = load_existing()
    statuses = read_json(DATA_DIR / "collection-status.json", {})
    translations = read_json(DATA_DIR / "translations.json", {"model": None, "entries": {}})
    translation_entries = translations.get("entries", {})
    active_translation_engine = translation_engine(configured_llm_model())
    discovered_at = iso_now()
    client = CrossrefClient()
    changed_days: dict[str, dict[str, dict[str, Any]]] = {}
    removed_from_days: dict[str, set[str]] = {}
    excluded_records: list[dict[str, Any]] = []
    title_filter = TitleFilter(DATA_DIR, target)

    for journal in journals:
        try:
            items, used_issn = gather_for_journal(client, journal, mode, target)
            statuses[journal["id"]] = {
                "status": "ok",
                "checked_at": discovered_at,
                "last_success_at": discovered_at,
                "issn": used_issn,
                "items_seen": len(items),
                "error": None,
            }
        except RuntimeError as exc:
            statuses[journal["id"]] = {
                "last_success_at": statuses.get(journal["id"], {}).get("last_success_at"),
                "status": "failed",
                "checked_at": discovered_at,
                "error": str(exc),
            }
            print(f"WARN {journal['name']}: {exc}")
            continue

        candidates = []
        for item in items:
            article = article_from_item(item, journal, discovered_at)
            if not article:
                continue
            candidates.append(article)

        filter_stats = {
            "enabled": title_filter.enabled_for(journal["id"]),
            "new_candidates": 0,
            "classified": 0,
            "cached": 0,
            "excluded": 0,
            "biomechanics_kept": 0,
            "failed_open": 0,
            "skipped_unconfigured": 0,
        }
        if filter_stats["enabled"]:
            new_candidates = [
                article for article in candidates if article_key(article) not in existing
            ]
            filter_stats["new_candidates"] = len(new_candidates)
            retained_new, excluded_new, classification_stats = title_filter.classify_articles(
                new_candidates, journal["id"]
            )
            excluded_records.extend(excluded_new)
            retained_keys = {article_key(article) for article in retained_new}
            candidates = [
                article
                for article in candidates
                if article_key(article) in existing or article_key(article) in retained_keys
            ]
            filter_stats.update(classification_stats)
        statuses[journal["id"]]["title_filter"] = filter_stats

        for article in candidates:
            key = article_key(article)
            cached = translation_entries.get(
                title_cache_key(article["title_en"], active_translation_engine)
            )
            if cached:
                article["title_zh"] = cached["translation"]
                article["translation_status"] = "translated"
                article["translation_engine"] = cached.get("engine") or cached.get("model")
            previous_locations = set(locations.get(key, set()))
            if key in existing:
                old = existing[key]
                article["first_discovered_at"] = old.get("first_discovered_at") or old.get("first_seen_at") or discovered_at
                article["title_zh"] = old.get("title_zh") or article["title_zh"]
                article["translation_status"] = old.get("translation_status", article["translation_status"])
                article["translation_engine"] = old.get("translation_engine") or article["translation_engine"]
                # Missing metadata must never shift a previously stored date,
                # including an inferred date, on a later month or retry.
                if article.get("date_inferred") and old.get("published_date"):
                    for field in ("published_date", "date_precision", "date_source", "raw_publication_date"):
                        if field in old:
                            article[field] = old[field]
                    article["date_inferred"] = bool(old.get("date_inferred"))
                    if old.get("date_inference"):
                        article["date_inference"] = old["date_inference"]
                    else:
                        article.pop("date_inference", None)
            existing[key] = article
            for old_location in previous_locations:
                if old_location != article["published_date"]:
                    removed_from_days.setdefault(old_location, set()).add(key)
            published = date.fromisoformat(article["published_date"])
            if published < retention_start(target) or published > target:
                # A corrected date outside the window removes the old placement.
                if article["published_date"] in previous_locations:
                    removed_from_days.setdefault(article["published_date"], set()).add(key)
                locations[key] = set()
                continue
            changed_days.setdefault(article["published_date"], {})[key] = article
            locations[key] = {article["published_date"]}

    # Clean against the complete retained ledger, including exclusions from
    # older runs and exclusions discovered during this collection.
    excluded_keys = set(load_excluded(DATA_DIR).get("entries", {}))
    excluded_keys.update(article_key(record) for record in excluded_records)
    if excluded_keys:
        for key in excluded_keys:
            for location in locations.get(key, set()):
                removed_from_days.setdefault(location, set()).add(key)

        for day, additions in changed_days.items():
            for key in excluded_keys:
                additions.pop(key, None)
    for day in sorted(set(changed_days) | set(removed_from_days)):
        additions = changed_days.get(day, {})
        path = DAYS_DIR / f"{day}.json"
        current = read_json(path, day_payload(day, [], {}))
        removals = removed_from_days.get(day, set())
        merged = {
            article_key(item): item
            for item in current.get("articles", [])
            if article_key(item) not in removals
        }
        merged.update(additions)
        journal_status = statuses if day == target.isoformat() else current.get("journal_status", {})
        write_json(path, day_payload(day, list(merged.values()), journal_status))

    # Keep an explicit empty file for a checked day so the calendar can
    # distinguish "no papers" from "data was never generated".
    if mode == "daily":
        target_path = DAYS_DIR / f"{target.isoformat()}.json"
        if not target_path.exists():
            write_json(target_path, day_payload(target.isoformat(), [], statuses))

    write_json(DATA_DIR / "collection-status.json", statuses)
    title_filter.prune_and_save()
    save_excluded_records(DATA_DIR, excluded_records, target)
    prune(target)
    save_manifest(all_journals, target, collected=journal_id is None)
    print(f"Collection complete: mode={mode}, target={target.isoformat()}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("daily", "backfill"), default="daily")
    parser.add_argument("--date", help="Business date in YYYY-MM-DD; defaults to today in Beijing")
    parser.add_argument("--journal-id", help="Only collect one journal; used after adding a journal")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    default_target = now_beijing().date()
    selected = date.fromisoformat(args.date) if args.date else default_target
    raise SystemExit(collect(args.mode, selected, args.journal_id))
