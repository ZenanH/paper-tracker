"""Private Blob snapshot with ETag compare-and-swap; never use /tmp as storage.

The HTTP headers follow Vercel's Blob SDK (API v12). Conditional PUT is used
because vercel-blob 0.5.0 does not yet expose if_match on its public put helper.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

MAX_STATE_BYTES = 256 * 1024 * 1024
MAX_BLOB_BYTES = 32 * 1024 * 1024


class Conflict(RuntimeError):
    pass


class StorageUnavailable(RuntimeError):
    pass


def empty_document() -> dict:
    return {"version": 1, "files": {}, "control": None, "auth_flows": {}}


def encode(document: dict) -> bytes:
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_STATE_BYTES:
        raise StorageUnavailable("Saved data exceeds the cloud snapshot size limit.")
    result = gzip.compress(raw, mtime=0)
    if len(result) > MAX_BLOB_BYTES:
        raise StorageUnavailable("Saved data exceeds the cloud snapshot size limit.")
    return result


def decode(body: bytes) -> dict:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
            raw = stream.read(MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError()
        document = json.loads(raw)
        if document.get("version") != 1 or not isinstance(document.get("files"), dict):
            raise ValueError()
        return document
    except (ValueError, TypeError, OSError, EOFError, AttributeError) as exc:
        raise StorageUnavailable("The saved cloud data is corrupt; refusing to overwrite it.") from exc


@dataclass
class Snapshot:
    document: dict
    etag: str | None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BlobStore:
    def __init__(self) -> None:
        token = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
        match = re.fullmatch(r"vercel_blob_rw_([A-Za-z0-9]+)_.+", token)
        if not match:
            raise StorageUnavailable("Connect a Private Vercel Blob store before using this app.")
        self.token = token
        env = os.environ.get("VERCEL_ENV", "development")
        branch = os.environ.get("VERCEL_GIT_COMMIT_REF", "preview")
        namespace = "production" if env == "production" else env + "-" + hashlib.sha256(branch.encode()).hexdigest()[:12]
        self.pathname = f"paper-tracker/{namespace}/state.json.gz"
        self.url = f"https://{match[1].lower()}.private.blob.vercel-storage.com/{self.pathname}"
        self.opener = urllib.request.build_opener(NoRedirect())
        self._cache: tuple[float, Snapshot] | None = None
        self._cache_lock = threading.Lock()

    def load(self, *, fresh: bool = False) -> Snapshot:
        with self._cache_lock:
            if not fresh and self._cache and time.monotonic() - self._cache[0] < 2:
                return copy.deepcopy(self._cache[1])
        request = urllib.request.Request(self.url, headers={"Authorization": f"Bearer {self.token}", "Cache-Control": "no-cache"})
        try:
            with self.opener.open(request, timeout=20) as response:
                body = response.read(MAX_BLOB_BYTES + 1)
                if len(body) > MAX_BLOB_BYTES:
                    raise StorageUnavailable("Cloud data is too large to load safely.")
                etag = response.headers.get("ETag")
                if not etag:
                    raise StorageUnavailable("Cloud storage did not provide a concurrency token.")
                snapshot = Snapshot(decode(body), etag)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise StorageUnavailable("Private Blob is unavailable. Check its configuration and free usage quota.") from None
            snapshot = Snapshot(empty_document(), None)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise StorageUnavailable("Private Blob is unavailable. Please retry.") from None
        with self._cache_lock:
            self._cache = (time.monotonic(), copy.deepcopy(snapshot))
        return snapshot

    def save(self, document: dict, etag: str | None) -> Snapshot:
        headers = {
            "Authorization": f"Bearer {self.token}", "x-api-version": "12",
            "x-vercel-blob-access": "private", "x-add-random-suffix": "0",
            "x-allow-overwrite": "1" if etag else "0", "x-content-type": "application/gzip",
            "x-cache-control-max-age": "0", "Content-Type": "application/gzip",
        }
        if etag:
            headers["x-if-match"] = etag
        request = urllib.request.Request(
            "https://vercel.com/api/blob?" + urllib.parse.urlencode({"pathname": self.pathname}),
            data=encode(document), headers=headers, method="PUT",
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                result = json.load(response)
            if not result.get("etag"):
                raise StorageUnavailable("Cloud storage did not confirm the saved revision.")
            saved = Snapshot(copy.deepcopy(document), result["etag"])
        except urllib.error.HTTPError as exc:
            if exc.code in {409, 412}:
                raise Conflict("The data changed. Reload and retry.") from None
            raise StorageUnavailable("Could not save cloud data. Check the Blob free usage quota.") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise StorageUnavailable("Cloud save could not be confirmed. Reload before retrying.") from None
        with self._cache_lock:
            self._cache = (time.monotonic(), copy.deepcopy(saved))
        return saved

    def update(self, transform: Callable[[dict], object], *, attempts: int = 5) -> tuple[Snapshot, object]:
        for _ in range(attempts):
            original = self.load(fresh=True)
            modified = copy.deepcopy(original.document)
            result = transform(modified)
            if modified == original.document:
                return original, result
            try:
                return self.save(modified, original.etag), result
            except Conflict:
                continue
        raise Conflict("The data is busy. Please retry.")


class MemoryStore(BlobStore):
    """Offline test implementation with the same CAS semantics."""
    def __init__(self) -> None:
        self.document = empty_document()
        self.revision = 0
        self.lock = threading.Lock()

    def load(self, *, fresh: bool = False) -> Snapshot:
        with self.lock:
            return Snapshot(copy.deepcopy(self.document), str(self.revision))

    def save(self, document: dict, etag: str | None) -> Snapshot:
        with self.lock:
            if etag != str(self.revision):
                raise Conflict("Revision changed")
            self.document = copy.deepcopy(document)
            self.revision += 1
            return Snapshot(copy.deepcopy(self.document), str(self.revision))
