from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from common import DATA_DIR, iso_now

CONFIG_PATH = Path(
    os.environ.get("PAPER_TRACKER_LLM_CONFIG", DATA_DIR / "secrets" / "llm-config.json")
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
        raise ValueError("The model settings file is unreadable or corrupt.") from exc
    if not isinstance(payload, dict):
        raise ValueError("The model settings file must contain a JSON object.")
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
        raise ValueError("Base URL must be a complete HTTP(S) URL, usually ending in /v1.")
    if parsed.username or parsed.password:
        raise ValueError("Base URL must not contain a username or password.")


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
        raise ValueError("Base URL, API key, and model name are all required.")
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


def verify_llm_settings(base_url: Any, api_key: Any, model: Any) -> dict[str, Any]:
    """Verify the unsaved form with an actual structured-tool request; never echo errors or keys."""
    from translate import LLMConfig, TranslationError, extract_translation_payload, request_json
    from title_filter import build_payload, validate_classification_payload

    base_url, api_key, model = _clean(base_url), _clean(api_key), _clean(model)
    if not api_key:
        api_key = read_llm_settings()["api_key"]
    if not base_url or not api_key or not model:
        raise ValueError("Enter a Base URL, API key, and model name.")
    _validate_base_url(base_url)
    config = LLMConfig(base_url, api_key, model)
    items = [{"id": "connection-check", "title_en": "Numerical methods for solid mechanics"}]
    for force in (True, False):
        try:
            response = request_json(
                config.endpoint, build_payload(config, items, force),
                {"Authorization": f"Bearer {api_key}"}, retries=1, timeout=12,
            )
            validate_classification_payload(
                items, extract_translation_payload(response, function_name="classify_titles")
            )
            return {"verified": True, "checked_at": iso_now(), "model": model}
        except TranslationError as exc:
            if force and exc.status_code in {400, 422} and "tool_choice" in exc.detail.lower():
                continue
            raise ValueError("Connection test failed. Check the URL, key, model, and function tool support. Settings were not saved.") from None
    raise ValueError("Model verification failed.")
