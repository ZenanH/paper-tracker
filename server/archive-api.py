#!/usr/bin/env python3
"""论文状态服务 —— 为论文追踪提供持久化的归档与收藏存储。

接口：
  GET  /api/archive   → {"updated_at":..., "items":{...}, "cleared_items":{...}, "favorites":{...}}
  POST /api/archive   → add/remove/clear/restore_cleared/favorite/unfavorite
                         （Prefer: return=minimal 时只返回更新时间）
  GET  /api/healthz   → health check

存储：ARCHIVE_FILE（默认位于项目 archive/archive.json），原子写入。
本机运行时只监听回环地址。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import fcntl
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PAPER_TRACKER_DATA_DIR", ROOT / "data/live")).expanduser().resolve()
ARCHIVE_FILE = Path(os.environ.get("ARCHIVE_FILE", ROOT / "archive/archive.json")).expanduser().resolve()
MANIFEST_FILE = Path(os.environ.get("MANIFEST_FILE", DATA_DIR / "manifest.json")).expanduser().resolve()
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "127.0.0.1")
BEIJING = timezone(timedelta(hours=8))
LOCK = threading.Lock()


@contextmanager
def state_lock():
    with LOCK:
        ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with (ARCHIVE_FILE.parent / ".archive.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                if (ARCHIVE_FILE.parent / ".restore-pending.json").exists():
                    raise ArchiveStateError("Data restoration requires recovery. Reading-state writes are temporarily disabled.")
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

# 归档时保留的文章字段（日期文件会滚出保留窗口，故需自带元数据）
ITEM_FIELDS = (
    "id", "journal_id", "date", "title_en", "title_zh",
    "url", "doi", "translation_status", "first_seen_at",
    "date_inferred", "date_inference",
)
MAX_BODY = 2 * 1024 * 1024


class ArchiveStateError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def empty_state() -> dict:
    return {"updated_at": None, "items": {}, "cleared_items": {}, "favorites": {}, "dataset_id": "initial"}


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
            if isinstance(item, dict)
            and (item.get("date") or item.get("first_seen_at") or item.get("archived_at"))
            and str(item.get("date") or item.get("first_seen_at") or item["archived_at"])[:10] < cutoff
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
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ArchiveStateError(f"The archive file is unreadable or corrupt: {ARCHIVE_FILE}") from exc
    if not isinstance(data, dict):
        raise ArchiveStateError(f"The archive file must contain a JSON object: {ARCHIVE_FILE}")
    for bucket_name in ("items", "cleared_items", "favorites"):
        if bucket_name not in data:
            data[bucket_name] = {}
        elif not isinstance(data[bucket_name], dict):
            raise ArchiveStateError(f"Archive field {bucket_name} must be a JSON object.")
    data.setdefault("updated_at", None)
    data.setdefault("dataset_id", "initial")
    return data


def read_pruned_state() -> dict:
    with state_lock():
        state = read_state()
        normalized = normalize_legacy_dates(state)
        if prune_state(state) or normalized:
            state["updated_at"] = now_iso()
            write_state(state)
        return state


def normalize_legacy_dates(state: dict) -> bool:
    """One-time compatibility for saved papers without a publication date."""
    try:
        manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if manifest.get("date_policy") != "current-month-start":
        return False
    month_start = datetime.fromisoformat(now_iso()).date().replace(day=1).isoformat()
    changed = False
    for bucket in ("items", "cleared_items", "favorites"):
        for item in state[bucket].values():
            if isinstance(item, dict) and not item.get("date"):
                item.update(date=month_start, date_inferred=True, date_inference="current-month-start")
                changed = True
    return changed


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


def response_state(state: dict, minimal: bool = False) -> dict:
    """Return either the legacy full state or a small mutation acknowledgement.

    The browser already applies archive/favorite mutations optimistically.  A
    minimal response keeps a POST from sending the entire (potentially large)
    archive back through the local web gateway while retaining the full response for
    older clients that do not opt in.
    """
    if minimal:
        return {"updated_at": state.get("updated_at")}
    return state


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
        "favorite", "unfavorite",
    }
    if action not in allowed_actions:
        raise ValueError(
            "action must be add, remove, clear, restore_cleared, favorite, or unfavorite."
        )

    with state_lock():
        state = read_state()
        if (payload.get("dataset_id") is not None or state["dataset_id"] != "initial") and payload.get("dataset_id") != state["dataset_id"]:
            raise ValueError("The dataset changed after an import. Reload before changing saved papers.")
        items = state["items"]
        cleared_items = state["cleared_items"]
        favorites = state["favorites"]
        normalized = normalize_legacy_dates(state)
        changed = prune_state(state) or normalized

        if action in {"add", "favorite"}:
            raw_items = payload.get("items")
            if not isinstance(raw_items, list):
                raise ValueError(f"{action} requires an items array.")
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
            raise ValueError(f"{action} requires an IDs array.")

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

    def _send(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
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
            try:
                self._send(200, read_pruned_state())
            except ArchiveStateError as exc:
                self._send(500, {"error": str(exc)})
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
            self._send(400, {"error": "The request body is missing or too large."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("The request body must be a JSON object.")
            state = apply_archive(payload)
        except ArchiveStateError as exc:
            self._send(500, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"error": "Invalid JSON."})
            return
        prefer = (self.headers.get("Prefer") or "").lower()
        minimal = "return=minimal" in prefer
        self._send(
            200,
            response_state(state, minimal=minimal),
            {"Preference-Applied": "return=minimal"} if minimal else None,
        )


def main() -> int:
    if not ARCHIVE_FILE.parent.exists():
        ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"archive-api listening on {HOST}:{PORT}, file={ARCHIVE_FILE}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
