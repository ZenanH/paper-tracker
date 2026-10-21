#!/usr/bin/env python3
from __future__ import annotations

import html
import os
import re
import urllib.error
import urllib.request
import urllib.parse
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
    # Publishers commonly put the label, number and year in separate HTML
    # elements. Do not mistake a report/copyright year elsewhere on the page
    # for the metric year, or a five-year impact factor for the annual JIF.
    visible = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", document, flags=re.I | re.S)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", visible)))
    verified = []
    undated = []
    for label in re.finditer(r"\b(?:Journal\s+)?Impact\s+Factor\b", text, re.I):
        if re.search(r"(?:5|five)[-\s]+year\s*$", text[max(0, label.start()-20):label.start()], re.I):
            continue
        tail = text[label.end():label.end()+90]
        year_first = re.match(r"\s*\((20\d{2})\)\s*[:：]?\s*(\d+(?:\.\d+)?)\b", tail)
        value_first = re.match(r"\s*[:：]?\s*(\d+(?:\.\d+)?)\s*\((20\d{2})\)", tail)
        value_only = re.match(r"\s*[:：]?\s*(\d+(?:\.\d+)?)\b", tail)
        value_before = re.search(r"(\d+(?:\.\d+)?)\s*$", text[max(0, label.start()-30):label.start()])
        if year_first:
            year, value = int(year_first[1]), float(year_first[2])
        elif value_first:
            value, year = float(value_first[1]), int(value_first[2])
        elif value_only:
            value = float(value_only[1])
            if 0 < value < 100:
                undated.append(value)
            continue
        elif value_before:
            value = float(value_before[1])
            if 0 < value < 100:
                undated.append(value)
            continue
        else:
            continue
        if 0 < value < 100:
            verified.append((value, year))
    if verified:
        latest = max(year for _, year in verified)
        values = {value for value, year in verified if year == latest}
        return (values.pop(), latest) if len(values) == 1 else None
    return (undated[0], None) if undated and len(set(undated)) == 1 else None


def update_journal(journal: dict[str, Any]) -> None:
    metric = journal["impact_factor"]
    metric["checked_at"] = iso_now()
    source_url = metric.get("source_url") or journal["url"]
    source = urllib.parse.urlsplit(source_url)
    # Nature homepages often omit the metric year. Its dedicated metrics page
    # gives the explicitly paired annual value and year.
    if source.hostname in {"www.nature.com", "nature.com"} and len(source.path.strip("/").split("/")) == 1:
        source_url = urllib.parse.urlunsplit((source.scheme, source.netloc, source.path.rstrip("/") + "/journal-impact", "", ""))
    try:
        document = fetch(source_url)
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
    if isinstance(metric.get("year"), int) and year < metric["year"]:
        metric["status"] = "outdated_source"
        metric["error"] = "Publisher page shows an older metric year; existing value retained"
        return
    metric.update({"value": value, "year": year, "status": "verified", "error": None, "source_url": source_url})
    metric.pop("candidate_value", None)


def main(journal_id: str | None = None) -> int:
    path = DATA_DIR / "journals.json"
    payload = read_json(path)
    if not payload:
        raise SystemExit("data/journals.json is missing")
    journals = [journal for journal in payload["journals"] if not journal_id or journal["id"] == journal_id]
    if journal_id and not journals:
        raise SystemExit(f"Unknown journal id: {journal_id}")
    for journal in journals:
        update_journal(journal)
        print(f"{journal['name']}: {journal['impact_factor']['status']}")
    if journal_id is None:
        payload["metrics_checked_at"] = iso_now()
    write_json(path, payload)
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--journal-id")
    args = parser.parse_args()
    raise SystemExit(main(args.journal_id))
