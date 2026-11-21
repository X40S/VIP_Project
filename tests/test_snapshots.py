import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from app import cache, importer, main, settings, snapshots
from app.providers.base import ProviderError
from tests.helpers import import_file, import_result, make_analysis

NAME = re.compile(r"^analysis_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?\.json$")


@pytest.fixture
def client():
    return TestClient(main.app)


def files():
    return sorted(snapshots.RESULTS_DIR.glob("*.json")) if snapshots.RESULTS_DIR.exists() else []


def txt(*dialogs):
    return json.dumps(import_file([import_result(d) for d in dialogs]), ensure_ascii=False)


def test_nothing_is_saved_when_there_are_no_results():
    assert snapshots.save() is None and files() == []


def test_import_creates_dated_file_that_can_be_imported_back():
    rep = importer.apply(txt("D001", "D002"))
    assert rep["snapshot"] and NAME.match(rep["snapshot"])
    f = files()[0]
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert saved["source"]["type"] == "app" and len(saved["results"]) == 2 and saved["results"][0]["meta"]["source"] == "claude_code"
    assert importer.check(f.read_text(encoding="utf-8"))["valid"] == 2          # тот же файл открывается через «Импорт анализа»


def test_files_are_never_overwritten_or_deleted():
    importer.apply(txt("D001"))
    first = files()[0]
    before = first.read_text(encoding="utf-8")
    importer.apply(txt("D002"))
    importer.apply(txt("D003"))
    names = [f.name for f in files()]
    assert len(names) == 3 and len(set(names)) == 3          # даже в одну секунду имена разные
    assert first.read_text(encoding="utf-8") == before        # старый файл не изменился


def test_reimport_of_saved_file_keeps_provenance_and_freshness():
    cache.put("D001", {"dialog_id": "D001", "player_id": "P1001", "manager_id": "Алина", "analysis": make_analysis("D001"),
                       "meta": {"source": "gemini", "provider": "gemini", "model": "gemini-3.8-flash", "rules_version": "v1",
                                "engine": "s2p2", "dialog_hash": "abc", "analyzed_at": "2026-11-20T10:00:00"}})
    snapshots.save()
    saved = files()[0].read_text(encoding="utf-8")
    rep = importer.apply(saved, replace_existing=True)
    meta = cache.get("D001")["meta"]
    assert rep["imported"] == 1 and meta["source"] == "gemini" and meta["model"] == "gemini-3.8-flash"
    assert meta["dialog_hash"] == "abc" and meta["rules_version"] == "v1"


def test_single_analysis_and_job_save_files(client, fake):
    fake(fn=lambda cfg, s, u: make_analysis(json.loads(u)["dialog"]["id"]))
    client.post("/api/settings", json={"provider": "gemini", "model": "m1", "api_key": "k", "concurrency": 2, "make_active": True})
    r = client.post("/api/analyze/D002").json()
    assert NAME.match(r["snapshot"]) and len(files()) == 1
    time.sleep(1.1)
    job = client.post("/api/analyze-all", json={}).json()
    for _ in range(300):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.03)
    assert job["status"] == "done" and NAME.match(job["snapshot"]) and len(files()) == 2
    assert len(json.loads(files()[-1].read_text(encoding="utf-8"))["results"]) == 40


def test_failed_job_creates_no_file(client, fake):
    def fail(cfg, s, u):
        raise ProviderError("quota_daily", "лимит")
    fake(fn=fail)
    client.post("/api/settings", json={"provider": "gemini", "model": "m1", "api_key": "k", "concurrency": 1, "make_active": True})
    job = client.post("/api/analyze-all", json={}).json()
    for _ in range(100):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.03)
    assert job["status"] == "aborted" and job["snapshot"] is None and files() == []


def test_cost_endpoint_reports_cached_results_for_opening_without_requests(client):
    settings.save("gemini", "m1", "k", make_active=True)
    assert client.get("/api/cost").json()["cached"] == 0
    client.post("/api/import", json={"text": txt("D001", "D002")})
    c = client.get("/api/cost").json()                     # провайдеры в тестах заблокированы: запросов к AI нет
    assert c["cached"] == 2 and c["count"] == 38
