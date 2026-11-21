import json

from app import cache
from tests.helpers import make_analysis


def _rec(source="gemini", model="m", rules="v1", engine="s2p2", h="h"):
    return {"dialog_id": "D001", "analysis": make_analysis("D001"),
            "meta": {"source": source, "provider": source, "model": model, "rules_version": rules,
                     "engine": engine, "dialog_hash": h}}


CUR = {"provider": "gemini", "model": "m", "rules_version": "v1", "engine": "s2p2", "dialog_hash": "h"}


def test_freshness_reasons():
    assert cache.freshness(None, CUR)["status"] == "missing"
    assert cache.freshness(_rec(), CUR)["status"] == "fresh"
    assert cache.freshness(_rec(model="x"), CUR)["reasons"][0]["code"] == "model"
    assert cache.freshness(_rec(rules="v0"), CUR)["reasons"][0]["label"] == "Правила изменены"
    assert cache.freshness(_rec(h="z"), CUR)["reasons"][0]["code"] == "dialog"
    assert cache.freshness(_rec(engine="s1"), CUR)["reasons"][0]["code"] == "engine"


def test_imported_results_ignore_model_change():
    r = _rec(source="claude_code", model="whatever")
    r["meta"]["provider"] = "claude_code"
    assert cache.freshness(r, CUR)["status"] == "fresh"


def test_replacing_result_keeps_history():
    cache.put("D001", _rec(model="a"))
    cache.put("D001", _rec(model="b"))
    store = json.loads(cache.CACHE_PATH.read_text(encoding="utf-8"))
    assert store["records"]["D001"]["meta"]["model"] == "b"
    assert store["history"]["D001"][0]["meta"]["model"] == "a"


def test_old_v1_results_are_preserved_not_deleted():
    cache.CACHE_PATH.parent.mkdir(parents=True)
    cache.CACHE_PATH.write_text(json.dumps({"records": {"D001": {"dialog_id": "D001", "analysis": {"old": True}}}}),
                                encoding="utf-8")
    assert cache.get("D001") is None                                     # не отображается в новом формате
    cache.put("D002", _rec())
    store = json.loads(cache.CACHE_PATH.read_text(encoding="utf-8"))
    assert store["legacy_v1"]["D001"]["analysis"] == {"old": True}       # но сохранён
    assert (cache.CACHE_PATH.parent / "analysis.v1.backup.json").exists()
