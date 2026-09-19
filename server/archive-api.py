#!/usr/bin/env python3
"""已归档状态服务 —— 为论文追踪提供持久化的「已读归档」存储。

接口：
  GET  /api/archive   → {"updated_at":..., "items":{<article_id>:{...}}}
  POST /api/archive   → {"action":"add","items":[{...}]} 或 {"action":"remove","ids":[...]}
  GET  /api/healthz   → 健康检查

存储：ARCHIVE_FILE（默认 /archive/archive.json），原子写入，权限 0644。
仅监听容器内网，不对外发布端口。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ARCHIVE_FILE = Path(os.environ.get("ARCHIVE_FILE", "/archive/archive.json"))
PORT = int(os.environ.get("PORT", "8080"))
BEIJING = timezone(timedelta(hours=8))
LOCK = threading.Lock()

# 归档时保留的文章字段（日期文件会滚出保留窗口，故需自带元数据）
ITEM_FIELDS = ("id", "journal_id", "date", "title_en", "title_zh", "url", "doi")
MAX_BODY = 2 * 1024 * 1024


def now_iso() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def empty_state() -> dict:
    return {"updated_at": None, "items": {}}


def read_state() -> dict:
    if not ARCHIVE_FILE.exists():
        return empty_state()
    try:
        data = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    if not isinstance(data.get("items"), dict):
        data["items"] = {}
    data.setdefault("updated_at", None)
    return data


def write_state(state: dict) -> None:
    ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".archive.", dir=ARCHIVE_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        # mkstemp 固定 0600；站点/宿主需可读，放宽到 0644
        os.chmod(tmp, 0o644)
        os.replace(tmp, ARCHIVE_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def clean_item(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    item = {key: raw.get(key) for key in ITEM_FIELDS if raw.get(key) not in (None, "")}
    if not item.get("id"):
        return None
    return item


def apply_archive(payload: dict) -> dict:
    """在锁内读改写，返回新状态。"""
    action = str(payload.get("action") or "").strip()
    if action not in {"add", "remove"}:
        raise ValueError("action 必须是 add 或 remove")

    with LOCK:
        state = read_state()
        items = state["items"]

        if action == "add":
            raw_items = payload.get("items")
            if not isinstance(raw_items, list):
                raise ValueError("add 需要 items 数组")
            added = 0
            for raw in raw_items:
                item = clean_item(raw)
                if not item:
                    continue
                merged = dict(items.get(item["id"]) or {})
                merged.update(item)
                merged["archived_at"] = merged.get("archived_at") or now_iso()
                items[item["id"]] = merged
                added += 1
            if added:
                state["updated_at"] = now_iso()
                write_state(state)
            return state

        raw_ids = payload.get("ids")
        if not isinstance(raw_ids, list):
            raise ValueError("remove 需要 ids 数组")
        removed = 0
        for raw_id in raw_ids:
            if isinstance(raw_id, str) and items.pop(raw_id, None) is not None:
                removed += 1
        if removed:
            state["updated_at"] = now_iso()
            write_state(state)
        return state


class Handler(BaseHTTPRequestHandler):
    server_version = "paper-tracker-archive/1.0"
    protocol_version = "HTTP/1.1"

    # 不记录访问日志（避免噪音；错误走 stderr）
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _path(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/") or "/"

    def do_GET(self) -> None:  # noqa: N802
        path = self._path()
        if path in {"/api/healthz", "/healthz"}:
            self._send(200, {"status": "ok"})
            return
        if path == "/api/archive":
            self._send(200, read_state())
            return
        self._send(404, {"error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self._path() != "/api/archive":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._send(400, {"error": "请求体缺失或过大"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
            state = apply_archive(payload)
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"error": "JSON 解析失败"})
            return
        self._send(200, state)


def main() -> int:
    if not ARCHIVE_FILE.parent.exists():
        ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"archive-api listening on 0.0.0.0:{PORT}, file={ARCHIVE_FILE}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
