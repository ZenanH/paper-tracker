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
            if not batch or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return items


def publication_date(item: dict[str, Any]) -> tuple[str | None, str, str]:
    for field in ("published-online", "published", "published-print"):
        value, precision = parse_date_parts(item.get(field))
        if value or precision not in {"missing", "invalid"}:
            return value, precision, field
    return None, "missing", "none"


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
        "first_discovered_at": discovered_at,
        "last_seen_at": discovered_at,
        "content_type": classify_title(title),
        "crossref_type": item.get("type"),
    }
    return article


def load_existing() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    articles: dict[str, dict[str, Any]] = {}
    locations: dict[str, str] = {}
    for path in iter_day_files():
        day = read_json(path, {})
        for article in day.get("articles", []):
            key = article_key(article)
            articles[key] = article
            locations[key] = path.stem
    supplements = read_json(DATA_DIR / "supplements.json", {"late_additions": [], "date_pending": []})
    for bucket in ("late_additions", "date_pending"):
        for article in supplements.get(bucket, []):
            key = article_key(article)
            articles[key] = article
            locations[key] = bucket
    return articles, locations


def day_payload(day: str, articles: list[dict[str, Any]], journal_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": day,
        "timezone": "Asia/Shanghai",
        "generated_at": iso_now(),
        "articles": sorted(articles, key=lambda item: (item["journal_id"], item["title_en"].lower())),
        "journal_status": journal_status,
    }


