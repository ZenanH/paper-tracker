#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import archive_recovery
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler
from http_service import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from collect import migrate_legacy_records, prune_translation_cache, save_manifest
from common import DATA_DIR, ROOT, iso_now, read_json, subtract_months, write_json
from excluded_papers import (
    load_excluded,
    public_excluded_records,
    restore_excluded_records,
)
from journal_config import (
    CONFIG_PATH,
    add_journal,
    ensure_config,
    ensure_runtime_data,
    filter_journal_ids,
    refresh_manifest,
    remove_journal,
    remove_journal_data,
    set_filter_journal,
    update_filter_config,
    snapshot_row_for_name,
    discover_journal_metric,
)
from llm_settings import (
    disable_llm_settings,
    llm_is_configured,
    public_llm_settings,
    save_llm_settings,
    verify_llm_settings,
)

PORT = int(os.environ.get("MANAGER_PORT", "8090"))
HOST = os.environ.get("MANAGER_HOST", "127.0.0.1")
ARCHIVE_API = os.environ.get("ARCHIVE_API_URL", "http://127.0.0.1:8080/api/archive")
BEIJING = ZoneInfo("Asia/Shanghai")
MAX_BODY = 128 * 1024
JOB_LOCK = threading.Lock()
OPERATION_LOCK = threading.Lock()
JOB_DEFAULTS: dict[str, Any] = {
    "status": "idle",
    "kind": None,
    "stage": None,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "log": [],
    "result": None,
}
JOB: dict[str, Any] = dict(JOB_DEFAULTS)
MAINTENANCE_PATH = DATA_DIR / "maintenance-status.json"


def save_job() -> None:
    write_json(DATA_DIR / "task-status.json", {**JOB, "next_run": next_run().isoformat()})


def set_job(**values: Any) -> None:
    with JOB_LOCK:
        JOB.update(values)
        save_job()


def append_log(message: str) -> None:
    clean = str(message).strip()
    if not clean:
        return
    with JOB_LOCK:
        JOB["log"] = [*JOB.get("log", []), clean][-40:]
        save_job()


def restore_job() -> None:
    saved = read_json(DATA_DIR / "task-status.json", {})
    with JOB_LOCK:
        JOB.clear()
        JOB.update(JOB_DEFAULTS)
        if isinstance(saved, dict):
            for key in JOB_DEFAULTS:
                if key in saved:
                    JOB[key] = saved[key]
        if JOB["status"] == "running":
            JOB.update(
                status="failed",
                message="上次任务在服务重启时中断",
                finished_at=iso_now(),
            )
            JOB["log"] = [*JOB.get("log", []), "服务重启：上次运行中的任务已标记为中断"][-40:]
        save_job()


def run_command(arguments: list[str]) -> None:
    display = " ".join(arguments)
    append_log(f"运行：{display}")
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60 * 60,
        check=False,
    )
    output = completed.stdout.strip()
    if output:
        append_log("\n".join(output.splitlines()[-12:]))
    if completed.returncode:
        raise RuntimeError(f"命令失败（{completed.returncode}）：{display}")


def remove_from_archive(journal_id: str, article_ids: list[str]) -> None:
    dataset_id = "initial"
    try:
        with urllib.request.urlopen(ARCHIVE_API, timeout=30) as response:
            archive = json.load(response)
        dataset_id = archive.get("dataset_id", "initial")
        article_ids.extend(
            item_id
            for bucket in ("items", "cleared_items")
            for item_id, item in (archive.get(bucket) or {}).items()
            if item.get("journal_id") == journal_id
        )
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        append_log(f"警告：无法读取归档状态，将按论文数据中的 ID 清理：{exc}")
    article_ids = list(dict.fromkeys(article_ids))
    for offset in range(0, len(article_ids), 500):
        body = json.dumps(
            {"action": "remove", "ids": article_ids[offset : offset + 500], "dataset_id": dataset_id}
        ).encode("utf-8")
        request = urllib.request.Request(
            ARCHIVE_API,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30):
                pass
        except (urllib.error.URLError, TimeoutError) as exc:
            append_log(f"警告：归档状态清理失败，将保留无效归档记录：{exc}")


def metrics_due() -> bool:
    payload = read_json(DATA_DIR / "journals.json", {})
    checked = payload.get("metrics_checked_at")
    if not checked:
        return True
    try:
        checked_date = datetime.fromisoformat(checked).date()
    except (TypeError, ValueError):
        return True
    return checked_date <= subtract_months(datetime.now(BEIJING).date(), 3)


