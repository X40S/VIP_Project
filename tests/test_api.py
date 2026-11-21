import json
import time

import pytest
from fastapi.testclient import TestClient

from app import main, settings
from app.providers.base import ProviderError
from tests.helpers import import_file, import_result, make_analysis, viol


@pytest.fixture
def client():
    return TestClient(main.app)


def wait_job(client, job):
    for _ in range(300):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def test_pages_and_lists_work_without_key(client):
    assert client.get("/").status_code == 200
    items = client.get("/api/dialogs").json()["items"]
    assert len(items) == 40 and items[0]["analysis_status"] == "missing"
    d = client.get("/api/dialogs/D001").json()
    assert d["record"] is None and d["metrics"]["dialog"]["first_response_minutes"] == 95.0
    assert client.get("/api/dialogs/D999").status_code == 404


def test_analyze_without_key_is_clear_error_and_no_request(client):
    r = client.post("/api/analyze/D010")
    assert r.status_code == 400 and r.json()["detail"]["kind"] == "config"


def test_settings_endpoints_hide_keys_and_support_both_providers(client, fake):
    r = client.post("/api/settings", json={"provider": "gemini", "model": "gemini-3.8-flash", "api_key": "AIzaSECRET0000",
                                           "make_active": True}).json()
    assert r["providers"]["gemini"]["has_key"] and "SECRET" not in json.dumps(r)
    r = client.post("/api/settings", json={"provider": "anthropic", "api_key": "sk-ant-SECRET1111"}).json()
    assert r["provider"] == "gemini" and r["providers"]["anthropic"]["has_key"]      # активный не переключился
    fake()
    assert client.post("/api/settings/test", json={"provider": "anthropic", "model": "m-a"}).json()["ok"] is True
    models = client.get("/api/settings/models", params={"provider": "gemini"}).json()
    assert models["models"] == settings.PROVIDERS["gemini"]["models"] and len(models["models"]) == 5
    assert models["current_available"] is False and models["unconfirmed"] == models["models"]


def test_analyze_one_and_stale_when_model_changes(client, fake):
    fake(fn=lambda cfg, s, u: make_analysis("D010"))
    client.post("/api/settings", json={"provider": "gemini", "model": "m1", "api_key": "k", "make_active": True})
    assert client.post("/api/analyze/D010").status_code == 200
    item = next(i for i in client.get("/api/dialogs").json()["items"] if i["dialog_id"] == "D010")
    assert item["analysis_status"] == "fresh" and item["source"] == "gemini"
    client.post("/api/settings", json={"provider": "gemini", "model": "m2"})
    item = next(i for i in client.get("/api/dialogs").json()["items"] if i["dialog_id"] == "D010")
    assert item["analysis_status"] == "stale" and item["stale_reasons"][0]["label"] == "Модель изменена"


def test_estimate_endpoint_makes_no_calls(client):
    client.post("/api/settings", json={"provider": "anthropic", "model": "claude-opus-5-5", "api_key": "k", "make_active": True})
    est = client.post("/api/analyze-estimate", json={}).json()   # провайдеры заблокированы conftest
    assert est["count"] == 40 and est["cost_usd"] > 0 and est["model"] == "claude-opus-5-5"


def test_background_job_progress_new_vs_stale_and_no_auto_retry(client, fake):
    calls = {"n": 0}

    def fn(cfg, system, user):
        calls["n"] += 1
        did = user.split('"id": "')[1].split('"')[0]
        if did in ("D001", "D002"):
            raise ProviderError("unavailable", "high demand")
        return make_analysis(did)

    fake(fn=fn)
    client.post("/api/settings", json={"provider": "gemini", "model": "m1", "api_key": "k", "concurrency": 2, "make_active": True})
    job = wait_job(client, client.post("/api/analyze-all", json={}).json())
    assert job["status"] == "done" and job["total"] == 40 and job["done"] == 40
    assert len(job["errors"]) == 2 and calls["n"] == 40            # ошибки не повторялись автоматически
    again = wait_job(client, client.post("/api/analyze-all", json={}).json())
    assert again["total"] == 2                                      # только то, что не получилось


def test_job_aborts_on_fatal_error(client, fake):
    def fn(cfg, s, u):
        raise ProviderError("quota_daily", "Суточный лимит")
    p = fake(fn=fn)
    client.post("/api/settings", json={"provider": "gemini", "model": "m1", "api_key": "k", "concurrency": 1, "make_active": True})
    job = wait_job(client, client.post("/api/analyze-all", json={}).json())
    assert job["status"] == "aborted" and len(job["errors"]) == 1 and p.calls == 1


