#!/usr/bin/env python3
from __future__ import annotations

import html
import os
import re
import urllib.error
import urllib.request
from typing import Any

from common import DATA_DIR, iso_now, read_json, write_json

MAILTO = os.environ.get("CROSSREF_MAILTO", "").strip()
USER_AGENT = (
    f"Mozilla/5.0 (compatible; PaperTracker/1.0; +mailto:{MAILTO})"
    if MAILTO
    else "Mozilla/5.0 (compatible; PaperTracker/1.0)"
)
LABEL_PATTERNS = (
    re.compile(
        r"(?:Journal\s+Impact\s+Factor|Impact\s+Factor)\s*(?:\([^)]*(20\d{2})[^)]*\))?\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
        re.I,
    ),
    re.compile(
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:</?[^>]+>\s*){0,4}(?:Journal\s+Impact\s+Factor|Impact\s+Factor)",
        re.I,
    ),
)


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def parse_metric(document: str) -> tuple[float, int | None] | None:
    simplified = html.unescape(document)
    for pattern in LABEL_PATTERNS:
        match = pattern.search(simplified)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 2:
            year_raw, value_raw = groups
        else:
            year_raw, value_raw = None, groups[0]
        value = float(value_raw)
        if 0 < value < 100:
            return value, int(year_raw) if year_raw else None
    return None


def update_journal(journal: dict[str, Any]) -> None:
    metric = journal["impact_factor"]
    metric["checked_at"] = iso_now()
    try:
        document = fetch(metric.get("source_url") or journal["url"])
        parsed = parse_metric(document)
    except (urllib.error.URLError, TimeoutError, UnicodeError) as exc:
        metric["status"] = "check_failed"
        metric["error"] = str(exc)
        return
    if not parsed:
        metric["status"] = "not_found"
        metric["error"] = "No unambiguous Journal Impact Factor label found"
        return
    value, year = parsed
    if year is None:
        metric["status"] = "year_unverified"
        metric["candidate_value"] = value
        metric["error"] = "Metric value found without an explicit year; existing value retained"
        return
    metric.update({"value": value, "year": year, "status": "verified", "error": None})
    metric.pop("candidate_value", None)


def main() -> int:
    path = DATA_DIR / "journals.json"
    payload = read_json(path)
    if not payload:
        raise SystemExit("data/journals.json is missing")
    for journal in payload["journals"]:
        update_journal(journal)
        print(f"{journal['name']}: {journal['impact_factor']['status']}")
    payload["metrics_checked_at"] = iso_now()
    write_json(path, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
