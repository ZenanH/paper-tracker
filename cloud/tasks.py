"""Bounded, resumable units for the original collection/translation rules."""
from __future__ import annotations

import copy
import time
import urllib.parse
from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

from cloud import workspace as w


def _job(stage: str, control: dict) -> None:
    total = len(control.get("journal_ids", []))
    w.journal_manager.set_job(stage=stage, message="Running", completed_journals=control.get("journal_index", 0), total_journals=total)


def _journal_page(work, control: dict) -> None:
    """One source page or 64-title classify/merge batch; cursor is persisted."""
    target = date.fromisoformat(control["target"])
    journals = w.common.read_json(work.data / "journals.json")["journals"]
    identifiers = control["journal_ids"]
    if control["journal_index"] >= len(identifiers):
        control.update(phase="translate", translation_attempted=[])
        return
    identifier = identifiers[control["journal_index"]]
    journal = next(j for j in journals if j["id"] == identifier)
    state = control.get("source")
    mode = "backfill" if control["kind"] in {"add", "backfill"} else "daily"
    if state is None:
        status = w.common.read_json(work.data / "collection-status.json", {}).get(identifier, {})
        state = control["source"] = {
            "filters": w.collect.query_filters(journal, mode, target), "query": 0,
            "cursor": "*", "buffer": [], "used_issns": [], "errors": [],
            "old_status": copy.deepcopy(status), "items_seen": 0,
        }
    _job("collect", control)
    filters = state["filters"]
    queries = [(issn, values) for issn in journal["issns"] for values in filters]
    if state["buffer"]:
        batch, state["buffer"] = state["buffer"][:64], state["buffer"][64:]
        with patch.object(w.collect, "gather_for_journal", return_value=(batch, ",".join(state["used_issns"]))):
            w.collect.collect(mode, target, identifier)
        # A page commit is not a complete journal check or a success watermark.
        statuses = w.common.read_json(work.data / "collection-status.json", {})
        statuses[identifier] = copy.deepcopy(state["old_status"])
        w.common.write_json(work.data / "collection-status.json", statuses)
        return
    if state["query"] >= len(queries):
        statuses = w.common.read_json(work.data / "collection-status.json", {})
        previous = state["old_status"]
        checked = w.common.iso_now()
        if state["used_issns"]:
            statuses[identifier] = {**previous, "status": "ok", "checked_at": checked, "last_success_at": checked,
                                    "issn": ",".join(state["used_issns"]), "items_seen": state["items_seen"], "error": None}
        else:
            statuses[identifier] = {**previous, "status": "failed", "checked_at": checked,
                                    "last_success_at": previous.get("last_success_at"), "error": "Source requests failed; previous data retained."}
        w.common.write_json(work.data / "collection-status.json", statuses)
        control["journal_index"] += 1
        control.pop("source", None)
        return
    issn, values = queries[state["query"]]
    client = w.collect.CrossrefClient(retries=2, timeout=15)
    try:
        message = client.get(f"/journals/{urllib.parse.quote(issn)}/works", {
            "filter": ",".join(values), "rows": 500, "cursor": state["cursor"],
            "select": "DOI,title,URL,published-online,published-print,published,created,indexed,type,resource,container-title",
        })
        records = message.get("items")
        if not isinstance(records, list):
            raise RuntimeError("Malformed source page")
        state["buffer"] = records
        state["items_seen"] += len(records)
        next_cursor = message.get("next-cursor")
        if len(records) < 500 or not next_cursor or next_cursor == state["cursor"]:
            if state["query"] % len(filters) == len(filters) - 1:
                state["used_issns"].append(issn)
            state["query"] += 1
            state["cursor"] = "*"
        else:
            state["cursor"] = next_cursor
    except RuntimeError:
        state["errors"].append(issn)
        state["query"] = ((state["query"] // len(filters)) + 1) * len(filters)
        state["cursor"] = "*"


def _translation(work, control: dict) -> None:
    if not w.llm_settings.llm_is_configured():
        control["phase"] = "metrics-prepare"
        return
    _job("translate", control)
    engine = w.common.translation_engine(w.llm_settings.configured_llm_model())
    attempted = set(control.setdefault("translation_attempted", []))
    records = [
        (path, article) for path, article in w.translate.pending_articles(engine)
        if w.common.title_cache_key(article["title_en"], engine) not in attempted
    ]
    groups = w.translate.group_records_by_title(records, engine)
    if not groups or len(attempted) >= 2000:
        control.pop("translation_attempted", None)
        control["phase"] = "metrics-prepare"
        return
    selected = groups[:min(8, 2000 - len(attempted))]
    selected_records = [record for _, matched in selected for record in matched]
    with patch.object(w.translate, "pending_articles", return_value=selected_records):
        w.translate.main(limit=len(selected), batch_size=8, workers=1)
    control["translation_attempted"].extend(key for key, _ in selected)


def _metrics(work, control: dict) -> None:
    _job("metrics", control)
    identifiers = control["metrics_ids"]
    index = control.setdefault("metrics_index", 0)
    if index < len(identifiers):
        w.update_metrics.main(identifiers[index])
        control["metrics_index"] = index + 1
        return
    if control.get("metrics_all"):
        path = work.data / "journals.json"
        data = w.common.read_json(path)
        data["metrics_checked_at"] = w.common.iso_now()
        w.common.write_json(path, data)
    control["phase"] = "validate"


def _finish(work, control: dict) -> None:
    _job("validate", control)
    journals = w.common.read_json(work.data / "journals.json")["journals"]
    w.collect.prune(date.fromisoformat(control["target"]))
    w.collect.save_manifest(journals, date.fromisoformat(control["target"]), collected=control.get("full_collection", False))
    w.validate_data.main()
    w.journal_manager.set_job(status="success", stage=None, message="Completed", finished_at=w.common.iso_now())


def process_chunk(work, control: dict, seconds: float = 165) -> bool:
    """Leave >=100 seconds for the last bounded network operation and commit.

    Blob/queue persistence happens outside this function. A killed invocation
    replays this chunk after its distributed lease expires; previous chunks stay.
    """
    deadline = time.monotonic() + seconds
    original_request = w.translate.request_json

    def bounded_request(url, payload, headers, retries=3, timeout=120):
        return original_request(url, payload, headers, retries=min(retries, 2), timeout=min(timeout, 15))

    with ExitStack() as stack:
        stack.enter_context(patch.object(w.translate, "request_json", bounded_request))
        stack.enter_context(patch.object(w.title_filter, "request_json", bounded_request))
        while time.monotonic() < deadline - 70:
            kind = control["kind"]
            if kind == "delete":
                w.sync_cas.main()
                w.journal_config.remove_journal_data(control["journal_id"])
                w.collect.prune_translation_cache()
                w.journal_config.refresh_manifest()
                _finish(work, control)
                return True
            if kind == "gist-export":
                result = w.gist_sync.export_files(w.user_backup.snapshot_files())
                w.journal_manager.set_job(status="success", stage=None, result=result, message="Completed", finished_at=w.common.iso_now())
                return True
            if kind == "gist-preview":
                result = w.user_backup.stage_import(w.gist_sync.import_files(control["payload"]["gist_url"]))
                w.journal_manager.set_job(status="success", stage=None, result=result, message="Completed", finished_at=w.common.iso_now())
                return True
            if kind == "gist-import":
                result = w.user_backup.restore_preview(control["payload"]["preview_id"], w.validate_data.main)
                w.journal_manager.set_job(status="success", stage=None, result=result, message="Completed", finished_at=w.common.iso_now())
                return True
            if kind == "restore-filtered" and control["phase"] == "collect":
                w.excluded_papers.restore_excluded_records(work.data, control["payload"].get("ids", []))
                control["phase"] = "translate"
                continue
            phase = control["phase"]
            if phase == "setup":
                w.journal_config.discover_journal_metric(control["journal_id"])
                w.sync_cas.main()
                control["phase"] = "collect"
            elif phase == "collect":
                _journal_page(work, control)
            elif phase == "translate":
                _translation(work, control)
            elif phase == "metrics-prepare":
                all_ids = [journal["id"] for journal in w.common.read_json(work.data / "journals.json")["journals"]]
                due = kind == "add" or (control.get("full_collection") and w.journal_manager.metrics_due())
                control.update(phase="metrics" if due else "validate", metrics_index=0,
                               metrics_ids=control["journal_ids"] if kind == "add" else all_ids, metrics_all=kind != "add")
            elif phase == "metrics":
                control.setdefault("metrics_ids", control["journal_ids"])
                control.setdefault("metrics_all", kind == "metrics")
                _metrics(work, control)
            elif phase == "validate":
                _finish(work, control)
                return True
            else:
                raise ValueError("Unknown task phase")
    return False
