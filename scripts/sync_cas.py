#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from common import DATA_DIR, ROOT, file_sha256, git_blob_sha, iso_now, slugify, write_json

COMMIT = "2557e1856ba9f8eb945f781deeda1e5fb58ace88"
EXPECTED_BLOB = "5918c4ee712878e2b6bc2e5d50f7a87b3c67a719"
SOURCE_PATH = "中科院分区表及JCR原始数据文件/FQBJCR2025-UTF8.csv"
SOURCE_URL = (
    "https://raw.githubusercontent.com/hitfyd/ShowJCR/"
    f"{COMMIT}/{urllib.parse.quote(SOURCE_PATH)}"
)


def normalized(value: str) -> str:
    return "".join(character for character in value.lower().replace("&", "and") if character.isalnum())


def load_source(source_file: Path | None) -> bytes:
    if source_file:
        return source_file.read_bytes()
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "paper-tracker/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main() -> int:
    source_file = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    content = load_source(source_file)
    actual_blob = git_blob_sha(content)
    if actual_blob != EXPECTED_BLOB:
        raise SystemExit(f"ShowJCR blob mismatch: expected {EXPECTED_BLOB}, got {actual_blob}")

    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    by_name = {normalized(row["Journal"]): row for row in rows}
    aliases = {
        normalized("Future Generation Computer Systems"): normalized(
            "Future Generation Computer Systems-The International Journal of eScience"
        )
    }
    config = json.loads((ROOT / "journal.json").read_text(encoding="utf-8"))
    journals = []
    missing = []
    for group, entries in config["categories"].items():
        for entry in entries:
            key = normalized(entry["name"])
            row = by_name.get(key) or by_name.get(aliases.get(key, ""))
            if not row:
                missing.append(entry["name"])
                continue
            major_zone = row["大类分区"].split()[0]
            subjects = []
            for number in range(1, 7):
                subject = row.get(f"小类{number}", "").strip()
                zone = row.get(f"小类{number}分区", "").strip()
                if subject:
                    subjects.append({"name": subject, "zone": zone})
            issns = [part.strip() for part in row["ISSN/EISSN"].split("/") if part.strip()]
            journals.append(
                {
                    "id": slugify(entry["name"]),
                    "name": entry["name"],
                    "source_name": row["Journal"],
                    "group": group,
                    "url": entry["url"],
                    "issns": list(dict.fromkeys(issns)),
                    "cas": {
                        "version": "2025升级版",
                        "category": row["大类"],
                        "zone": int(major_zone),
                        "rank": row["大类分区"],
                        "top": row["Top"] == "是",
                        "subjects": subjects,
                    },
                    "impact_factor": {
                        "value": entry.get("if"),
                        "year": entry.get("if_year"),
                        "status": "seeded_unverified",
                        "source_url": entry["url"],
                        "checked_at": None,
                    },
                }
            )
    if missing:
        raise SystemExit("Missing ShowJCR records: " + ", ".join(missing))

    payload = {
        "generated_at": iso_now(),
        "source": {
            "label": "中科院期刊分区表 2025 升级版，数据整理来源 ShowJCR",
            "repository": "https://github.com/hitfyd/ShowJCR",
            "commit": COMMIT,
            "path": SOURCE_PATH,
            "git_blob_sha": EXPECTED_BLOB,
            "sha256": file_sha256(content),
        },
        "journals": journals,
    }
    write_json(DATA_DIR / "journals.json", payload)
    print(f"Wrote {len(journals)} journals to data/journals.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
