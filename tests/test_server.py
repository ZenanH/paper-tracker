import json
import os
import queue
import signal
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import run as container_server
from http_service import ThreadingHTTPServer
urlopen = build_opener(ProxyHandler({})).open


class ServerTests(unittest.TestCase):
    def test_fresh_volume_initializes_32_journals_and_keeps_user_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(__file__).resolve().parents[1] / "scripts"
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("PAPER_TRACKER_")}
            environment["PAPER_TRACKER_DATA_DIR"] = directory
            code = (
                "import sys; sys.path.insert(0,sys.argv[1]); "
                "import journal_manager; journal_manager.sync_if_needed()"
            )
            arguments = [sys.executable, "-B", "-c", code, str(scripts)]
            subprocess.check_output(arguments, env=environment)
            root = Path(directory)
            config_path = root / "journal-config.json"
            metadata_path = root / "journals.json"
            self.assertEqual(len(json.loads(metadata_path.read_text())["journals"]), 32)
            self.assertEqual(json.loads((root / "manifest.json").read_text())["journal_count"], 32)
            config = json.loads(config_path.read_text())
            removed = config["categories"]["顶刊"].pop()["name"]
            config_path.write_text(json.dumps(config))
            metadata_path.unlink()
            subprocess.check_output(arguments, env=environment)
            names = [item["name"] for item in json.loads(metadata_path.read_text())["journals"]]
            self.assertEqual(len(names), 31)
            self.assertNotIn(removed, names)
            config["categories"] = {}
            config_path.write_text(json.dumps(config))
            metadata_path.unlink()
            subprocess.check_output(arguments, env=environment)
            self.assertEqual(json.loads(metadata_path.read_text())["journals"], [])

    def test_listeners_do_not_require_reverse_dns(self):
        with patch("socket.getfqdn", side_effect=RuntimeError("DNS unavailable")):
            server = ThreadingHTTPServer(("127.0.0.1", 0), container_server.GatewayHandler)
            try:
                self.assertEqual(server.server_name, "127.0.0.1")
            finally:
                server.server_close()

    def test_all_default_storage_paths_follow_the_data_root(self):
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(__file__).resolve().parents[1] / "scripts"
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("PAPER_TRACKER_")}
            environment["PAPER_TRACKER_DATA_DIR"] = directory
            code = (
                "import sys,json; sys.path.insert(0,sys.argv[1]); "
                "import common,llm_settings,archive_recovery; "
                "print(json.dumps([str(common.DATA_DIR),str(llm_settings.CONFIG_PATH),"
                "str(archive_recovery.ARCHIVE_FILE)]))"
            )
            actual = json.loads(subprocess.check_output([sys.executable, "-B", "-c", code, str(scripts)], env=environment))
            root = Path(directory).resolve()
            self.assertEqual(actual, [str(root), str(root / "secrets/llm-config.json"),
                                      str(root / "archive/archive.json")])

    def test_static_allowlist_serves_only_public_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            (root / "journals.json").write_text("{}", encoding="utf-8")
            (root / "days").mkdir()
            (root / "days" / "2026-10-21.json").write_text("{}", encoding="utf-8")
            (root / "excluded-papers.json").write_text("secret", encoding="utf-8")
            self.assertEqual(container_server.static_target("/data/manifest.json", root).read_text(), "{}")
            self.assertEqual(container_server.static_target("/data/journals.json", root).read_text(), "{}")
            self.assertEqual(container_server.static_target("/data/days/2026-10-21.json", root).read_text(), "{}")
            self.assertIsNone(container_server.static_target("/data/excluded-papers.json", root))
            self.assertIsNone(container_server.static_target("/data/days/../../secrets.json", root))
            self.assertIsNone(container_server.static_target("/data/archive/archive.json", root))
            self.assertIsNone(container_server.static_target("/data/secrets/llm-config.json", root))

    def test_api_routes_choose_manager_before_archive(self):
        self.assertEqual(container_server.api_target("/api/admin/healthz", 18080, 18090), "http://127.0.0.1:18090")
        self.assertEqual(container_server.api_target("/api/archive", 18080, 18090), "http://127.0.0.1:18080")
        self.assertIsNone(container_server.api_target("/assets/app.js", 18080, 18090))

    def test_gateway_proxies_management_requests_same_origin(self):
        class BackendHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                length = int(self.headers.get('Content-Length', 0))
                payload = json.loads(self.rfile.read(length)) if length else None
                body = json.dumps({"path": self.path, "origin": self.headers.get("Origin"), "payload": payload, "method": self.command}).encode()
                self.send_response(202 if self.command == 'POST' else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET
            do_DELETE = do_GET

            def log_message(self, *_args):
                pass

        backend = ThreadingHTTPServer(("127.0.0.1", 0), BackendHandler)
        frontend = ThreadingHTTPServer(("127.0.0.1", 0), container_server.GatewayHandler)
        frontend.RequestHandlerClass.manager_port = backend.server_address[1]
        frontend.RequestHandlerClass.archive_port = backend.server_address[1]
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (backend, frontend)]
        for thread in threads:
            thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{frontend.server_address[1]}/api/admin/healthz?probe=1") as response:
                payload = json.load(response)
            self.assertEqual(payload["path"], "/api/admin/healthz?probe=1")
            self.assertEqual(payload["origin"], f"http://127.0.0.1:{backend.server_address[1]}")
            origin = f"http://127.0.0.1:{frontend.server_address[1]}"
            request = Request(origin + '/api/admin/run', data=b'{"mode":"daily"}', headers={"Content-Type": "application/json", "Origin": origin}, method='POST')
            with urlopen(request) as response:
                self.assertEqual(response.status, 202)
                payload = json.load(response)
            self.assertEqual(payload['payload'], {'mode': 'daily'})
            self.assertEqual(payload['path'], '/api/admin/run')
            foreign = Request(origin + '/api/admin/run', data=b'{"mode":"daily"}', headers={"Content-Type": "application/json", "Origin": 'https://another.example'}, method='POST')
            with self.assertRaises(HTTPError) as error:
                urlopen(foreign)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            with urlopen(Request(origin + '/api/admin/journals/example', method='DELETE')) as response:
                self.assertEqual(json.load(response)['method'], 'DELETE')
        finally:
            for server in (frontend, backend):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)

    def test_health_check_fails_when_an_internal_service_is_unavailable(self):
        frontend = ThreadingHTTPServer(("127.0.0.1", 0), container_server.GatewayHandler)
        thread = threading.Thread(target=frontend.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(container_server.INTERNAL_HTTP, "open", side_effect=OSError("offline")):
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"http://127.0.0.1:{frontend.server_address[1]}/healthz")
                self.assertEqual(error.exception.code, 503)
                error.exception.close()
        finally:
            frontend.shutdown()
            frontend.server_close()
            thread.join(timeout=2)

    def test_container_entrypoint_uses_one_volume_and_stops_on_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            data_dir.mkdir()
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            stamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
            (data_dir / "journal-config.json").write_text(json.dumps({"categories": {}, "config_updated_at": stamp}))
            (data_dir / "journals.json").write_text(json.dumps({"journals": []}))
            (data_dir / "manifest.json").write_text(json.dumps({
                "collection_checked_at": stamp,
                "retention": {"start": today.replace(day=1).isoformat(), "end": today.isoformat()},
                "available_dates": [],
            }))
            process = subprocess.Popen([
                sys.executable, str(Path(container_server.__file__)),
                "--host", "127.0.0.1", "--port", "0",
                "--data-dir", str(data_dir),
            ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            output = queue.Queue()
            logs = []

            def read_output():
                for line in process.stdout:
                    output.put(line)

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            try:
                deadline = time.monotonic() + 75
                url = None
                while time.monotonic() < deadline:
                    try:
                        line = output.get(timeout=0.25)
                    except queue.Empty:
                        if process.poll() is not None:
                            break
                        continue
                    logs.append(line)
                    if line.startswith("Paper Tracker: "):
                        url = line.split(": ", 1)[1].strip()
                        break
                self.assertIsNotNone(url, "Container server did not become ready:\n" + "".join(logs))
                with urlopen(url) as response:
                    self.assertIn("Paper Tracker", response.read().decode("utf-8"))
                with urlopen(url + "data/manifest.json") as response:
                    self.assertEqual(json.load(response)["available_dates"], [])
                with urlopen(url + "api/archive") as response:
                    self.assertEqual(json.load(response)["favorites"], {})
                with urlopen(url + "api/admin/healthz") as response:
                    self.assertEqual(json.load(response)["status"], "ok")
                article = {"id": "persistent-favorite", "journal_id": "example", "title_en": "Mechanics", "date": today.isoformat()}
                request = Request(url + "api/archive", data=json.dumps({"action": "favorite", "items": [article]}).encode(), headers={"Content-Type": "application/json"})
                with urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                saved = json.loads((data_dir / "archive/archive.json").read_text())
                self.assertIn(article["id"], saved["favorites"])
                self.assertEqual((data_dir / "secrets").stat().st_mode & 0o777, 0o700)
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=12), 0)
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                reader.join(timeout=2)
                if process.stdout:
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