def save_manifest(journals: list[dict[str, Any]], target: date) -> None:
    available = [path.stem for path in iter_day_files()]
    supplement = read_json(DATA_DIR / "supplements.json", {"late_additions": [], "date_pending": []})
    statuses = read_json(DATA_DIR / "collection-status.json", {})
    payload = {
        "timezone": "Asia/Shanghai",
        "default_date": target.isoformat(),
        "updated_at": iso_now(),
        "retention": {"start": retention_start(target).isoformat(), "end": target.isoformat()},
        "available_dates": available,
        "journal_count": len(journals),
        "late_addition_count": len(supplement.get("late_additions", [])),
        "date_pending_count": len(supplement.get("date_pending", [])),
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
    supplements = read_json(DATA_DIR / "supplements.json", {"late_additions": [], "date_pending": []})
    supplements["late_additions"] = [
        item
        for item in supplements.get("late_additions", [])
        if item.get("published_date") and date.fromisoformat(item["published_date"]) >= cutoff
    ]
    supplements["date_pending"] = [
        item
        for item in supplements.get("date_pending", [])
        if datetime.fromisoformat(item["first_discovered_at"]).date() >= cutoff
    ]
    supplements["updated_at"] = iso_now()
    write_json(DATA_DIR / "supplements.json", supplements)


def gather_for_journal(
    client: CrossrefClient, journal: dict[str, Any], mode: str, target: date, include_supplements: bool = True
) -> tuple[list[dict[str, Any]], str]:
    if mode == "backfill":
        filter_sets = [[
                f"from-pub-date:{retention_start(target).isoformat()}",
                f"until-pub-date:{target.isoformat()}",
                "type:journal-article",
            ]]
    else:
        # The primary query is exactly the previous business day. A separate,
        # tightly bounded index query discovers late deposits without widening
        # the daily page's publication range.
        filter_sets = [[
                # Crossref filters are date-granular. Include the preceding
                # UTC date so timestamps near Beijing midnight can be
                # converted and filtered correctly below.
                f"from-pub-date:{(target - timedelta(days=1)).isoformat()}",
                f"until-pub-date:{target.isoformat()}",
                "type:journal-article",
            ]]
        if include_supplements:
            filter_sets.append([
                f"from-index-date:{(target - timedelta(days=1)).isoformat()}",
                f"until-index-date:{target.isoformat()}",
                f"from-pub-date:{retention_start(target).isoformat()}",
                f"until-pub-date:{(target - timedelta(days=1)).isoformat()}",
                "type:journal-article",
            ])
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


def collect(mode: str, target: date) -> int:
    journal_data = read_json(DATA_DIR / "journals.json")
    if not journal_data:
        raise SystemExit("data/journals.json is missing; run scripts/sync_cas.py first")
    journals = journal_data["journals"]
    existing, locations = load_existing()
    statuses = read_json(DATA_DIR / "collection-status.json", {})
    initializing = not bool(statuses)
    supplements = read_json(DATA_DIR / "supplements.json", {"late_additions": [], "date_pending": []})
    translations = read_json(DATA_DIR / "translations.json", {"model": None, "entries": {}})
    translation_entries = translations.get("entries", {})
    discovered_at = iso_now()
    client = CrossrefClient()
    changed_days: dict[str, dict[str, dict[str, Any]]] = {}

    for journal in journals:
        try:
            items, used_issn = gather_for_journal(
                client, journal, mode, target, include_supplements=not initializing
            )
            statuses[journal["id"]] = {
                "status": "ok",
                "checked_at": discovered_at,
                "issn": used_issn,
                "items_seen": len(items),
                "error": None,
            }
        except RuntimeError as exc:
            statuses[journal["id"]] = {
                "status": "failed",
                "checked_at": discovered_at,
                "error": str(exc),
            }
            print(f"WARN {journal['name']}: {exc}")
            continue

        for item in items:
            article = article_from_item(item, journal, discovered_at)
            if not article:
                continue
            key = article_key(article)
            cached = translation_entries.get(
                title_cache_key(article["title_en"], translation_engine())
            )
            if cached:
                article["title_zh"] = cached["translation"]
                article["translation_status"] = "translated"
                article["translation_engine"] = cached.get("engine") or cached.get("model")
            if key in existing:
                old = existing[key]
                article["first_discovered_at"] = old["first_discovered_at"]
                article["title_zh"] = old.get("title_zh") or article["title_zh"]
                article["translation_status"] = old.get("translation_status", article["translation_status"])
                article["translation_engine"] = old.get("translation_engine") or article["translation_engine"]
            existing[key] = article

            if article["published_date"]:
                published = date.fromisoformat(article["published_date"])
                if published < retention_start(target) or published > target:
                    continue
                changed_days.setdefault(article["published_date"], {})[key] = article
                if mode != "backfill" and not initializing and key not in locations and published < target:
                    late = {**article, "supplement_type": "late_addition", "archived_date": article["published_date"]}
                    supplements.setdefault("late_additions", []).append(late)
            else:
                if mode != "backfill" and not initializing and key not in locations:
                    pending = {**article, "supplement_type": "date_pending"}
                    supplements.setdefault("date_pending", []).append(pending)
            locations[key] = article["published_date"] or "date_pending"

    for day, additions in changed_days.items():
        path = DAYS_DIR / f"{day}.json"
        current = read_json(path, day_payload(day, [], {}))
        merged = {article_key(item): item for item in current.get("articles", [])}
        merged.update(additions)
        write_json(path, day_payload(day, list(merged.values()), statuses))

    # Keep an explicit empty file for a checked day so the calendar can
    # distinguish "no papers" from "data was never generated".
    if mode == "daily":
        target_path = DAYS_DIR / f"{target.isoformat()}.json"
        if not target_path.exists():
            write_json(target_path, day_payload(target.isoformat(), [], statuses))

    for bucket in ("late_additions", "date_pending"):
        deduped = {article_key(item): item for item in supplements.get(bucket, [])}
        supplements[bucket] = sorted(
            deduped.values(), key=lambda item: item.get("first_discovered_at", ""), reverse=True
        )
    supplements["updated_at"] = iso_now()
    write_json(DATA_DIR / "supplements.json", supplements)
    write_json(DATA_DIR / "collection-status.json", statuses)
    prune(target)
    save_manifest(journals, target)
    print(f"Collection complete: mode={mode}, target={target.isoformat()}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("daily", "backfill"), default="daily")
    parser.add_argument("--date", help="Business date in YYYY-MM-DD; defaults to yesterday in Beijing")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    default_target = now_beijing().date() - timedelta(days=1)
    selected = date.fromisoformat(args.date) if args.date else default_target
    raise SystemExit(collect(args.mode, selected))
