"""GitHub device authorization and credential-free, versioned Gist backups."""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from common import ROOT

CONFIG_PATH = Path(os.environ.get("PAPER_TRACKER_GIST_CONFIG", ROOT / "secrets/github-gist.json"))
AUTH_LOCK = threading.RLock()
FLOWS: dict[str, dict[str, Any]] = {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def protected_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".gist-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def settings() -> dict:
    if not CONFIG_PATH.exists():
        return {"client_id": os.environ.get("PAPER_TRACKER_GITHUB_CLIENT_ID", "")}
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise ValueError("GitHub connection settings are unreadable. Restore the protected settings file.") from None


def public_status() -> dict:
    value = settings()
    expired = bool(value.get("expires_at") and value["expires_at"] <= time.time())
    return {"client_id": value.get("client_id", ""), "connected": bool(value.get("access_token")) and not expired,
            "login": value.get("login", ""), "expired": expired, "last_gist_url": value.get("last_gist_url", "")}


def request(url: str, payload: dict | None = None, token: str | None = None, form: bool = False, method: str | None = None):
    # Every destination is constructed here, never supplied by backup contents.
    host = urllib.parse.urlsplit(url).hostname
    if host not in {"github.com", "api.github.com"} or not url.startswith("https://"):
        raise ValueError("Invalid GitHub destination.")
    headers = {"Accept": "application/json" if form else "application/vnd.github+json", "User-Agent": "PaperTracker/1.0"}
    if token:
        if host != "api.github.com":
            raise ValueError("Invalid token destination.")
        headers.update(Authorization=f"Bearer {token}", **{"X-GitHub-Api-Version": "2026-03-10"})
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        body = (urllib.parse.urlencode(payload) if form else json.dumps(payload, ensure_ascii=False)).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            raw = response.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError("GitHub response exceeds the backup size limit.")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Invalid GitHub response.")
        return value
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ValueError("GitHub authorization expired or was revoked. Connect GitHub again.") from None
        if exc.code == 403:
            raise ValueError("GitHub denied the request or its rate limit was reached. Try again later.") from None
        if exc.code == 404:
            raise ValueError("Gist not found or unavailable to this GitHub account.") from None
        raise ValueError("GitHub could not complete the request. Check your account and try again.") from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("GitHub connection failed. Check the network and try again.") from None


def begin_authorization(client_id: Any) -> dict:
    client_id = str(client_id or settings().get("client_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{8,100}", client_id):
        raise ValueError("Enter the Client ID of your GitHub OAuth app with Device Flow enabled.")
    with AUTH_LOCK:
        result = request("https://github.com/login/device/code", {"client_id": client_id, "scope": "gist"}, form=True)
        if result.get("error") or not all(isinstance(result.get(key), str) and result[key] for key in ("device_code", "user_code")):
            raise ValueError("GitHub could not start authorization. Check the Client ID and enable Device Flow.")
        duration = min(max(int(result.get("expires_in", 900)), 1), 900)
        interval = max(int(result.get("interval", 5)), 5)
        flow_id = secrets.token_urlsafe(24)
        FLOWS.clear()
        FLOWS[flow_id] = {"client_id": client_id, "device_code": result["device_code"], "expires": time.time() + duration,
                          "interval": interval, "next_poll": time.time() + interval}
        return {"flow_id": flow_id, "user_code": result["user_code"], "verification_uri": "https://github.com/login/device", "expires_in": duration, "interval": interval}


def poll_authorization(flow_id: Any) -> dict:
    with AUTH_LOCK:
        flow = FLOWS.get(str(flow_id))
        if not flow or flow["expires"] <= time.time():
            FLOWS.pop(str(flow_id), None)
            raise ValueError("GitHub authorization expired. Connect again.")
        if time.time() < flow["next_poll"]:
            return {"pending": True, "interval": flow["interval"]}
        result = request("https://github.com/login/oauth/access_token", {"client_id": flow["client_id"], "device_code": flow["device_code"],
                         "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}, form=True)
        error = result.get("error")
        if error in {"authorization_pending", "slow_down"}:
            if error == "slow_down":
                flow["interval"] += 5
            flow["next_poll"] = time.time() + flow["interval"]
            return {"pending": True, "interval": flow["interval"]}
        if error or not result.get("access_token"):
            FLOWS.pop(str(flow_id), None)
            raise ValueError("GitHub authorization was denied or expired. Connect again.")
        if "gist" not in re.split(r"[ ,]+", result.get("scope", "")):
            FLOWS.pop(str(flow_id), None)
            raise ValueError("Gist access was not granted. Connect again and allow Gist access.")
        identity = request("https://api.github.com/user", token=result["access_token"])
        if not isinstance(identity.get("login"), str) or not isinstance(identity.get("id"), int):
            raise ValueError("GitHub could not verify the connected account.")
        value = {"client_id": flow["client_id"], "access_token": result["access_token"], "login": identity["login"], "user_id": identity["id"]}
        if result.get("expires_in"):
            value["expires_at"] = time.time() + max(0, int(result["expires_in"]))
        protected_write(CONFIG_PATH, value)
        FLOWS.pop(str(flow_id), None)
        return public_status()


def disconnect() -> dict:
    with AUTH_LOCK:
        value = settings()
        protected_write(CONFIG_PATH, {"client_id": value.get("client_id", "")})
        FLOWS.clear()
        return public_status()


def credentials() -> dict:
    value = settings()
    if not public_status()["connected"]:
        raise ValueError("Connect GitHub before importing or exporting.")
    return value


def gist_id(value: Any) -> str:
    value = str(value or "").strip()
    if re.fullmatch(r"[a-fA-F0-9]{20,40}", value):
        return value.lower()
    parsed = urllib.parse.urlsplit(value)
    parts = parsed.path.strip("/").split("/")
    if parsed.scheme != "https" or parsed.netloc != "gist.github.com" or len(parts) != 2 or not re.fullmatch(r"[a-fA-F0-9]{20,40}", parts[-1]):
        raise ValueError("Enter a valid gist.github.com URL or Gist ID.")
    return parts[-1].lower()


def export_files(files: dict[str, dict]) -> dict:
    with AUTH_LOCK:
        value = credentials()
        result = request("https://api.github.com/gists", {"description": "Paper Tracker backup", "public": False, "files": files}, token=value["access_token"])
        identifier = gist_id(result.get("id"))
        url = f"https://gist.github.com/{value['login']}/{identifier}"
        value["last_gist_url"] = url
        protected_write(CONFIG_PATH, value)
        return {"gist_url": url}


def import_files(value: Any) -> dict[str, str]:
    identifier = gist_id(value)
    with AUTH_LOCK:
        auth = credentials()
        result = request(f"https://api.github.com/gists/{identifier}", token=auth["access_token"])
    if result.get("owner", {}).get("id") != auth.get("user_id"):
        raise ValueError("Choose a Gist owned by the connected GitHub account.")
    files = result.get("files")
    if result.get("truncated") or not isinstance(files, dict) or not 1 <= len(files) <= 200:
        raise ValueError("The Gist file list is missing or truncated.")
    output = {}
    total = 0
    for name, item in files.items():
        if not isinstance(item, dict) or item.get("truncated") or not isinstance(item.get("content"), str):
            raise ValueError("Gist content is truncated or incomplete. Export a new backup.")
        size = len(item["content"].encode("utf-8"))
        total += size
        if size > 900000 or total > 64 * 1024 * 1024:
            raise ValueError("The Gist exceeds the backup size limit.")
        output[name] = item["content"]
    return output