def reconciliation_due(reference: date | None = None) -> bool:
    current = reference or datetime.now(BEIJING).date()
    payload = read_json(MAINTENANCE_PATH, {})
    checked = payload.get("last_reconciled_date")
    try:
        checked_date = datetime.strptime(checked, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return True
    return checked_date <= current - timedelta(days=7)


def mark_reconciled(reference: date | None = None) -> None:
    current = reference or datetime.now(BEIJING).date()
    write_json(
        MAINTENANCE_PATH,
        {"last_reconciled_date": current.isoformat(), "updated_at": iso_now()},
    )


def daily_pipeline() -> None:
    set_job(stage="collect")
    run_command(["python3", "scripts/collect.py", "--mode", "daily"])
    if llm_is_configured():
        set_job(stage="translate")
    run_translation(2000)
    if metrics_due():
        set_job(stage="metrics")
        run_command(["python3", "scripts/update_metrics.py"])
    set_job(stage="validate")
    run_command(["python3", "scripts/validate_data.py"])
    mark_reconciled()


def backfill_pipeline(journal_ids: list[str] | None = None) -> None:
    for journal_id in journal_ids or [None]:
        command = ["python3", "scripts/collect.py", "--mode", "backfill"]
        if journal_id:
            command.extend(["--journal-id", journal_id])
        run_command(command)
    run_translation(2000)
    run_command(["python3", "scripts/validate_data.py"])
    if journal_ids is None:
        mark_reconciled()


def add_pipeline(journal_id: str) -> None:
    discover_journal_metric(journal_id)
    run_command(["python3", "scripts/sync_cas.py"])
    run_command(["python3", "scripts/update_metrics.py", "--journal-id", journal_id])
    run_command(
        ["python3", "scripts/collect.py", "--mode", "backfill", "--journal-id", journal_id]
    )
    run_translation(2000)
    run_command(["python3", "scripts/validate_data.py"])


def run_translation(limit: int) -> bool:
    if not llm_is_configured():
        append_log("未配置模型，跳过翻译；标题过滤也不会执行")
        return False
    run_command(
        [
            "python3", "scripts/translate.py", "--limit", str(limit),
            "--batch-size", "8", "--workers", "2",
        ]
    )
    return True


def restore_filtered_pipeline(filter_ids: list[str]) -> None:
    result = restore_excluded_records(DATA_DIR, filter_ids)
    restored_count = len(result["restored"])
    append_log(
        f"已恢复 {restored_count} 篇过滤论文，跳过 {len(result['skipped'])} 篇"
    )
    refresh_manifest()
    if restored_count:
        run_translation(2000)
    run_command(["python3", "scripts/validate_data.py"])


def delete_pipeline(journal_id: str) -> None:
    run_command(["python3", "scripts/sync_cas.py"])
    removed_ids = remove_journal_data(journal_id)
    remove_from_archive(journal_id, removed_ids)
    prune_translation_cache()
    refresh_manifest()
    run_command(["python3", "scripts/validate_data.py"])
    append_log(f"Stopped following journal; removed {len(removed_ids)} window records; favorites retained")


def execute_job(kind: str, callback: Callable[[], Any]) -> None:
    try:
        result = callback()
    except Exception as exc:
        set_job(status="failed", stage=None, message=str(exc), finished_at=iso_now())
        return
    set_job(status="success", stage=None, message="Completed", result=result, finished_at=iso_now())


def start_job(kind: str, callback: Callable[[], Any]) -> None:
    with JOB_LOCK:
        if JOB["status"] == "running":
            raise ValueError("A task is already running. Wait for it to finish.")
        JOB.update(
            status="running",
            kind=kind,
            stage=None,
            message="任务正在运行",
            started_at=iso_now(),
            finished_at=None,
            log=[],
            result=None,
        )
        save_job()
    thread = threading.Thread(target=execute_job, args=(kind, callback), daemon=True)
    thread.start()


def next_run(now: datetime | None = None) -> datetime:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    candidate = current.replace(hour=1, minute=0, second=0, microsecond=0)
    return candidate if candidate > current else candidate + timedelta(days=1)


def daily_catchup_due(now: datetime | None = None) -> bool:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    expected = current.replace(hour=1, minute=0, second=0, microsecond=0)
    if current < expected:
        return False
    manifest = read_json(DATA_DIR / "manifest.json", {})
    actual = manifest.get("collection_checked_at")
    try:
        checked = datetime.fromisoformat(actual.replace("Z", "+00:00"))
        if checked.tzinfo is None:
            return True
        return checked.astimezone(BEIJING) < expected
    except (TypeError, ValueError, AttributeError):
        return True


def start_startup_catchup() -> bool:
    if not daily_catchup_due():
        return False
    with OPERATION_LOCK:
        start_job("daily-catchup", daily_pipeline)
    return True


def scheduler_loop() -> None:
    while True:
        scheduled = next_run()
        while True:
            remaining = (scheduled - datetime.now(BEIJING)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 60))
        while True:
            with OPERATION_LOCK:
                if not daily_catchup_due():
                    break
                try:
                    start_job("daily", daily_pipeline)
                    break
                except ValueError:
                    pass
            time.sleep(30)
        time.sleep(61)


