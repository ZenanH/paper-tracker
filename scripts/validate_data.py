#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from common import DATA_DIR, DAYS_DIR, iter_day_files


def main() -> int:
    journals = json.loads((DATA_DIR / "journals.json").read_text(encoding="utf-8"))
    journal_ids = {journal["id"] for journal in journals["journals"]}
    assert len(journal_ids) == 32, "Expected 32 configured journals"
    manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    files = list(iter_day_files())
    assert manifest["available_dates"] == [path.stem for path in files]
    assert date.fromisoformat(manifest["retention"]["start"]) <= date.fromisoformat(manifest["retention"]["end"])
    seen = set()
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["date"] == path.stem, path
        assert isinstance(payload["articles"], list), path
        for article in payload["articles"]:
            assert article["journal_id"] in journal_ids, article
            assert article["url"].startswith(("http://", "https://")), article
            key = article.get("doi") or article["id"]
            assert key not in seen, f"Duplicate article across day files: {key}"
            seen.add(key)
    for bucket in ("late_additions", "date_pending"):
        assert isinstance(json.loads((DATA_DIR / "supplements.json").read_text(encoding="utf-8"))[bucket], list)
    print(f"Validated {len(files)} day files and {len(seen)} articles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
