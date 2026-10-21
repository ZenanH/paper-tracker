"""Authenticated FastAPI entrypoint for Vercel; no local daemon or timer."""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import time
import urllib.parse
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware

from cloud.runtime import Runtime
from cloud.storage import Conflict, StorageUnavailable
from cloud.workspace import ROOT

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")
LOGGER = logging.getLogger("paper-tracker")
SESSION_COOKIE = "paper_tracker_session"


@lru_cache(maxsize=1)
def runtime() -> Runtime:
    return Runtime()


def _password() -> str:
    return os.environ.get("PAPER_TRACKER_PASSWORD", "")


def session_value(expires: int) -> str:
    signature = hmac.new(_password().encode(), f"paper-tracker:{expires}".encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def authenticated(request: Request) -> bool:
    if len(_password()) < 16:
        return False
    value = request.cookies.get(SESSION_COOKIE, "")
    try:
        expires = int(value.split(".", 1)[0])
    except (ValueError, TypeError):
        return False
    return time.time() < expires <= time.time() + 32 * 86400 and hmac.compare_digest(value, session_value(expires))


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    return request.headers.get("sec-fetch-site") != "cross-site" and (not origin or urllib.parse.urlsplit(origin).netloc == request.headers.get("host"))


def login_page(error: str = "") -> str:
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · Paper Tracker</title><link rel="icon" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/styles.css"><style>main{max-width:400px;margin:12vh auto;padding:24px}form{display:grid;gap:16px;padding:28px}h1{font-size:22px}input{width:100%;min-height:44px;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink)}p{overflow-wrap:anywhere}</style></head><body><main><form class="surface" method="post" action="/login"><h1>Paper Tracker</h1><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus maxlength="1024"><button class="button primary" type="submit">Sign in</button><p role="alert">''' + html.escape(error) + '''</p></form></main></body></html>'''


@app.middleware("http")
async def headers_and_auth(request: Request, call_next):
    path = request.url.path
    public = path in {"/login", "/healthz", "/favicon.ico"} or path.startswith("/assets/") or path.startswith("/api/cron/")
    if not public and not authenticated(request):
        if path.startswith(("/api/", "/data/")):
            return JSONResponse({"error": "Sign in to use Paper Tracker."}, status_code=401, headers={"Cache-Control": "no-store"})
        return RedirectResponse("/login", status_code=303)
    if request.method in {"POST", "DELETE", "PUT", "PATCH"} and not same_origin(request):
        return JSONResponse({"error": "Requests must come from this Paper Tracker site."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if not path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/login")
async def sign_in():
    return HTMLResponse(login_page())


@app.post("/login")
async def sign_in_submit(request: Request):
    expected = _password()
    if len(expected) < 16:
        return HTMLResponse(login_page("Set PAPER_TRACKER_PASSWORD to at least 16 characters in Vercel, then redeploy."), status_code=503)
    body = await request.body()
    if len(body) > 4096:
        return HTMLResponse(login_page("Incorrect password."), status_code=400)
    candidate = urllib.parse.parse_qs(body.decode("utf-8", errors="replace")).get("password", [""])[0]
    if not hmac.compare_digest(hashlib.sha256(candidate.encode()).digest(), hashlib.sha256(expected.encode()).digest()):
        import asyncio
        await asyncio.sleep(1)
        return HTMLResponse(login_page("Incorrect password."), status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, session_value(int(time.time()) + 30 * 86400), max_age=30 * 86400,
                        secure=request.url.scheme == "https", httponly=True, samesite="lax")
    return response


@app.get("/healthz")
async def health():
    return {"status": "ok", "storage_configured": bool(os.environ.get("BLOB_READ_WRITE_TOKEN")),
            "access_configured": len(_password()) >= 16, "scheduler_configured": len(os.environ.get("CRON_SECRET", "")) >= 32}


@app.get("/")
@app.get("/index.html")
async def index():
    return FileResponse(ROOT / "index.html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(ROOT / "favicon.ico")


@app.get("/api/cron/morning")
@app.get("/api/cron/afternoon")
async def cron(request: Request):
    secret = os.environ.get("CRON_SECRET", "")
    if len(secret) < 32 or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {secret}"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        status, value, headers = await run_in_threadpool(runtime().scheduled)
        return JSONResponse(value, status_code=status, headers=headers)
    except (Conflict, StorageUnavailable):
        return JSONResponse({"error": "Scheduled update could not be started. Check cloud storage and task status."}, status_code=503)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE"])
@app.get("/data/{path:path}")
async def api(request: Request, path: str):
    payload = None
    if request.method == "POST":
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            return JSONResponse({"error": "Use an application/json request."}, status_code=400)
        body = await request.body()
        limit = 2 * 1024 * 1024 if request.url.path == "/api/archive" else 128 * 1024
        if len(body) > limit:
            return JSONResponse({"error": "The request is too large."}, status_code=413)
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"error": "Use a JSON object."}, status_code=400)
    try:
        status, value, headers = await run_in_threadpool(runtime().request, request.method, request.url.path, payload, dict(request.headers))
        return JSONResponse(value, status_code=status, headers=headers)
    except Conflict as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except StorageUnavailable as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception:
        LOGGER.error("Cloud request failed at %s", request.url.path)
        return JSONResponse({"error": "The cloud operation failed. Saved data has been retained; reload and retry."}, status_code=500)
