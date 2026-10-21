"""Request adapter and durable queue jobs, backed by one atomic private snapshot."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from email.message import Message
from unittest.mock import patch

from cloud.storage import BlobStore, Conflict
from cloud import workspace as w

TOPIC = "paper-tracker-tasks"


def queue_message(control: dict) -> dict:
    body = {"job_id": control["id"], "step": control["step"]}
    key = os.environ.get("CRON_SECRET", "")
    if len(key) < 32:
        raise ValueError("Set CRON_SECRET to a random value of at least 32 characters.")
    body["signature"] = hmac.new(key.encode(), f'{body["job_id"]}:{body["step"]}'.encode(), hashlib.sha256).hexdigest()
    return body


def publish(control: dict) -> None:
    from vercel.queue.sync import send
    message = queue_message(control)
    send(TOPIC, message, idempotency_key=f'{message["job_id"]}-{message["step"]}', retention=86400)


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Runtime:
    def __init__(self, store=None, publisher=publish) -> None:
        self.store = store or BlobStore()
        self.publisher = publisher

    def ensure(self) -> dict:
        def initialize(document):
            if "data/manifest.json" in document["files"]:
                return
            with w.bind(document) as work:
                work.initialize()
                document["files"] = work.snapshot_files()
        snapshot = self.store.load()
        if "data/manifest.json" not in snapshot.document["files"]:
            snapshot, _ = self.store.update(initialize)
        return snapshot.document

    def dispatch(self, document: dict) -> None:
        control = document.get("control")
        if control and control.get("lease_until", 0) <= time.time():
            self.publisher(control)

    def request(self, method: str, path: str, payload: dict | None = None, headers: dict | None = None) -> tuple[int, dict, dict]:
        self.ensure()
        payload = payload or {}
        path = urllib.parse.urlsplit(path).path
        headers = headers or {}
        result_headers = {}
        if method == "GET" and path.startswith("/data/"):
            value = w.public_data(self.store.load().document, path.removeprefix("/data/"))
            return (200, value, {}) if value is not None else (404, {"error": "not found"}, {})

        def operation(document):
            with w.bind(document) as work:
                before = copy.deepcopy(document["files"])
                if path == "/api/archive":
                    if method == "GET":
                        value = w.archive_api.read_pruned_state()
                    elif method == "POST":
                        job = w.journal_manager.JOB
                        if job.get("status") == "running" and job.get("kind") == "gist-import":
                            raise ValueError("Data import is running. Wait before saving papers.")
                        value = w.archive_api.apply_archive(payload)
                        if "return=minimal" in headers.get("prefer", "").lower():
                            value = w.archive_api.response_state(value, minimal=True)
                            result_headers["Preference-Applied"] = "return=minimal"
                    else:
                        return 405, {"error": "Method not allowed"}
                    document["files"] = work.snapshot_files()
                    return 200, value

                response = []
                handler = object.__new__(w.journal_manager.Handler)
                handler.path = path
                handler.command = method
                handler.headers = Message()
                handler.headers["Host"] = "paper-tracker.internal"
                handler.headers["Origin"] = "https://paper-tracker.internal"
                handler.headers["Content-Type"] = "application/json"
                handler.read_payload = lambda: payload
                handler.send_json = lambda status, value: response.append((status, value))

                def start(kind, _callback):
                    if w.journal_manager.JOB["status"] == "running":
                        raise ValueError("A task is already running. Wait for it to finish.")
                    w.journal_manager.set_job(
                        status="running", kind=kind, stage="collect" if kind in {"manual", "daily", "backfill"} else None,
                        message="Queued", started_at=w.common.iso_now(), finished_at=None, log=[], result=None,
                    )
                    document["control"] = self.control(kind, path, payload, before, work)

                with patch.object(w.journal_manager, "start_job", start):
                    if method == "GET":
                        handler.do_GET()
                    elif method == "POST":
                        handler.do_POST()
                    elif method == "DELETE":
                        handler.do_DELETE()
                    else:
                        return 405, {"error": "Method not allowed"}
                if not response:
                    raise RuntimeError("Management handler did not return a response.")
                # Even an OAuth pending/error response may update interval/expiry.
                document["files"] = work.snapshot_files()
                return response[-1]

        try:
            # GET metadata is a read, not a Blob write. OAuth and writes use CAS.
            if method == "GET" and path != "/api/archive":
                document = self.store.load().document
                status, value = operation(document)
                self.dispatch(document)
            else:
                snapshot, result = self.store.update(operation, attempts=1)
                status, value = result
                if status < 400:
                    self.dispatch(snapshot.document)
            return status, value, result_headers
        except (ValueError, w.archive_api.ArchiveStateError) as exc:
            return 400, {"error": str(exc)}, {}

    @staticmethod
    def control(kind: str, path: str, payload: dict, before: dict, work) -> dict:
        journal_data = w.common.read_json(work.data / "journals.json", {})
        ids = [journal["id"] for journal in journal_data.get("journals", [])]
        selected = payload.get("journal_ids") or ids
        journal_id = None
        if kind == "add":
            old = json.loads(before.get("data/journal-config.json", '{"categories":{}}'))
            old_ids = {w.common.slugify(e["name"]) for values in old.get("categories", {}).values() for e in values}
            new = w.journal_config.ensure_config()
            new_ids = {w.common.slugify(e["name"]) for values in new.get("categories", {}).values() for e in values}
            journal_id = next(iter(new_ids - old_ids))
            selected = [journal_id]
        elif kind == "delete":
            journal_id = urllib.parse.unquote(path.rsplit("/", 1)[1])
        return {
            "id": secrets.token_hex(16), "step": 0, "kind": kind,
            "target": w.common.now_beijing().date().isoformat(),
            "journal_ids": selected, "journal_index": 0, "journal_id": journal_id,
            "phase": "setup" if kind == "add" else "metrics" if kind == "metrics" else "collect",
            "payload": payload, "lease_until": 0, "owner": None,
            "full_collection": kind in {"manual", "daily"} or (kind == "backfill" and not payload.get("journal_ids")),
        }

    def scheduled(self) -> tuple[int, dict, dict]:
        document = self.ensure()
        with w.bind(document):
            if not w.journal_manager.daily_catchup_due():
                return 200, {"status": "already checked"}, {}
        status, value, headers = self.request("POST", "/api/admin/run", {"mode": "daily"})
        return status, value, headers

    def worker(self, message: dict) -> None:
        current = self.store.load(fresh=True).document.get("control")
        if not current:
            return
        expected = queue_message({"id": message.get("job_id"), "step": message.get("step")})
        if not hmac.compare_digest(str(message.get("signature", "")), expected["signature"]):
            raise ValueError("Invalid task signature.")
        owner = secrets.token_hex(16)

        def claim(document):
            control = document.get("control")
            if not control or control["id"] != message["job_id"] or control["step"] != message["step"]:
                return False
            if control.get("lease_until", 0) > time.time():
                raise Conflict("Task lease is active; retry this queue delivery later.")
            control.update(owner=owner, lease_until=time.time() + 290)
            return True

        claimed, active = self.store.update(claim)
        if not active:
            self.dispatch(claimed.document)
            return
        before = claimed.document["files"]
        control = copy.deepcopy(claimed.document["control"])
        try:
            with w.bind(copy.deepcopy(claimed.document)) as work:
                from cloud.tasks import process_chunk
                finished = process_chunk(work, control, seconds=165)
                after = work.snapshot_files()
        except Exception:
            # Do not store provider response bodies, credentials or tracebacks in public status.
            def fail(document):
                live = document.get("control")
                if live and live.get("owner") == owner:
                    job = json.loads(document["files"].get("data/task-status.json", "{}"))
                    job.update(status="failed", stage=None, finished_at=w.common.iso_now(), message="Task failed. Retry the operation; saved data has been retained.")
                    document["files"]["data/task-status.json"] = _dump(job)
                    document["control"] = None
            self.store.update(fail)
            return

        def commit(document):
            live = document.get("control")
            if not live or live.get("owner") != owner or live["id"] != control["id"]:
                return
            w.merge_files(document, before, after, preserve_archive=control["kind"] != "gist-import")
            if control["kind"] == "delete":
                raw = document["files"].get("archive/archive.json")
                if raw:
                    saved = json.loads(raw)
                    for bucket in ("items", "cleared_items"):
                        saved[bucket] = {key: record for key, record in saved.get(bucket, {}).items() if record.get("journal_id") != control["journal_id"]}
                    document["files"]["archive/archive.json"] = _dump(saved)
            if finished:
                document["control"] = None
            else:
                control.update(step=control["step"] + 1, owner=None, lease_until=0)
                document["control"] = control

        saved, _ = self.store.update(commit)
        self.dispatch(saved.document)
