"""Импорт готового analysis_results.json (режим B). Запросов к AI API не делает."""
import json
from datetime import datetime, timezone

from pydantic import ValidationError

from . import analyzer, cache, rules, snapshots
from .data import load_all
from .schemas import SCHEMA_VERSION, ImportFile

# источники, результаты которых нельзя заменять без явного разрешения
API_SOURCES = ("gemini", "anthropic", "openai")
META_KEYS = ("source", "provider", "model", "rules_version", "engine", "dialog_hash", "analyzed_at", "duration_s")


def _short_error(e: ValidationError) -> str:
    err = e.errors()[0]
    loc = ".".join(str(x) for x in err["loc"])
    return f"{loc}: {err['msg']}"


def check(text: str) -> dict:
    """Проверка файла без сохранения. Возвращает отчёт."""
    report = _check(text)
    report.pop("_records", None)
    return report


def _check(text: str) -> dict:
    report = {"ok": False, "error": None, "total": 0, "valid": 0, "skipped": [], "rules_version": None,
              "rules_changed": False, "existing_api": 0, "model": None}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        report["error"] = f"Некорректный JSON ({e.msg}, строка {e.lineno})"
        return report
    try:
        imp = ImportFile.model_validate(raw)
    except ValidationError as e:
        report["error"] = f"Неверная структура: {_short_error(e)}"
        return report
    if imp.schema_version != SCHEMA_VERSION:
        report["error"] = f"Версия схемы {imp.schema_version}, ожидается {SCHEMA_VERSION}"
        return report

    db = load_all()
    if not db["dialogs"]:
        report["error"] = "Сначала загрузите dialogs.json и players.csv"
        return report
    records, seen = {}, set()
    report["total"] = len(imp.results)
    report["rules_version"] = imp.rules_version
    report["rules_changed"] = imp.rules_version != rules.current_version()
    report["model"] = imp.source.model
    for r in imp.results:
        did = r.dialog_id
        d = db["dialogs"].get(did)
        if d is None:
            report["skipped"].append({"dialog_id": did, "reason": "диалог не найден"})
        elif did in seen:
            report["skipped"].append({"dialog_id": did, "reason": "повторяющийся dialog_id"})
        elif d["player_id"] != r.player_id:
            report["skipped"].append({"dialog_id": did, "reason": f"игрок {r.player_id} не совпадает с {d['player_id']}"})
        elif d["manager"] != r.manager_id:
            report["skipped"].append({"dialog_id": did, "reason": f"менеджер {r.manager_id} не совпадает с {d['manager']}"})
        else:
            valid_ids = {m["id"] for m in d["messages"]}
            refs = [x for v in r.analysis.violations for x in v.message_refs]
            refs += [x for rec in r.analysis.recommendations for x in rec.message_refs] + r.analysis.rg.message_refs
            refs += [p.message_id for p in r.analysis.player_points] + [p.message_id for p in r.analysis.manager_points]
            bad = sorted({x for x in refs if x not in valid_ids})
            if bad:
                report["skipped"].append({"dialog_id": did, "reason": f"неверные ссылки на сообщения: {', '.join(bad[:3])}"})
            else:
                seen.add(did)
                records[did] = r
    report["valid"] = len(records)
    existing = cache.all_records()
    report["existing_api"] = sum(1 for did in records
                                 if existing.get(did, {}).get("meta", {}).get("source") in API_SOURCES)
    report["ok"] = bool(records)
    if not records and not report["error"]:
        report["error"] = "Нет ни одного корректного результата"
    report["_records"] = records
    return report


def apply(text: str, replace_existing: bool = False) -> dict:
    """Сохраняет корректные результаты. Результаты API не заменяются без replace_existing."""
    report = _check(text)
    records = report.pop("_records", {})
    if not report["ok"]:
        return report
    imp = json.loads(text)
    existing = cache.all_records()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    to_save, kept = {}, []
    for did, r in records.items():
        old = existing.get(did)
        if old and old["meta"].get("source") in API_SOURCES and not replace_existing:
            kept.append({"dialog_id": did, "reason": "есть результат API, не заменён"})
            continue
        meta = {"source": "claude_code", "provider": "claude_code", "model": imp["source"].get("model"),
                "rules_version": imp["rules_version"], "analyzed_at": imp.get("generated_at") or now}
        if r.meta:   # файл сохранён приложением: сохраняем исходного провайдера, модель, версию правил и хэш диалога
            meta.update({k: v for k, v in r.meta.items() if k in META_KEYS})
        to_save[did] = analyzer.build_record(did, r.analysis.model_dump(), {**meta, "imported_at": now})
    if to_save:
        cache.put_many(to_save)
    report["imported"] = len(to_save)
    report["kept"] = kept
    report["snapshot"] = snapshots.save() if to_save else None
    return report
