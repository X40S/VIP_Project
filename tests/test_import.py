import json

from app import cache, importer, jobs, rules
from app.schemas import ImportFile
from scripts import export_schema
from tests.helpers import import_file, import_result, make_analysis, viol


def txt(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_schema_file_is_in_sync_and_describes_required_fields():
    assert export_schema.OUT.read_text(encoding="utf-8") == export_schema.build()
    schema = ImportFile.model_json_schema()
    for key in ("schema_version", "rules_version", "source", "results"):
        assert key in schema["properties"]
    item = schema["$defs"]["ImportResult"]["properties"]
    assert {"dialog_id", "player_id", "manager_id", "analysis"} <= set(item)


def test_valid_import_saves_and_does_not_call_any_provider():
    # провайдеры в тестах заблокированы (conftest): любое обращение к ним уронило бы тест
    f = import_file([import_result("D001"), import_result("D002", violations=[viol("UNRESOLVED_REQUEST", "medium", ["D002#1"])])])
    rep = importer.apply(txt(f))
    assert rep["ok"] and rep["imported"] == 2 and rep["skipped"] == []
    rec = cache.get("D002")
    assert rec["meta"]["source"] == "claude_code" and rec["meta"]["model"] == "claude-code-test"
    assert rec["analysis"]["priority"]["level"] == "medium"        # единая обработка с AI-режимом
    assert rec["analysis"]["quality"]["overall"] == 70               # оценки не изменены


def test_invalid_json_and_wrong_structure_give_short_errors():
    assert "Некорректный JSON" in importer.check("{not json")["error"]
    assert "Неверная структура" in importer.check(txt({"results": []}))["error"]
    bad = import_file([import_result("D001")])
    bad["results"][0]["analysis"]["quality"]["overall"] = 150
    assert "Неверная структура" in importer.check(txt(bad))["error"]
    assert "Версия схемы" in importer.check(txt(import_file([], schema_version=1)))["error"]


def test_ids_and_message_refs_are_validated_per_result():
    wrong_player = import_result("D001")
    wrong_player["player_id"] = "P9999"
    wrong_mgr = import_result("D002")
    wrong_mgr["manager_id"] = "Nobody"
    bad_ref = import_result("D003", violations=[viol("FALSE_PROMISE", "high", ["D003#99"])])
    unknown = import_result("D004")
    unknown["dialog_id"] = "D777"
    dup = import_result("D005")
    f = import_file([wrong_player, wrong_mgr, bad_ref, unknown, dup, import_result("D005")])
    rep = importer.check(txt(f))
    reasons = {s["dialog_id"]: s["reason"] for s in rep["skipped"]}
    assert rep["valid"] == 1 and rep["ok"]
    assert "не совпадает" in reasons["D001"] and "менеджер" in reasons["D002"]
    assert "ссылки" in reasons["D003"] and "не найден" in reasons["D777"] and "повторяющийся" in reasons["D005"]


def test_import_does_not_silently_overwrite_api_results():
    api = {"dialog_id": "D001", "analysis": make_analysis("D001"),
           "meta": {"source": "gemini", "provider": "gemini", "model": "m", "rules_version": "v1",
                    "engine": "x", "dialog_hash": "h"}}
    cache.put("D001", api)
    f = txt(import_file([import_result("D001"), import_result("D002")]))
    assert importer.check(f)["existing_api"] == 1
    rep = importer.apply(f)
    assert rep["imported"] == 1 and rep["kept"][0]["dialog_id"] == "D001"
    assert cache.get("D001")["meta"]["source"] == "gemini"
    rep = importer.apply(f, replace_existing=True)             # явное разрешение
    assert rep["imported"] == 2 and cache.get("D001")["meta"]["source"] == "claude_code"
    hist = json.loads(cache.CACHE_PATH.read_text(encoding="utf-8"))["history"]["D001"]
    assert hist[0]["meta"]["source"] == "gemini"                # прежний результат сохранён в истории


def test_rules_version_mismatch_is_flagged_but_import_still_works():
    f = txt(import_file([import_result("D001")], rules_version="v0"))
    rep = importer.check(f)
    assert rep["ok"] and rep["rules_changed"] is True
    importer.apply(f)
    fresh = cache.freshness(cache.get("D001"), {"provider": "gemini", "model": "m", "rules_version": rules.current_version(),
                                                "engine": cache.get("D001")["meta"]["engine"],
                                                "dialog_hash": cache.get("D001")["meta"]["dialog_hash"]})
    assert fresh["reasons"][0]["label"] == "Правила изменены"
    assert cache.get("D001")["analysis"]["quality"]["overall"] == 70   # оценки не пересчитаны
    assert jobs.targets({"provider": "gemini", "model": "m", "api_key": "", "concurrency": 1}) != []


def test_import_format_detected_by_content_not_filename():
    assert importer.check(txt(import_file([import_result("D001")])))["ok"]
    assert not importer.check(txt([{"dialog_id": "D001"}]))["ok"]


def test_documented_example_file_imports_without_changes():
    """Пример из docs должен импортироваться без правок: так Claude Code получает рабочий образец."""
    from pathlib import Path
    ex = (Path(__file__).resolve().parent.parent / "docs" / "analysis_results.example.json").read_text(encoding="utf-8")
    rep = importer.check(ex)
    assert rep["ok"] and rep["valid"] == 1 and rep["skipped"] == []
