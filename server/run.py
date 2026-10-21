#!/usr/bin/env python3
from __future__ import annotations

import argparse
import mimetypes
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PAPER_TRACKER_DATA_DIR", "/data"))
DAY_FILE = re.compile(r"^/data/days/\d{4}-\d{2}-\d{2}\.json$")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def static_target(url_path: str, data_dir: Path = DATA_DIR) -> Path | None:
    path = urllib.parse.unquote(urllib.parse.urlsplit(url_path).path)
    if path in {"/", "/index.html"}:
        return ROOT / "index.html"
    if path in {"/journal.json", "/favicon.ico"}:
        return ROOT / path.lstrip("/")
    if path.startswith("/assets/"):
        target = (ROOT / path.lstrip("/")).resolve()
        try:
            target.relative_to((ROOT / "assets").resolve())
        except ValueError:
            return None
        return target if target.is_file() else None
    if path in {"/data/manifest.json", "/data/journals.json"} or DAY_FILE.fullmatch(path):
        target = (data_dir / path.removeprefix("/data/")).resolve()
        try:
            target.relative_to(data_dir.resolve())
        except ValueError:
            return None
        return target if target.is_file() else None
    return None


def api_target(path: str, archive_port: int, manager_port: int) -> str | None:
    if path.startswith("/api/admin/"):
        return f"http://127.0.0.1:{manager_port}"
    if path.startswith("/api/"):
        return f"http://127.0.0.1:{archive_port}"
    return None


class GatewayHandler(BaseHTTPRequestHandler):
    archive_port = 0
    manager_port = 0
    data_dir = DATA_DIR

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/healthz":
            try:
                for port, endpoint in ((self.archive_port, "/api/healthz"), (self.manager_port, "/api/admin/healthz")):
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{endpoint}", timeout=1):
                        pass
            except (urllib.error.URLError, TimeoutError, OSError):
                self._send(503, b"Service unavailable\n", "text/plain; charset=utf-8", "no-store")
            else:
                self._send(200, b"ok\n", "text/plain; charset=utf-8", "no-store")
            return
        if path.startswith("/api/"):
            self._proxy()
            return
        target = static_target(self.path, self.data_dir)
        if target is None:
            self.send_error(404)
            return
        cache = "no-cache" if target == ROOT / "index.html" or target.suffix in {".css", ".js"} else "public, max-age=3600"
        if target.parent == ROOT / "assets" and target.suffix not in {".css", ".js"}:
            cache = "public, max-age=604800"
        self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream", cache)

    def do_HEAD(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        target = static_target(self.path, self.data_dir)
        if target is None or path.startswith("/api/"):
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if not urllib.parse.urlsplit(self.path).path.startswith("/api/"):
            self.send_error(404)
            return
        self._proxy()

    do_DELETE = do_POST

    def _proxy(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        base = api_target(path, self.archive_port, self.manager_port)
        if base is None:
            self.send_error(404)
            return
        if self.command in {"POST", "DELETE"}:
            origin = self.headers.get("Origin")
            if self.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host")):
                self._send(403, b'{"error":"Requests must come from this Paper Tracker site."}', "application/json", "no-store")
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length < 0 or length > 4 * 1024 * 1024:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else None
        headers = {"Origin": base}
        for name in ("Content-Type", "Prefer"):
            value = self.headers.get(name)
            if value:
                headers[name] = value
        request = urllib.request.Request(base + self.path, data=body, headers=headers, method=self.command)
        try:
            response: Any = urllib.request.urlopen(request, timeout=65)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, TimeoutError, OSError):
            self._send(502, b'{"error":"Service unavailable."}', "application/json", "no-store")
            return
        with response:
            payload = response.read()
            self._send(
                response.status,
                payload,
                response.headers.get("Content-Type", "application/json"),
                "no-store",
                response.headers.get("Preference-Applied"),
            )

    def _send(self, status: int, body: bytes, content_type: str, cache: str, preference: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        if preference:
            self.send_header("Preference-Applied", preference)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"web: {format % args}", flush=True)


def wait_for_service(url: str, process: subprocess.Popen, label: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited with status {process.returncode}.")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {label}.")


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Paper Tracker container services.")
    parser.add_argument("--host", default="0.0.0.0", help="Web interface bind address inside the container.")
    parser.add_argument("--port", type=int, default=8080, help="Web interface port (default: 8080).")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Persistent volume root (default: /data).")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.host not in {"0.0.0.0", "127.0.0.1", "localhost"}:
        parser.error("Use 0.0.0.0 or an IPv4 loopback address.")
    bind_host = "127.0.0.1" if args.host == "localhost" else args.host

    data_dir = args.data_dir.expanduser().resolve()
    archive_dir = data_dir / "archive"
    secrets_dir = data_dir / "secrets"
    data_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(secrets_dir, 0o700)

    archive_port, manager_port = free_port(), free_port()
    while manager_port == archive_port:
        manager_port = free_port()
    child_env = os.environ.copy()
    child_env.update({
        "HOST": "127.0.0.1",
        "PORT": str(archive_port),
        "ARCHIVE_FILE": str(archive_dir / "archive.json"),
        "MANIFEST_FILE": str(data_dir / "manifest.json"),
        "PAPER_TRACKER_DATA_DIR": str(data_dir),
        "PAPER_TRACKER_JOURNAL_CONFIG": str(data_dir / "journal-config.json"),
        "PAPER_TRACKER_LLM_CONFIG": str(secrets_dir / "llm-config.json"),
        "PAPER_TRACKER_GIST_CONFIG": str(secrets_dir / "github-gist.json"),
        "PAPER_TRACKER_ARCHIVE_FILE": str(archive_dir / "archive.json"),
        "ARCHIVE_API_URL": f"http://127.0.0.1:{archive_port}/api/archive",
        "MANAGER_HOST": "127.0.0.1",
        "MANAGER_PORT": str(manager_port),
    })
    archive_process = manager_process = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        archive_process = subprocess.Popen([sys.executable, str(ROOT / "server" / "archive-api.py")], cwd=ROOT, env=child_env)
        wait_for_service(f"http://127.0.0.1:{archive_port}/api/healthz", archive_process, "archive service")
        manager_process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "journal_manager.py")], cwd=ROOT, env=child_env)
        wait_for_service(f"http://127.0.0.1:{manager_port}/api/admin/healthz", manager_process, "management service")

        GatewayHandler.archive_port = archive_port
        GatewayHandler.manager_port = manager_port
        GatewayHandler.data_dir = data_dir
        server = ThreadingHTTPServer((bind_host, args.port), GatewayHandler)
        server.timeout = 0.5
        print(f"Paper Tracker: http://{bind_host}:{server.server_address[1]}/", flush=True)
        try:
            while not stopping:
                if archive_process.poll() is not None or manager_process.poll() is not None:
                    raise RuntimeError("An internal service exited; stopping the container.")
                server.handle_request()
        finally:
            server.server_close()
    except OSError as exc:
        print(f"Could not start Paper Tracker; check /data permissions and the web port: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if manager_process is not None:
            stop_process(manager_process)
        if archive_process is not None:
            stop_process(archive_process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
