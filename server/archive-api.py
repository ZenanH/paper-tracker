#!/usr/bin/env python3
"""论文状态服务 —— 为论文追踪提供持久化的归档与收藏存储。

接口：
  GET  /api/archive   → {"updated_at":..., "items":{...}, "cleared_items":{...}, "favorites":{...}}
  POST /api/archive   → add/remove/clear/restore_cleared/favorite/unfavorite/remove_all
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
MANIFEST_FILE = Path(os.environ.get("MANIFEST_FILE", "/data/manifest.json"))
PORT = int(os.environ.get("PORT", "8080"))
BEIJING = timezone(timedelta(hours=8))
LOCK = threading.Lock()

# 归档时保留的文章字段（日期文件会滚出保留窗口，故需自带元数据）
ITEM_FIELDS = (
    "id", "journal_id", "date", "title_en", "title_zh",
    "url", "doi", "translation_status",
)
MAX_BODY = 2 * 1024 * 1024


def now_iso() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def empty_state() -> dict:
    return {"updated_at": None, "items": {}, "cleared_items": {}, "favorites": {}}


def retention_cutoff() -> str | None:
    try:
        manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        value = ((manifest.get("retention") or {}).get("start") or "").strip()
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except (AttributeError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def prune_state(state: dict) -> bool:
    cutoff = retention_cutoff()
    if not cutoff:
        return False
    changed = False
    for bucket_name in ("items", "cleared_items"):
        bucket = state[bucket_name]
        stale_ids = [
            item_id
            for item_id, item in bucket.items()
            if isinstance(item, dict) and item.get("date") and str(item["date"]) < cutoff
        ]
        for item_id in stale_ids:
            bucket.pop(item_id, None)
            changed = True
    return changed


def read_state() -> dict:
    if not ARCHIVE_FILE.exists():
        return empty_state()
    try:
        data = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    for bucket_name in ("items", "cleared_items", "favorites"):
        if not isinstance(data.get(bucket_name), dict):
            data[bucket_name] = {}
    data.setdefault("updated_at", None)
    return data


def read_pruned_state() -> dict:
    with LOCK:
        state = read_state()
        if prune_state(state):
            state["updated_at"] = now_iso()
            write_state(state)
        return state


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
    allowed_actions = {
        "add", "remove", "clear", "restore_cleared",
        "favorite", "unfavorite", "remove_all",
    }
    if action not in allowed_actions:
        raise ValueError(
            "action 必须是 add、remove、clear、restore_cleared、favorite、unfavorite 或 remove_all"
        )

    with LOCK:
        state = read_state()
        items = state["items"]
        cleared_items = state["cleared_items"]
        favorites = state["favorites"]
        changed = prune_state(state)

        if action in {"add", "favorite"}:
            raw_items = payload.get("items")
            if not isinstance(raw_items, list):
                raise ValueError(f"{action} 需要 items 数组")
            for raw in raw_items:
                item = clean_item(raw)
                if not item:
                    continue
                if action == "favorite":
                    merged = dict(favorites.get(item["id"]) or {})
                    merged.update(item)
                    merged["favorited_at"] = merged.get("favorited_at") or now_iso()
                    favorites[item["id"]] = merged
                    changed = True
                    continue
                merged = dict(items.get(item["id"]) or cleared_items.pop(item["id"], None) or {})
                merged.update(item)
                merged["archived_at"] = merged.get("archived_at") or now_iso()
                merged.pop("cleared_at", None)
                items[item["id"]] = merged
                changed = True
            if changed:
                state["updated_at"] = now_iso()
                write_state(state)
            return state

        raw_ids = payload.get("ids")
        if not isinstance(raw_ids, list):
            raise ValueError(f"{action} 需要 ids 数组")

        if action == "remove":
            for raw_id in raw_ids:
                if not isinstance(raw_id, str):
                    continue
                removed = items.pop(raw_id, None)
                removed = cleared_items.pop(raw_id, None) or removed
                changed = removed is not None or changed
        elif action == "unfavorite":
            for raw_id in raw_ids:
                if not isinstance(raw_id, str):
                    continue
                changed = favorites.pop(raw_id, None) is not None or changed
        elif action == "remove_all":
            for raw_id in raw_ids:
                if not isinstance(raw_id, str):
                    continue
                removed = items.pop(raw_id, None)
                removed = cleared_items.pop(raw_id, None) or removed
                removed = favorites.pop(raw_id, None) or removed
                changed = removed is not None or changed
        elif action == "clear":
            for raw_id in raw_ids:
                if not isinstance(raw_id, str):
                    continue
                item = items.pop(raw_id, None)
                if item is None:
                    continue
                item["cleared_at"] = item.get("cleared_at") or now_iso()
                cleared_items[raw_id] = item
                changed = True
        else:
            for raw_id in raw_ids:
                if not isinstance(raw_id, str):
                    continue
                item = cleared_items.pop(raw_id, None)
                if item is None:
                    continue
                item.pop("cleared_at", None)
                items[raw_id] = item
                changed = True

        if changed:
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
            self._send(200, read_pruned_state())
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
