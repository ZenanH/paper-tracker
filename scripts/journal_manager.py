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
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from collect import prune_translation_cache
from common import DATA_DIR, ROOT, iso_now, read_json, subtract_months, write_json
from journal_config import (
    CONFIG_PATH,
    add_journal,
    ensure_config,
    ensure_runtime_data,
    refresh_manifest,
    remove_journal,
    remove_journal_data,
)

PORT = int(os.environ.get("MANAGER_PORT", "8090"))
ARCHIVE_API = os.environ.get("ARCHIVE_API_URL", "http://archive-api:8080/api/archive")
BEIJING = ZoneInfo("Asia/Shanghai")
MAX_BODY = 128 * 1024
JOB_LOCK = threading.Lock()
OPERATION_LOCK = threading.Lock()
JOB_DEFAULTS: dict[str, Any] = {
    "status": "idle",
    "kind": None,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "log": [],
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
    try:
        with urllib.request.urlopen(ARCHIVE_API, timeout=30) as response:
            archive = json.load(response)
        article_ids.extend(
            item_id
            for bucket in ("items", "cleared_items", "favorites")
            for item_id, item in (archive.get(bucket) or {}).items()
            if item.get("journal_id") == journal_id
        )
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        append_log(f"警告：无法读取归档状态，将按论文数据中的 ID 清理：{exc}")
    article_ids = list(dict.fromkeys(article_ids))
    for offset in range(0, len(article_ids), 500):
        body = json.dumps(
            {"action": "remove_all", "ids": article_ids[offset : offset + 500]}
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
    run_command(["python3", "scripts/collect.py", "--mode", "daily"])
    run_command(
        [
            "python3", "scripts/translate.py", "--limit", "1000",
            "--batch-size", "8", "--workers", "2",
        ]
    )
    reconciled = reconciliation_due()
    if reconciled:
        run_command(["python3", "scripts/collect.py", "--mode", "backfill"])
        run_command(
            [
                "python3", "scripts/translate.py", "--limit", "2000",
                "--batch-size", "8", "--workers", "2",
            ]
        )
    if metrics_due():
        run_command(["python3", "scripts/update_metrics.py"])
    run_command(["python3", "scripts/validate_data.py"])
    if reconciled:
        mark_reconciled()


def add_pipeline(journal_id: str) -> None:
    run_command(["python3", "scripts/sync_cas.py"])
    run_command(["python3", "scripts/update_metrics.py", "--journal-id", journal_id])
    run_command(
        ["python3", "scripts/collect.py", "--mode", "backfill", "--journal-id", journal_id]
    )
    run_command(
        [
            "python3", "scripts/translate.py", "--limit", "2000",
            "--batch-size", "8", "--workers", "2",
        ]
    )
    run_command(["python3", "scripts/validate_data.py"])


def delete_pipeline(journal_id: str) -> None:
    run_command(["python3", "scripts/sync_cas.py"])
    removed_ids = remove_journal_data(journal_id)
    remove_from_archive(journal_id, removed_ids)
    prune_translation_cache()
    refresh_manifest()
    run_command(["python3", "scripts/validate_data.py"])
    append_log(f"已删除 {len(removed_ids)} 篇关联论文")


def execute_job(kind: str, callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception as exc:
        set_job(status="failed", message=str(exc), finished_at=iso_now())
        return
    set_job(status="success", message="运行完成", finished_at=iso_now())


def start_job(kind: str, callback: Callable[[], None]) -> None:
    with JOB_LOCK:
        if JOB["status"] == "running":
            raise ValueError("已有任务正在运行，请等待完成")
        JOB.update(
            status="running",
            kind=kind,
            message="任务正在运行",
            started_at=iso_now(),
            finished_at=None,
            log=[],
        )
        save_job()
    thread = threading.Thread(target=execute_job, args=(kind, callback), daemon=True)
    thread.start()


def next_run(now: datetime | None = None) -> datetime:
    current = now or datetime.now(BEIJING)
    candidate = current.replace(hour=1, minute=0, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate


def daily_catchup_due(now: datetime | None = None) -> bool:
    current = now or datetime.now(BEIJING)
    expected = (current.date() - timedelta(days=1)).isoformat()
    actual = read_json(DATA_DIR / "manifest.json", {}).get("default_date")
    try:
        return datetime.strptime(actual, "%Y-%m-%d").date() < datetime.strptime(
            expected, "%Y-%m-%d"
        ).date()
    except (TypeError, ValueError):
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
    with JOB_LOCK:
        job = dict(JOB)
    return {
        "journals": journals,
        "job": {**job, "next_run": next_run().isoformat()},
        "timezone": "Asia/Shanghai",
        "schedule": "01:00",
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
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("请求体缺失或过大")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        if self.request_path() in {"/api/admin/healthz", "/healthz"}:
            self.send_json(200, {"status": "ok"})
        elif self.request_path() in {"/api/admin/journals", "/api/admin/status"}:
            self.send_json(200, journal_payload())
        else:
            self.send_json(404, {"error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.request_path() == "/api/admin/journals":
                payload = self.read_payload()
                with OPERATION_LOCK:
                    with JOB_LOCK:
                        if JOB["status"] == "running":
                            raise ValueError("已有任务正在运行，请等待完成")
                    journal = add_journal(payload.get("name", ""))
                    start_job("add", lambda: add_pipeline(journal["id"]))
                self.send_json(202, {"journal": journal, **journal_payload()})
                return
            if self.request_path() == "/api/admin/run":
                with OPERATION_LOCK:
                    start_job("manual", daily_pipeline)
                self.send_json(202, journal_payload())
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
            with OPERATION_LOCK:
                with JOB_LOCK:
                    if JOB["status"] == "running":
                        raise ValueError("已有任务正在运行，请等待完成")
                journal_id = urllib.parse.unquote(request_path[len(prefix) :])
                journal = remove_journal(journal_id)
                start_job("delete", lambda: delete_pipeline(journal_id))
            self.send_json(202, {"journal": journal, **journal_payload()})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})


def sync_if_needed() -> None:
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


def main() -> int:
    sync_if_needed()
    restore_job()
    start_startup_catchup()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(
        f"journal-manager listening on 0.0.0.0:{PORT}; next run={next_run().isoformat()}",
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
