#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from common import (
    DATA_DIR,
    iso_now,
    iter_day_files,
    read_json,
    title_cache_key,
    translation_engine,
    write_json,
)


class TranslationError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"


TRANSLATE_TOOL = {
    "type": "function",
    "function": {
        "name": "translate_titles",
        "description": "Translate every English academic title into Simplified Chinese.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "translations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title_zh": {"type": "string"},
                        },
                        "required": ["id", "title_zh"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["translations"],
            "additionalProperties": False,
        },
    },
}


def load_llm_config() -> LLMConfig:
    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()
    missing = [
        name
        for name, value in (
            ("LLM_BASE_URL", base_url),
            ("LLM_API_KEY", api_key),
            ("LLM_MODEL", model),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "Missing translation configuration: " + ", ".join(missing)
        )
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit("LLM_BASE_URL must be an absolute http(s) URL, normally ending in /v1")
    return LLMConfig(base_url=base_url, api_key=api_key, model=model)


def pending_articles(engine: str, retranslate_existing: bool = False) -> list[tuple[Any, dict[str, Any]]]:
    records = []
    for path in reversed(list(iter_day_files())):
        payload = read_json(path, {})
        for article in payload.get("articles", []):
            needs_translation = article.get("translation_status") != "translated"
            needs_upgrade = retranslate_existing and article.get("translation_engine") != engine
            if article.get("title_en") and (needs_translation or needs_upgrade):
                records.append((path, article))
    supplements_path = DATA_DIR / "supplements.json"
    supplements = read_json(supplements_path, {})
    for bucket in ("late_additions", "date_pending"):
        for article in supplements.get(bucket, []):
            needs_translation = article.get("translation_status") != "translated"
            needs_upgrade = retranslate_existing and article.get("translation_engine") != engine
            if article.get("title_en") and (needs_translation or needs_upgrade):
                records.append((supplements_path, article))
    return records


def group_records_by_title(
    records: list[tuple[Any, dict[str, Any]]], engine: str
) -> list[tuple[str, list[tuple[Any, dict[str, Any]]]]]:
    grouped: dict[str, list[tuple[Any, dict[str, Any]]]] = {}
    for path, article in records:
        key = title_cache_key(article["title_en"], engine)
        grouped.setdefault(key, []).append((path, article))
    return list(grouped.items())


def request_json(
    url: str,
    payload: Any,
    headers: dict[str, str],
    retries: int = 3,
    timeout: int = 90,
) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
        method="POST",
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:600]
            except Exception:
                detail = ""
            authorization = headers.get("Authorization", "")
            if authorization:
                detail = detail.replace(authorization, "Bearer ***")
                token = authorization.removeprefix("Bearer ")
                detail = detail.replace(token, "***")
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt + 1 == retries:
                raise TranslationError(
                    f"Translation API HTTP {exc.code}: {detail}",
                    status_code=exc.code,
                    detail=detail,
                ) from exc
            time.sleep(2**attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt + 1 == retries:
                raise TranslationError(f"Translation API request failed: {exc}") from exc
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def build_payload(config: LLMConfig, items: list[dict[str, str]], force_tool_choice: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是专业学术标题翻译器。把每个英文论文标题翻译成自然、准确、简洁的简体中文。"
                    "保留公式、数字、缩写、专有名词和大小写含义；不要增加解释、摘要或前后缀。"
                    "必须对输入中的每个 id 返回一个且仅一个 title_zh。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"items": items}, ensure_ascii=False),
            },
        ],
        "tools": [TRANSLATE_TOOL],
    }
    if force_tool_choice:
        payload["tool_choice"] = {
            "type": "function",
            "function": {"name": "translate_titles"},
        }
    return payload


def parse_json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        raise TranslationError("Translation API returned non-JSON tool arguments")
    text = value.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TranslationError(f"Translation API returned invalid JSON: {exc}") from exc
    if isinstance(parsed, str):
        return parse_json_value(parsed)
    return parsed


