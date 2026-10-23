from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PAPER_TRACKER_DATA_DIR", "/data")).expanduser().resolve()
DAYS_DIR = DATA_DIR / "days"
BEIJING = ZoneInfo("Asia/Shanghai")
# Keep the default namespace stable for DOI-less article fallback IDs created by
# earlier versions; translation caches pass their engine explicitly.
TITLE_KEY_NAMESPACE = "facebook/nllb-200-distilled-600M"
TITLE_FILTER_RULE_VERSION = "2026-09-19.1"
RETENTION_FLOOR = date(2026, 10, 1)


def update_collection_detail(**detail: Any) -> None:
    filename = os.environ.get("PAPER_TRACKER_COLLECTION_PROGRESS")
    token = os.environ.get("PAPER_TRACKER_COLLECTION_TOKEN")
    if not filename or not token:
        return
    path = Path(filename)
    progress = read_json(path, {})
    if progress.get("token") != token:
        return
    progress["detail"] = detail
    write_json(path, progress)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        # Public JSON is served by the local web process; keep it host-readable.
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def now_beijing() -> datetime:
    return datetime.now(timezone.utc).astimezone(BEIJING)


def ensure_publication_date(article: dict[str, Any], reference: date) -> dict[str, Any]:
    """Persist a month-start fallback once; keep the source metadata intact."""
    if not article.get("published_date"):
        article["published_date"] = reference.replace(day=1).isoformat()
        article["date_inferred"] = True
        article["date_inference"] = "current-month-start"
    article.pop("supplement_type", None)
    article.pop("archived_date", None)
    return article


def iso_now() -> str:
    return now_beijing().isoformat(timespec="seconds")


def subtract_months(value: date, count: int) -> date:
    absolute = value.year * 12 + value.month - 1 - count
    year, month_zero = divmod(absolute, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def retention_start(reference: date) -> date:
    calculated = subtract_months(reference, 3)
    return max(calculated, RETENTION_FLOOR) if reference >= RETENTION_FLOOR else calculated


def normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value).strip().lower()
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def article_key(article: dict[str, Any]) -> str:
    doi = normalize_doi(article.get("doi"))
    if doi:
        return f"doi:{doi}"
    seed = "\n".join(
        [article.get("journal_id", ""), article.get("title_en", ""), article.get("url", "")]
    )
    return "fallback:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def translation_engine(model: str | None = None) -> str:
    model = (model if model is not None else os.environ.get("LLM_MODEL", "")).strip()
    model = model or "unconfigured"
    return f"openai-compatible:{model}"


def title_cache_key(title: str, model: str = TITLE_KEY_NAMESPACE) -> str:
    normalized = re.sub(r"\s+", " ", title).strip()
    return hashlib.sha256(f"{model}\n{normalized}".encode("utf-8")).hexdigest()


def iter_day_files() -> Iterable[Path]:
    return sorted(DAYS_DIR.glob("????-??-??.json"))


def parse_date_parts(value: Any) -> tuple[str | None, str]:
    date_time = value.get("date-time") if isinstance(value, dict) else None
    if date_time:
        try:
            normalized = str(date_time).replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(BEIJING)
                return parsed.date().isoformat(), "datetime"
        except (TypeError, ValueError):
            pass
    try:
        parts = value["date-parts"][0]
    except (KeyError, IndexError, TypeError):
        return None, "missing"
    if len(parts) < 3:
        return None, "month" if len(parts) == 2 else "year"
    try:
        parsed = date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (TypeError, ValueError):
        return None, "invalid"
    return parsed.isoformat(), "date"


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
