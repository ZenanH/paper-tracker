#!/usr/bin/env python3
"""Verify the bundled ShowJCR snapshots offline; never modify input files."""

import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def issns(value):
    return set(re.findall(r"\b\d{4}-\d{3}[\dX]\b", value.upper()))


def main():
    manifest = json.loads((ROOT / "data/journals/manifest.json").read_text())
    tables = {}
    for spec in manifest["files"]:
        raw = (ROOT / spec["path"]).read_bytes()
        require(len(raw) == spec["bytes"], f"size: {spec['path']}")
        require(hashlib.sha256(raw).hexdigest() == spec["sha256"],
                f"SHA-256: {spec['path']}")
        blob = b"blob " + str(len(raw)).encode() + b"\0" + raw
        require(hashlib.sha1(blob).hexdigest() == spec["upstream_blob_sha"],
                f"Git blob: {spec['path']}")
        parsed = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        require(parsed[0] == spec["columns"], f"header: {spec['path']}")
        require(len(parsed) - 1 == spec["records"], f"count: {spec['path']}")
        require(all(len(row) == len(parsed[0]) for row in parsed[1:]),
                f"row width: {spec['path']}")
        tables[Path(spec["path"]).name] = [
            dict(zip(parsed[0], row)) for row in parsed[1:]
        ]

    cas = tables["FQBJCR2025-UTF8.csv"]
    jcr = tables["JCR2025-UTF8.csv"]
    expected = manifest["audit"]
    require(all(row["年份"] == str(manifest["cas_edition"]) for row in cas),
            "CAS edition")
    require(sum(row["Top"] == "是" for row in cas) == expected["cas_top_true"],
            "CAS Top count")
    require(set(row["Top"] for row in cas) <= {"是", "否"}, "Top vocabulary")
    zones = Counter(row["大类分区"].split()[0] for row in cas)
    require(dict(zones) == expected["cas_zones"], "CAS zone counts")
    column = f"IF({manifest['jif_metric_year']})"
    values = Counter(row[column] for row in jcr)
    require(values["<0.1"] == expected["jif_less_than_0_1"], "IF <0.1 count")
    require(values["N/A"] == expected["jif_not_available"], "IF N/A count")

    index = defaultdict(set)
    for number, row in enumerate(jcr):
        for issn in issns(row["ISSN"] + "/" + row["EISSN"]):
            index[issn].add(number)
    matches = Counter()
    for row in cas:
        hits = set()
        for issn in issns(row["ISSN/EISSN"]):
            hits.update(index[issn])
        matches["unique" if len(hits) == 1 else "ambiguous" if hits else "unmatched"] += 1
    for kind in ("unique", "unmatched", "ambiguous"):
        field = "cas_to_jcr_unique_issn_matches" if kind == "unique" else f"cas_to_jcr_{kind}"
        require(matches[kind] == expected[field], f"ISSN join: {kind}")

    examples = {"NATURE": "56.1", "SCIENCE": "47.3",
                "IEEE TRANSACTIONS ON PATTERN ANALYSIS AND MACHINE INTELLIGENCE": "20.4",
                "NATURE COMMUNICATIONS": "18.1"}
    for name, value in examples.items():
        rows = [row for row in jcr if row["Journal"].upper() == name]
        require(len(rows) == 1 and rows[0][column] == value, f"IF example: {name}")
    print(f"PASS: {len(cas):,} CAS records; {len(jcr):,} IF records; hashes, schema, statistics and ISSN joins verified.")


if __name__ == "__main__":
    main()