def extract_translation_payload(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TranslationError("Translation API response has no choices")
    message = (choices[0] or {}).get("message") or {}
    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        if function.get("name") == "translate_titles":
            payload = parse_json_value(function.get("arguments"))
            if isinstance(payload, dict):
                return payload
            raise TranslationError("Translation tool arguments must be a JSON object")

    # A few OpenAI-compatible gateways ignore tool_choice. Accept JSON content only
    # as a compatibility fallback; semantic validation below remains mandatory.
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    payload = parse_json_value(content)
    if isinstance(payload, dict):
        return payload
    raise TranslationError("Translation API response is not a JSON object")


def validate_translation_payload(
    items: list[dict[str, str]], payload: dict[str, Any]
) -> list[str]:
    expected_ids = [item["id"] for item in items]
    expected_set = set(expected_ids)
    translations = payload.get("translations")
    if not isinstance(translations, list):
        raise TranslationError("Translation response is missing translations array")

    by_id: dict[str, str] = {}
    for item in translations:
        if not isinstance(item, dict):
            raise TranslationError("Translation response contains a non-object item")
        item_id = item.get("id")
        title_zh = item.get("title_zh")
        if not isinstance(item_id, str) or not item_id:
            raise TranslationError("Translation response contains an invalid id")
        if item_id in by_id:
            raise TranslationError(f"Translation response contains duplicate id: {item_id}")
        if item_id not in expected_set:
            raise TranslationError(f"Translation response contains unexpected id: {item_id}")
        if not isinstance(title_zh, str) or not title_zh.strip():
            raise TranslationError(f"Translation response contains an empty title for id: {item_id}")
        by_id[item_id] = title_zh.strip()

    if set(by_id) != expected_set:
        missing = sorted(expected_set - set(by_id))
        extra = sorted(set(by_id) - expected_set)
        raise TranslationError(f"Translation response id mismatch; missing={missing}, extra={extra}")
    return [by_id[item_id] for item_id in expected_ids]


def translate_llm_batch(config: LLMConfig, titles: list[str]) -> list[str]:
    items = [{"id": f"p{index:04d}", "title_en": title} for index, title in enumerate(titles, 1)]
    headers = {"Authorization": f"Bearer {config.api_key}"}
    last_error: TranslationError | None = None
    for attempt in range(2):
        force_tool_choice = True
        if (
            attempt == 1
            and last_error
            and last_error.status_code in {400, 422}
            and "tool_choice" in last_error.detail.lower()
        ):
            force_tool_choice = False
        try:
            response = request_json(
                config.endpoint,
                build_payload(config, items, force_tool_choice),
                headers,
            )
            return validate_translation_payload(items, extract_translation_payload(response))
        except TranslationError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    assert last_error is not None
    raise last_error


def translate_batch_job(
    config: LLMConfig,
    offset: int,
    batch: list[tuple[str, list[tuple[Any, dict[str, Any]]]]],
) -> tuple[
    int,
    list[tuple[str, list[tuple[Any, dict[str, Any]]]]],
    list[str] | None,
    Exception | None,
]:
    """Run one API batch in a worker; persistence stays in the main thread."""
    try:
        results = translate_llm_batch(config, [records[0][1]["title_en"] for _, records in batch])
    except Exception as exc:  # Keep other workers and resumable progress alive.
        return offset, batch, None, exc
    return offset, batch, results, None


def persist_records(records: list[tuple[Any, dict[str, Any]]]) -> None:
    by_path: dict[Any, list[dict[str, Any]]] = {}
    for path, article in records:
        by_path.setdefault(path, []).append(article)
    for path, articles in by_path.items():
        payload = read_json(path, {})
        if path.name == "supplements.json":
            lookup = {
                item.get("id"): item
                for bucket in ("late_additions", "date_pending")
                for item in payload.get(bucket, [])
            }
            payload["updated_at"] = iso_now()
        else:
            lookup = {item.get("id"): item for item in payload.get("articles", [])}
            payload["generated_at"] = iso_now()
        for article in articles:
            if article.get("id") in lookup:
                lookup[article["id"]].update(article)
        write_json(path, payload)


def persist_cache(path, cache: dict[str, Any], config: LLMConfig, engine: str) -> None:
    cache["provider"] = "openai-compatible"
    cache["engine"] = engine
    cache["model"] = config.model
    cache["updated_at"] = iso_now()
    write_json(path, cache)


def main(
    limit: int,
    batch_size: int,
    workers: int,
    retranslate_existing: bool = False,
) -> int:
    config = load_llm_config()
    if limit < 0:
        raise SystemExit("--limit must be zero or greater")
    if batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")
    if workers < 1:
        raise SystemExit("--workers must be at least 1")
    engine = translation_engine()
    translations_path = DATA_DIR / "translations.json"
    cache = read_json(
        translations_path,
        {"provider": "openai-compatible", "engine": engine, "model": config.model, "entries": {}},
    )
    cache.setdefault("entries", {})
    records = pending_articles(engine, retranslate_existing=retranslate_existing)
    grouped_records = group_records_by_title(records, engine)

    cached_records = []
    unresolved = []
    for key, matching_records in grouped_records:
        cached = cache["entries"].get(key)
        if cached and cached.get("translation") and cached.get("engine") == engine:
            for path, article in matching_records:
                article["title_zh"] = cached["translation"]
                article["translation_status"] = "translated"
                article["translation_engine"] = engine
                cached_records.append((path, article))
        else:
            unresolved.append((key, matching_records))

    if cached_records:
        persist_records(cached_records)

    unresolved = unresolved[:limit]
    translated_count = 0
    batches = [
        (offset, unresolved[offset : offset + batch_size])
        for offset in range(0, len(unresolved), batch_size)
    ]
    if batches:
        worker_count = min(workers, len(batches))
        print(
            f"Translation concurrency: {worker_count} workers; batch size {batch_size}",
            flush=True,
        )
        pending_results: dict[
            int,
            tuple[
                int,
                list[tuple[str, list[tuple[Any, dict[str, Any]]]]],
                list[str] | None,
                Exception | None,
            ],
        ] = {}
        next_offset = 0
        with ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="translation"
        ) as executor:
            future_to_offset = {
                executor.submit(translate_batch_job, config, offset, batch): offset
                for offset, batch in batches
            }
            for future in as_completed(future_to_offset):
                offset = future_to_offset[future]
                try:
                    result = future.result()
                except Exception as exc:  # Defensive guard for unexpected worker errors.
                    result = (offset, dict(batches)[offset], None, exc)
                pending_results[offset] = result

                # Apply completed batches in input order so data files and cache remain
                # deterministic even when API responses finish out of order.
                while next_offset in pending_results:
                    _, batch, results, error = pending_results.pop(next_offset)
                    if error is not None:
                        print(
                            f"WARN translation batch skipped at {next_offset}: {error}",
                            flush=True,
                        )
                    else:
                        assert results is not None
                        changed_records = []
                        for (key, matching_records), translated in zip(batch, results):
                            for path, article in matching_records:
                                article["title_zh"] = translated
                                article["translation_status"] = "translated"
                                article["translation_engine"] = engine
                                changed_records.append((path, article))
                            source = matching_records[0][1]["title_en"]
                            cache["entries"][key] = {
                                "source": source,
                                "translation": translated,
                                "engine": engine,
                                "model": config.model,
                                "translated_at": iso_now(),
                            }
                            translated_count += 1
                        persist_records(changed_records)
                        persist_cache(translations_path, cache, config, engine)
                    next_offset += len(batch)
                    print(
                        f"Translation progress: {min(next_offset, len(unresolved))}/{len(unresolved)}",
                        flush=True,
                    )

    persist_cache(translations_path, cache, config, engine)
    print(
        f"Provider openai-compatible; model {config.model}; translated {translated_count} new titles; "
        f"cache contains {len(cache['entries'])} entries"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--retranslate-existing",
        action="store_true",
        help="Replace translations produced by a different engine; intended for the one-off backfill.",
    )
    options = parser.parse_args()
    raise SystemExit(
        main(options.limit, options.batch_size, options.workers, options.retranslate_existing)
    )
