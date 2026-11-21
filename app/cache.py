"""Хранилище результатов анализа (v2) с историей и проверкой актуальности.

Файл cache/analysis.json: {"version": 2, "records": {...}, "history": {...}, "legacy_v1": {...}}.
Результаты старого формата (v1) сохраняются в legacy_v1 и не удаляются.
Замена существующего результата переносит прежний в history.
"""
import json
import os
import shutil
import threading
from pathlib import Path

from .data import ROOT

CACHE_PATH = ROOT / "cache" / "analysis.json"
STORE_VERSION = 2
HISTORY_LIMIT = 5
_lock = threading.RLock()


def _read() -> dict:
    path = Path(CACHE_PATH)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": STORE_VERSION, "records": {}, "history": {}, "legacy_v1": {}}
    if raw.get("version") != STORE_VERSION:   # формат v1: переносим целиком в legacy_v1
        backup = path.with_name("analysis.v1.backup.json")
        if not backup.exists():
            shutil.copyfile(path, backup)
        return {"version": STORE_VERSION, "records": {}, "history": {},
                "legacy_v1": {**raw.get("legacy_v1", {}), **raw.get("records", {})}}
    raw.setdefault("history", {})
    raw.setdefault("legacy_v1", {})
    return raw


def _write(store: dict) -> None:
    path = Path(CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def get(dialog_id: str) -> dict | None:
    with _lock:
        return _read()["records"].get(dialog_id)


def all_records() -> dict:
    with _lock:
        return _read()["records"]


def put(dialog_id: str, record: dict) -> None:
    """Сохраняет результат. Прежний результат диалога (если был) уходит в историю."""
    with _lock:
        store = _read()
        old = store["records"].get(dialog_id)
        if old:
            hist = store["history"].setdefault(dialog_id, [])
            hist.insert(0, old)
            del hist[HISTORY_LIMIT:]
        store["records"][dialog_id] = record
        _write(store)


def put_many(records: dict) -> None:
    with _lock:
        store = _read()
        for did, rec in records.items():
            old = store["records"].get(did)
            if old:
                hist = store["history"].setdefault(did, [])
                hist.insert(0, old)
                del hist[HISTORY_LIMIT:]
            store["records"][did] = rec
        _write(store)



REASON_LABELS = {
    "model": "Модель изменена",
    "rules": "Правила изменены",
    "engine": "Схема анализа изменена",
    "dialog": "Диалог изменён",
}


def freshness(record: dict | None, current: dict) -> dict:
    """current: {provider, model, rules_version, engine, dialog_hash}.

    Для результатов из API учитывается и модель. Для импортированных (claude_code)
    модель не сравнивается, так как анализ делался вне приложения.
    """
    if record is None:
        return {"status": "missing", "reasons": []}
    meta = record["meta"]
    reasons = []
    if meta.get("source") != "claude_code" and (
        meta.get("provider") != current["provider"] or meta.get("model") != current["model"]
    ):
        reasons.append("model")
    if meta.get("rules_version") != current["rules_version"]:
        reasons.append("rules")
    if meta.get("engine") != current["engine"]:
        reasons.append("engine")
    if meta.get("dialog_hash") != current["dialog_hash"]:
        reasons.append("dialog")
    return {"status": "stale" if reasons else "fresh",
            "reasons": [{"code": r, "label": REASON_LABELS[r]} for r in reasons]}
