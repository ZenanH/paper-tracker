from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from common import (
    TITLE_FILTER_RULE_VERSION,
    iso_now,
    read_json,
    retention_start,
    title_cache_key,
    translation_engine,
    write_json,
)
from translate import (
    LLMConfig,
    TranslationError,
    extract_translation_payload,
    load_llm_config,
    request_json,
)

RULE_VERSION = TITLE_FILTER_RULE_VERSION
EXCLUDED_CATEGORIES = {"medicine", "biology", "chemistry", "humanities"}
ALLOWED_CATEGORIES = {"keep", *EXCLUDED_CATEGORIES, "uncertain"}
EXCLUDE_CONFIDENCE = 0.90
BATCH_SIZE = 64
BIOMECHANICS_PATTERN = re.compile(
    r"\b(?:biomechan\w*|mechanobiolog\w*|musculoskeletal\s+mechanic\w*|"
    r"tissue\s+mechanic\w*|cell(?:ular)?\s+mechanic\w*)\b",
    re.IGNORECASE,
)

CLASSIFY_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_titles",
        "description": "Classify academic paper titles for a configurable subject filter.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "classifications": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "category": {
                                "type": "string",
                                "enum": sorted(ALLOWED_CATEGORIES),
                            },
                            "confidence": {"type": "number"},
                        },
                        "required": ["id", "category", "confidence"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["classifications"],
            "additionalProperties": False,
        },
    },
}


def is_biomechanics_title(title: str) -> bool:
    return bool(BIOMECHANICS_PATTERN.search(title))


def build_payload(
    config: LLMConfig, items: list[dict[str, str]], force_tool_choice: bool
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你负责按论文英文标题做保守的学科过滤分类。可选类别只有 keep、medicine、biology、"
                    "chemistry、humanities、uncertain。medicine 是临床医学、诊断、治疗、药理等；"
                    "biology 是以生物过程、物种、细胞或分子生物学为核心；chemistry 是化学合成、"
                    "反应、分子化学或纯化学材料研究；humanities 包含文学、语言、历史、哲学和其他"
                    "人文学科；标题中的 literature review 或 systematic literature review 仅表示文献"
                    "综述，并不自动属于 humanities。交叉学科、信息科学、工程、物理、数学、地球与"
                    "环境研究通常为 keep。"
                    "生物力学及其明确以力学为核心的方向必须判为 keep，包括 biomechanics、"
                    "biomechanical engineering、musculoskeletal mechanics、tissue/cell mechanics"
                    "以及 mechanobiology。仅根据标题无法可靠判断时使用 uncertain，不要猜测。"
                    "confidence 必须是 0 到 1 之间的数字，并为每个输入 id 返回且仅返回一项。"
                ),
            },
            {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
        ],
        "tools": [CLASSIFY_TOOL],
    }
    if force_tool_choice:
        payload["tool_choice"] = {
            "type": "function",
            "function": {"name": "classify_titles"},
        }
    return payload


def validate_classification_payload(
    items: list[dict[str, str]], payload: dict[str, Any]
) -> list[dict[str, Any]]:
    expected_ids = [item["id"] for item in items]
    expected_set = set(expected_ids)
    classifications = payload.get("classifications")
    if not isinstance(classifications, list):
        raise TranslationError("Classification response is missing classifications array")

    by_id: dict[str, dict[str, Any]] = {}
    for item in classifications:
        if not isinstance(item, dict):
            raise TranslationError("Classification response contains a non-object item")
        item_id = item.get("id")
        category = item.get("category")
        confidence = item.get("confidence")
        if not isinstance(item_id, str) or item_id not in expected_set:
            raise TranslationError(f"Classification response contains invalid id: {item_id}")
        if item_id in by_id:
            raise TranslationError(f"Classification response contains duplicate id: {item_id}")
        if category not in ALLOWED_CATEGORIES:
            raise TranslationError(f"Classification response contains invalid category: {category}")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise TranslationError(f"Classification response contains invalid confidence: {item_id}")
        if not 0 <= float(confidence) <= 1:
            raise TranslationError(f"Classification confidence is outside 0..1: {item_id}")
        by_id[item_id] = {"category": category, "confidence": float(confidence)}

    if set(by_id) != expected_set:
        missing = sorted(expected_set - set(by_id))
        raise TranslationError(f"Classification response id mismatch; missing={missing}")
    return [by_id[item_id] for item_id in expected_ids]


def classify_llm_batch(config: LLMConfig, titles: list[str]) -> list[dict[str, Any]]:
    items = [
        {"id": f"p{index:04d}", "title_en": title}
        for index, title in enumerate(titles, 1)
    ]
    headers = {"Authorization": f"Bearer {config.api_key}"}
    last_error: TranslationError | None = None
    for attempt in range(2):
        force_tool_choice = not (
            attempt == 1
            and last_error
            and last_error.status_code in {400, 422}
            and "tool_choice" in last_error.detail.lower()
        )
        try:
            response = request_json(
                config.endpoint,
                build_payload(config, items, force_tool_choice),
                headers,
            )
            tool_payload = extract_translation_payload(response, function_name="classify_titles")
            return validate_classification_payload(items, tool_payload)
        except TranslationError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    assert last_error is not None
    raise last_error


