#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from common import DATA_DIR, article_key, iter_day_files, read_json, retention_start


def main() -> int:
    journals = json.loads((DATA_DIR / "journals.json").read_text(encoding="utf-8"))
    journal_ids = {journal["id"] for journal in journals["journals"]}
    assert journal_ids, "At least one configured journal is required"
    assert len(journal_ids) == len(journals["journals"]), "Journal ids must be unique"
    manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    files = list(iter_day_files())
    assert manifest["available_dates"] == [path.stem for path in files]
    retention_end = date.fromisoformat(manifest["retention"]["end"])
    retention_begin = date.fromisoformat(manifest["retention"]["start"])
    assert retention_begin == retention_start(retention_end)
    assert manifest["default_date"] == retention_end.isoformat()
    assert all(retention_begin <= date.fromisoformat(path.stem) <= retention_end for path in files)
    seen = set()
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["date"] == path.stem, path
        assert isinstance(payload["articles"], list), path
        for article in payload["articles"]:
            assert article["journal_id"] in journal_ids, article
            assert article["url"].startswith(("http://", "https://")), article
            assert article.get("published_date") == path.stem, article
            key = article_key(article)
            assert key not in seen, f"Duplicate article across day files: {key}"
            seen.add(key)
    supplements = json.loads((DATA_DIR / "supplements.json").read_text(encoding="utf-8"))
    supplement_keys = {}
    for bucket in ("late_additions", "date_pending"):
        records = supplements[bucket]
        assert isinstance(records, list)
        keys = set()
        for article in records:
            assert article["journal_id"] in journal_ids, article
            assert article["url"].startswith(("http://", "https://")), article
            if bucket == "date_pending":
                assert not article.get("published_date"), article
            else:
                assert article.get("published_date"), article
            key = article_key(article)
            assert key not in keys, f"Duplicate article in {bucket}: {key}"
            keys.add(key)
        supplement_keys[bucket] = keys
    assert not (supplement_keys["late_additions"] & supplement_keys["date_pending"])
    assert manifest["late_addition_count"] == len(supplements["late_additions"])
    assert manifest["date_pending_count"] == len(supplements["date_pending"])

    filter_config = read_json(DATA_DIR / "filter-config.json", {})
    filter_ids = filter_config.get("journal_ids", [])
    assert isinstance(filter_ids, list), "filter-config journal_ids must be a list"
    assert len(filter_ids) == len(set(filter_ids)), "filter-config journal_ids must be unique"
    assert set(filter_ids) <= journal_ids, "filter-config contains unknown journal ids"
    classifications = read_json(DATA_DIR / "title-classifications.json", {"entries": {}})
    classification_entries = classifications.get("entries", {})
    assert isinstance(classification_entries, dict), "title classification entries must be an object"
    allowed_categories = {"keep", "medicine", "biology", "chemistry", "humanities", "uncertain"}
    for entry in classification_entries.values():
        assert entry.get("category") in allowed_categories, entry
        confidence = entry.get("confidence")
        assert isinstance(confidence, (int, float)) and not isinstance(confidence, bool), entry
        assert 0 <= confidence <= 1, entry
        assert set(entry.get("journal_ids", [])) <= journal_ids, entry

    excluded = read_json(DATA_DIR / "excluded-papers.json", {"entries": {}})
    excluded_entries = excluded.get("entries", {})
    assert isinstance(excluded_entries, dict), "excluded-papers entries must be an object"
    visible_keys = seen | supplement_keys["late_additions"] | supplement_keys["date_pending"]
    required_fields = {
        "id",
        "journal_id",
        "title_en",
        "url",
        "first_discovered_at",
        "filter_category",
        "filter_confidence",
        "filter_engine",
        "filter_rule_version",
        "excluded_at",
    }
    for key, article in excluded_entries.items():
        assert isinstance(article, dict), article
        assert required_fields <= set(article), article
        assert key == article_key(article), article
        assert key not in visible_keys, f"Filtered article is still visible: {key}"
        assert article["journal_id"] in journal_ids, article
        assert article["title_en"].strip(), article
        assert article["url"].startswith(("http://", "https://")), article
        assert article["filter_category"] in {
            "medicine",
            "biology",
            "chemistry",
            "humanities",
        }, article
        confidence = article["filter_confidence"]
        assert isinstance(confidence, (int, float)) and not isinstance(confidence, bool), article
        assert 0 <= confidence <= 1, article
        published = article.get("published_date")
        if published:
            record_date = date.fromisoformat(published)
        else:
            record_date = date.fromisoformat(article["first_discovered_at"][:10])
        assert retention_begin <= record_date <= retention_end, article
    print(f"Validated {len(files)} day files and {len(seen)} articles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