def journal_payload() -> dict[str, Any]:
    journals = read_json(DATA_DIR / "journals.json", {}).get("journals", [])
    excluded = load_excluded(DATA_DIR).get("entries", {})
    with JOB_LOCK:
        job = dict(JOB)
    return {
        "journals": journals,
        "filter_journal_ids": [
            journal_id
            for journal_id in filter_journal_ids()
            if journal_id in {journal["id"] for journal in journals}
        ],
        "excluded_count": len(excluded),
        "filter_prompt": read_json(DATA_DIR / "filter-config.json", {}).get("prompt", ""),
        "llm": public_llm_settings(),
        "job": {**job, "next_run": next_run().isoformat()},
        "timezone": "Asia/Shanghai",
        "schedule": "01:00",
        "update_interval_hours": 24,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "paper-tracker-manager/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def request_path(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/") or "/"

    def read_payload(self) -> dict[str, Any]:
        self.check_origin()
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("The request body is missing or too large.")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("The request body must be a JSON object.")
        return payload

    def check_origin(self) -> None:
        # Browsers must not authorize or replace data from another website.
        if not hasattr(self, "headers"):
            return  # Unit handlers may supply their own payload reader.
        origin = self.headers.get("Origin")
        if self.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host")):
            raise ValueError("Requests must come from this Paper Tracker site.")
        if self.command == "POST" and self.headers.get_content_type() != "application/json":
            raise ValueError("Use an application/json request.")

    def do_GET(self) -> None:  # noqa: N802
        if self.request_path() in {"/api/admin/healthz", "/healthz"}:
            self.send_json(200, {"status": "ok"})
        elif self.request_path() in {"/api/admin/journals", "/api/admin/status"}:
            self.send_json(200, journal_payload())
        elif self.request_path() == "/api/admin/filter/excluded":
            self.send_json(200, {"records": public_excluded_records(DATA_DIR)})
        else:
            self.send_json(404, {"error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        try:
            self.check_origin()
            if self.request_path() == "/api/admin/journals/resolve":
                payload = self.read_payload()
                name = payload.get("name")
                if not isinstance(name, str) or not 2 <= len(name.strip()) <= 300:
                    raise ValueError("Enter an English journal name between 2 and 300 characters.")
                row = snapshot_row_for_name(name)
                self.send_json(200, {
                    "name": row["Journal"].strip(),
                    "issns": [part.strip() for part in row["ISSN/EISSN"].split("/") if part.strip()],
                    "cas": {
                        "zone": int(row["大类分区"].split()[0]),
                        "category": row["大类"], "top": row["Top"] == "是",
                    },
                    "source": "2025 升级版（官方平台停服前快照）",
                })
                return
            if self.request_path() == "/api/admin/metrics/update":
                with OPERATION_LOCK:
                    start_job("metrics", lambda: run_command(["python3", "scripts/update_metrics.py"]))
                self.send_json(202, journal_payload())
                return
            if self.request_path() == "/api/admin/llm/verify":
                payload = self.read_payload()
                result = verify_llm_settings(payload.get("base_url"), payload.get("api_key"), payload.get("model"))
                self.send_json(200, result)
                return
            if self.request_path() == "/api/admin/journals":
                payload = self.read_payload()
                filter_enabled = payload.get("filter_enabled", False)
                if not isinstance(filter_enabled, bool):
                    raise ValueError("filter_enabled must be a boolean.")
                with OPERATION_LOCK:
                    with JOB_LOCK:
                        if JOB["status"] == "running":
                            raise ValueError("A task is already running. Wait for it to finish.")
                    journal = add_journal(payload.get("name", ""))
                    set_filter_journal(journal["id"], filter_enabled)
                    start_job("add", lambda: add_pipeline(journal["id"]))
                self.send_json(202, {"journal": journal, **journal_payload()})
                return
            if self.request_path() == "/api/admin/run":
                payload = self.read_payload()
                mode = payload.get("mode", "daily")
                if not isinstance(mode, str) or mode not in {"daily", "backfill"}:
                    raise ValueError("mode must be daily or backfill.")
                with OPERATION_LOCK:
                    journal_ids = payload.get("journal_ids")
                    if journal_ids is not None:
                        valid_ids = {journal["id"] for journal in read_json(DATA_DIR / "journals.json", {}).get("journals", [])}
                        if mode != "backfill" or not isinstance(journal_ids, list) or not journal_ids or any(not isinstance(value, str) or value not in valid_ids for value in journal_ids):
                            raise ValueError("Choose valid journals for backfill.")
                        journal_ids = list(dict.fromkeys(journal_ids))
                    start_job("backfill" if mode == "backfill" else "manual", (lambda: backfill_pipeline(journal_ids)) if mode == "backfill" else daily_pipeline)
                self.send_json(202, journal_payload())
                return
            if self.request_path() == "/api/admin/filter":
                payload = self.read_payload()
                with OPERATION_LOCK:
                    with JOB_LOCK:
                        if JOB["status"] == "running":
                            raise ValueError("Wait for the running task before changing filter settings.")
                    journals = read_json(DATA_DIR / "journals.json", {}).get("journals", [])
                    valid_ids = {journal["id"] for journal in journals}
                    update_filter_config(payload.get("journal_ids"), valid_ids, payload.get("prompt"))
                self.send_json(200, journal_payload())
                return
            if self.request_path() == "/api/admin/filter/restore":
                payload = self.read_payload()
                filter_ids = payload.get("ids")
                if not isinstance(filter_ids, list) or any(
                    not isinstance(item, str) for item in filter_ids
                ):
                    raise ValueError("Restore IDs must be an array of strings.")
                if not filter_ids:
                    raise ValueError("Select at least one paper.")
                with OPERATION_LOCK:
                    start_job(
                        "restore-filtered",
                        lambda: restore_filtered_pipeline(filter_ids),
                    )
                self.send_json(202, journal_payload())
                return
            if self.request_path() == "/api/admin/llm":
                payload = self.read_payload()
                with OPERATION_LOCK:
                    with JOB_LOCK:
                        if JOB["status"] == "running":
                            raise ValueError("Wait for the running task before changing model settings.")
                    save_llm_settings(
                        payload.get("base_url"),
                        payload.get("api_key"),
                        payload.get("model"),
                    )
                self.send_json(200, journal_payload())
                return
            if self.request_path() == "/api/admin/llm/disable":
                with OPERATION_LOCK:
                    with JOB_LOCK:
                        if JOB["status"] == "running":
                            raise ValueError("Wait for the running task before changing model settings.")
                    disable_llm_settings()
                self.send_json(200, journal_payload())
                return
            self.send_json(404, {"error": "not found"})
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        prefix = "/api/admin/journals/"
        request_path = self.request_path()
        if not request_path.startswith(prefix):
            self.send_json(404, {"error": "not found"})
            return
        try:
            self.check_origin()
            with OPERATION_LOCK:
                with JOB_LOCK:
                    if JOB["status"] == "running":
                        raise ValueError("A task is already running. Wait for it to finish.")
                journal_id = urllib.parse.unquote(request_path[len(prefix) :])
                journal = remove_journal(journal_id)
                start_job("delete", lambda: delete_pipeline(journal_id))
            self.send_json(202, {"journal": journal, **journal_payload()})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})


def sync_if_needed() -> None:
    archive_recovery.recover_restore()
    config_existed = CONFIG_PATH.exists()
    ensure_config()
    journals_path = DATA_DIR / "journals.json"
    if (
        not config_existed
        or not journals_path.exists()
        or CONFIG_PATH.stat().st_mtime > journals_path.stat().st_mtime
    ):
        run_command(["python3", "scripts/sync_cas.py"])
    ensure_runtime_data()
    if migrate_legacy_records(datetime.now(BEIJING).date()):
        journals = read_json(journals_path, {}).get("journals", [])
        save_manifest(journals, datetime.now(BEIJING).date(), collected=False)


def main() -> int:
    sync_if_needed()
    restore_job()
    start_startup_catchup()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(
        f"journal-manager listening on {HOST}:{PORT}; next run={next_run().isoformat()}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