def test_rules_endpoints_and_versioning(client):
    st = client.get("/api/rules").json()
    assert st["version"] == "v1"
    secs = st["sections"]
    secs[2]["body"] += " Дополнение."
    st = client.put("/api/rules", json={"sections": secs, "note": "t"}).json()
    assert st["version"] == "v2"
    assert client.put("/api/rules", json={"sections": []}).status_code == 400
    assert client.post("/api/rules/restore", json={"version": 1}).json()["version"] == "v3"
    assert client.post("/api/rules/restore", json={"version": 42}).status_code == 400


def test_import_endpoints_and_summary(client):
    f = import_file([import_result("D001", violations=[viol("DRY_TONE", "low", ["D001#2"], category="communication")]),
                     import_result("D006", violations=[viol("NO_ESCALATION", "high", ["D006#3"])])])
    text = json.dumps(f)
    rep = client.post("/api/import/check", json={"text": text}).json()
    assert rep["ok"] and rep["valid"] == 2
    assert client.post("/api/import", json={"text": text}).json()["imported"] == 2
    s = client.get("/api/summary").json()
    assert s["analyzed"] == 2 and s["categories"]["operational"] == 1 and s["categories"]["communication"] == 1
    assert {m["manager"] for m in s["managers"]} >= {"Алина"}
    d = client.get("/api/dialogs/D006").json()
    assert d["record"]["meta"]["source"] == "claude_code" and d["freshness"]["status"] in ("fresh", "stale")
    assert client.post("/api/import/check", json={"text": "nope"}).json()["ok"] is False


def test_data_endpoints(client):
    assert client.get("/api/data").json()["dialogs"] == 40
    dialogs = json.dumps({"dialogs": [{"dialog_id": "Z", "player_id": "P1001", "manager": "Алина", "channel": "whatsapp",
                                       "messages": [{"from": "player", "sent_at": "2026-11-18T10:00:00", "text": "x"}]}]})
    rep = client.post("/api/data/check", json={"dialogs_text": dialogs}).json()
    assert rep["ok"] and rep["dialogs"]["count"] == 1
    assert client.post("/api/data/apply", json={"dialogs_text": dialogs}).json()["info"]["dialogs"] == 1
    assert client.post("/api/data/reset").status_code in (404, 405)   # возврата к файлам проекта нет


def test_app_without_uploaded_files_is_empty_and_safe(client, empty_data):
    d = client.get("/api/dialogs").json()
    assert d["items"] == [] and client.get("/api/data").json() == {"dialogs": 0, "players": 0, "as_of": None}
    assert client.get("/api/summary").json()["total"] == 0
    c = client.get("/api/cost").json()
    assert c["count"] == 0 and c["cached"] == 0
    job = client.post("/api/analyze-all", json={}).json()
    assert job["total"] == 0                                  # без файлов анализировать нечего, запросов к AI нет
    rep = client.post("/api/import/check", json={"text": json.dumps(import_file([]))}).json()
    assert not rep["ok"] and "загрузите" in rep["error"].lower()


def test_frontend_is_never_served_from_stale_browser_cache(client):
    for path in ("/", "/static/app.js", "/static/rules.js", "/static/style.css"):
        assert client.get(path).headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/api/rules").headers


def test_models_endpoint_adds_prices_and_static_anthropic_list_without_key(client):
    r = client.get("/api/settings/models", params={"provider": "anthropic"}).json()   # ключа нет, провайдер не вызывается
    assert r["static"] is True and r["models"] == ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    assert r["prices"]["claude-opus-5-5"] > r["prices"]["claude-sonnet-5-5"] > r["prices"]["claude-haiku-5-5"] > 0
    assert r["recommended"] == "claude-opus-5-5"


def test_models_endpoint_with_key_uses_api_list(client, fake):
    fake()
    client.post("/api/settings", json={"provider": "anthropic", "api_key": "sk-ant-KEY00000"})
    r = client.get("/api/settings/models", params={"provider": "anthropic"}).json()
    assert r["static"] is False and r["models"] == ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    assert r["unconfirmed"] == r["models"] and r["current_available"] is False


def test_cost_endpoint_depends_on_selected_model_and_makes_no_calls(client):
    def cost(model):
        client.post("/api/settings", json={"provider": "anthropic", "model": model, "api_key": "k", "make_active": True})
        return client.get("/api/cost").json()
    opus, haiku = cost("claude-opus-5-5"), cost("claude-haiku-5-5")
    assert opus["count"] == 40 and opus["cost_usd"] > haiku["cost_usd"] > 0
    client.post("/api/settings", json={"provider": "gemini", "api_key": "k", "make_active": True})
    g = client.get("/api/cost").json()
    assert g["cost_usd"] is None and "тариф" in g["note"]


def test_dialog_detail_has_cost_of_reanalysis(client):
    client.post("/api/settings", json={"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "k", "make_active": True})
    c = client.get("/api/dialogs/D001").json()["cost"]
    assert c["model"] == "claude-sonnet-5-5" and 0 < c["cost_usd"] < 0.5 and c["count"] == 1
