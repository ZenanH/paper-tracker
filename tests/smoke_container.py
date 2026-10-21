"""Exercise a built image with disposable data; no provider credentials used."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def request(base: str, path: str, payload: dict | None = None):
    req = Request(base + path, data=None if payload is None else json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json", "Origin": base})
    with urlopen(req, timeout=10) as response:
        body = response.read()
        return json.loads(body) if response.headers.get_content_type() == "application/json" else body


def wait_ready(base: str) -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            request(base, "/healthz")
            return
        except (OSError, URLError):
            time.sleep(0.25)
    raise AssertionError("Container services did not become ready")


def main() -> None:
    image = sys.argv[1] if len(sys.argv) > 1 else "paper-tracker:ci"
    assert docker("image", "inspect", "--format", "{{.Config.User}}", image) == "1000:1000"
    name = "paper-tracker-smoke-" + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix="paper-tracker-smoke-") as directory:
        root = Path(directory)
        stamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        today = stamp[:10]
        # Empty followed journals avoid all external source/model requests.
        fixtures = {
            "journal-config.json": {"categories": {}, "config_updated_at": stamp},
            "journals.json": {"journals": [], "metrics_checked_at": stamp},
            "manifest.json": {"collection_checked_at": stamp, "available_dates": [],
                              "retention": {"start": today, "end": today}},
        }
        for filename, value in fixtures.items():
            (root / filename).write_text(json.dumps(value))
        try:
            for attempt in range(2):
                docker("run", "--detach", "--name", name, "--init", "--user", f"{os.getuid()}:{os.getgid()}",
                       "--publish", "127.0.0.1::8080", "--volume", f"{root}:/data", image)
                base = "http://" + docker("port", name, "8080/tcp").splitlines()[0]
                wait_ready(base)
                assert b"Paper Tracker" in request(base, "/")
                manifest = request(base, "/data/manifest.json")
                for day in manifest["available_dates"]:
                    assert request(base, f"/data/days/{day}.json")["articles"] == []
                if attempt == 0:
                    request(base, "/api/admin/run", {"mode": "daily"})
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        status = request(base, "/api/admin/status")["job"]["status"]
                        if status != "running":
                            assert status == "success", status
                            break
                        time.sleep(0.2)
                    else:
                        raise AssertionError("Refresh did not complete")
                    article = {"id": "smoke-favorite", "journal_id": "example", "date": today,
                               "title_en": "Persistent mechanics title", "url": "https://example.com/paper"}
                    request(base, "/api/archive", {"action": "favorite", "items": [article]})
                    request(base, "/api/admin/llm", {"base_url": "https://models.invalid/v1",
                                                    "api_key": "offline-smoke-key", "model": "offline-smoke"})
                assert "smoke-favorite" in request(base, "/api/archive")["favorites"]
                assert (root / "archive/archive.json").is_file()
                config = root / "secrets/llm-config.json"
                assert json.loads(config.read_text())["api_key"] == "offline-smoke-key"
                assert config.stat().st_mode & 0o777 == 0o600
                assert config.parent.stat().st_mode & 0o777 == 0o700
                assert "offline-smoke-key" not in json.dumps(request(base, "/api/admin/journals"))
                for path in ("/data/secrets/llm-config.json", "/data/archive/archive.json",
                             "/data/task-status.json", "/data/excluded-papers.json", "/secrets/llm-config.json"):
                    try:
                        request(base, path)
                    except HTTPError as exc:
                        assert exc.code == 404, (path, exc.code)
                        exc.close()
                    else:
                        raise AssertionError(f"Private file was exposed: {path}")
                docker("stop", "--time", "25", name)
                assert docker("inspect", "--format", "{{.State.ExitCode}}", name) == "0"
                docker("rm", name)
            print("Container smoke test passed: refresh, /data writes, private files, recreate persistence and SIGTERM.")
        except Exception:
            subprocess.run(["docker", "logs", name], check=False)
            raise
        finally:
            subprocess.run(["docker", "rm", "--force", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