class TitleFilter:
    def __init__(self, data_dir: Path, reference: date) -> None:
        self.data_dir = data_dir
        self.reference = reference
        self.config_path = data_dir / "filter-config.json"
        self.cache_path = data_dir / "title-classifications.json"
        config = read_json(self.config_path, {})
        selected = config.get("journal_ids", []) if isinstance(config, dict) else []
        self.journal_ids = {
            item for item in selected if isinstance(item, str) and item.strip()
        }
        cached = read_json(self.cache_path, {})
        self.cache = cached if isinstance(cached, dict) else {}
        if not isinstance(self.cache.get("entries"), dict):
            self.cache["entries"] = {}
        self.engine = translation_engine()
        self.changed = False

    def enabled_for(self, journal_id: str) -> bool:
        return journal_id in self.journal_ids

    def cache_key(self, title: str) -> str:
        namespace = f"title-filter:{self.engine}:{RULE_VERSION}"
        return title_cache_key(title.casefold(), namespace)

    def _load_llm_config(self) -> LLMConfig:
        try:
            return load_llm_config()
        except SystemExit as exc:
            raise RuntimeError(str(exc)) from exc

    def classify_articles(
        self, articles: list[dict[str, Any]], journal_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        stats = {
            "classified": 0,
            "cached": 0,
            "excluded": 0,
            "biomechanics_kept": 0,
            "failed_open": 0,
        }
        if not articles:
            return [], stats

        entries = self.cache["entries"]
        decisions: dict[str, dict[str, Any]] = {}
        unresolved: dict[str, str] = {}
        observed_at = iso_now()
        for article in articles:
            title = article["title_en"]
            key = self.cache_key(title)
            if key in decisions:
                continue
            if is_biomechanics_title(title):
                decisions[key] = {"category": "keep", "confidence": 1.0}
                stats["biomechanics_kept"] += 1
                continue
            cached = entries.get(key)
            if (
                isinstance(cached, dict)
                and cached.get("engine") == self.engine
                and cached.get("rule_version") == RULE_VERSION
                and cached.get("category") in ALLOWED_CATEGORIES
            ):
                decisions[key] = {
                    "category": cached["category"],
                    "confidence": float(cached.get("confidence", 0)),
                }
                cached["journal_ids"] = list(
                    dict.fromkeys([*(cached.get("journal_ids") or []), journal_id])
                )
                cached["last_seen_at"] = observed_at
                stats["cached"] += 1
                self.changed = True
            else:
                unresolved[key] = title

        if unresolved:
            keys = list(unresolved)
            try:
                llm_config = self._load_llm_config()
            except RuntimeError as exc:
                stats["failed_open"] += len(unresolved)
                print(f"WARN title filtering skipped; keeping {len(unresolved)} titles: {exc}")
                for key in unresolved:
                    decisions.setdefault(key, {"category": "keep", "confidence": 0.0})
            else:
                for offset in range(0, len(keys), BATCH_SIZE):
                    batch_keys = keys[offset : offset + BATCH_SIZE]
                    try:
                        results = classify_llm_batch(
                            llm_config, [unresolved[key] for key in batch_keys]
                        )
                    except TranslationError as exc:
                        stats["failed_open"] += len(batch_keys)
                        print(
                            "WARN title filtering batch skipped; "
                            f"keeping {len(batch_keys)} titles: {exc}"
                        )
                        for key in batch_keys:
                            decisions[key] = {"category": "keep", "confidence": 0.0}
                        continue
                    for key, result in zip(batch_keys, results):
                        decisions[key] = result
                        entries[key] = {
                            "source": unresolved[key],
                            "category": result["category"],
                            "confidence": result["confidence"],
                            "excluded": result["category"] in EXCLUDED_CATEGORIES
                            and result["confidence"] >= EXCLUDE_CONFIDENCE,
                            "provider": "openai-compatible",
                            "engine": self.engine,
                            "model": llm_config.model,
                            "rule_version": RULE_VERSION,
                            "classified_at": observed_at,
                            "last_seen_at": observed_at,
                            "journal_ids": [journal_id],
                        }
                        stats["classified"] += 1
                        self.changed = True

        retained = []
        for article in articles:
            decision = decisions[self.cache_key(article["title_en"])]
            excluded = (
                decision["category"] in EXCLUDED_CATEGORIES
                and decision["confidence"] >= EXCLUDE_CONFIDENCE
            )
            if excluded:
                stats["excluded"] += 1
            else:
                retained.append(article)
        self.prune_and_save()
        return retained, stats

    def prune_and_save(self) -> None:
        cutoff = retention_start(self.reference)
        entries = self.cache["entries"]
        retained = {}
        for key, entry in entries.items():
            try:
                last_seen = datetime.fromisoformat(entry["last_seen_at"]).date()
            except (KeyError, TypeError, ValueError):
                self.changed = True
                continue
            if last_seen >= cutoff:
                retained[key] = entry
            else:
                self.changed = True
        if not self.changed:
            return
        self.cache.update(
            {
                "provider": "openai-compatible",
                "engine": self.engine,
                "rule_version": RULE_VERSION,
                "updated_at": iso_now(),
                "entries": retained,
            }
        )
        write_json(self.cache_path, self.cache)
        self.changed = False
