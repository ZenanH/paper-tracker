from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from common import ROOT, iso_now

CONFIG_PATH = Path(
    os.environ.get("PAPER_TRACKER_LLM_CONFIG", ROOT / "secrets" / "llm-config.json")
).expanduser().resolve()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _environment_settings() -> dict[str, str]:
    return {
        "base_url": _clean(os.environ.get("LLM_BASE_URL")),
        "api_key": _clean(os.environ.get("LLM_API_KEY")),
        "model": _clean(os.environ.get("LLM_MODEL")),
    }


def read_llm_settings() -> dict[str, str]:
    if not CONFIG_PATH.exists():
        return _environment_settings()
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("模型配置文件无法读取或已损坏") from exc
    if not isinstance(payload, dict):
        raise ValueError("模型配置文件必须是 JSON 对象")
    return {
        "base_url": _clean(payload.get("base_url")),
        "api_key": _clean(payload.get("api_key")),
        "model": _clean(payload.get("model")),
    }


def llm_is_configured() -> bool:
    try:
        settings = read_llm_settings()
    except ValueError:
        return False
    return all(settings.values())


def configured_llm_model() -> str:
    try:
        return read_llm_settings()["model"]
    except ValueError:
        return ""


def public_llm_settings() -> dict[str, Any]:
    try:
        settings = read_llm_settings()
        error = None
    except ValueError as exc:
        settings = {"base_url": "", "api_key": "", "model": ""}
        error = str(exc)
    return {
        "configured": all(settings.values()),
        "base_url": settings["base_url"],
        "model": settings["model"],
        "api_key_set": bool(settings["api_key"]),
        "error": error,
    }


def _validate_base_url(base_url: str) -> None:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL 必须是完整的 http(s) 地址，通常以 /v1 结尾")
    if parsed.username or parsed.password:
        raise ValueError("Base URL 不能包含用户名或密码")


def _write_secure(payload: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_PATH.parent, 0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=".llm-config.", dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, CONFIG_PATH)
        os.chmod(CONFIG_PATH, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def save_llm_settings(base_url: Any, api_key: Any, model: Any) -> dict[str, Any]:
    base_url = _clean(base_url)
    api_key = _clean(api_key)
    model = _clean(model)
    if not api_key:
        api_key = read_llm_settings()["api_key"]
    if not base_url or not api_key or not model:
        raise ValueError("Base URL、API Key 和模型名称都必须填写")
    _validate_base_url(base_url)
    _write_secure(
        {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "updated_at": iso_now(),
        }
    )
    return public_llm_settings()


def disable_llm_settings() -> dict[str, Any]:
    _write_secure(
        {"base_url": "", "api_key": "", "model": "", "updated_at": iso_now()}
    )
    return public_llm_settings()
