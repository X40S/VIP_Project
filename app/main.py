"""FastAPI: API и раздача интерфейса. Запуск: python -m uvicorn app.main:app"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analyzer, cache, data, estimate, importer, jobs, rules, settings, snapshots
from .data import ROOT, load_all
from .metrics import full_metrics
from .providers.base import ProviderError, get_provider
from .schemas import ImportFile

app = FastAPI(title="VIP Chat Analyzer")


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    """Браузер всегда проверяет актуальность страницы и скриптов, чтобы после обновления не работал старый интерфейс."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

PLAYER_FIELDS = [
    "player_id", "brand", "geo", "language", "status", "account_status", "vip_since",
    "deposits_30d_usd", "deposits_30d_count", "deposits_prev_30d_usd", "deposits_prev_30d_count",
    "last_deposit_at", "failed_deposits_7d", "withdrawals_30d_usd", "pending_withdrawal_usd",
    "bonuses_30d_usd", "bonuses_30d_count", "last_login_at", "favorite_game",
]
QUALITY_KEYS = ["tone", "empathy", "engagement", "personalization", "problem_understanding",
                "completeness", "follow_up", "next_steps_clarity"]


def _dialog_or_404(dialog_id: str):
    db = load_all()
    d = db["dialogs"].get(dialog_id)
    if not d:
        raise HTTPException(404, f"Диалог {dialog_id} не найден")
    return d, db["players"][d["player_id"]]


def _summary(dialog_id: str, cfg: dict) -> dict:
    db = load_all()
    d = db["dialogs"][dialog_id]
    metrics = full_metrics(d, db["players"][d["player_id"]])
    rec = cache.get(dialog_id)
    fresh = cache.freshness(rec, analyzer.current_signature(dialog_id, cfg))
    item = {
        "dialog_id": dialog_id, "player_id": d["player_id"], "manager": d["manager"],
        "channel": d["channel"], "messages": len(d["messages"]),
        "started_at": d["messages"][0]["sent_at"], "ended_at": d["messages"][-1]["sent_at"],
        "flags": metrics["player"]["flags"], "analysis_status": fresh["status"],
        "stale_reasons": fresh["reasons"], "priority": None, "overall": None,
        "violations": None, "source": None, "rg_signal": None,
    }
    if rec:
        a = rec["analysis"]
        item.update(priority=a["priority"]["level"], overall=a["quality"]["overall"],
                    violations=len(a["violations"]), source=rec["meta"].get("source"), rg_signal=a["rg"]["signal"])
    return item


@app.get("/api/dialogs")
def list_dialogs():
    cfg = settings.load()
    return {"as_of": load_all()["as_of"], "items": [_summary(i, cfg) for i in load_all()["order"]],
            "rules_version": rules.current_version(), "provider": cfg["provider"], "model": cfg["model"]}


def _cost_fields(est: dict) -> dict:
    return {k: est[k] for k in ("provider", "model", "count", "cost_usd", "note")}


@app.get("/api/cost")
def cost():
    """Ориентировочная стоимость анализа новых диалогов выбранной моделью. Запросов к AI не делает."""
    cfg = settings.load()
    out = _cost_fields(estimate.estimate(jobs.targets(cfg), cfg))
    out["has_key"] = bool(cfg["api_key"])
    out["cached"] = sum(1 for did in load_all()["order"] if cache.get(did))   # диалоги с готовым результатом на сервере
    return out


@app.get("/api/dialogs/{dialog_id}")
def get_dialog(dialog_id: str):
    d, p = _dialog_or_404(dialog_id)
    cfg = settings.load()
    metrics = full_metrics(d, p)
    rec = cache.get(dialog_id)
    fresh = cache.freshness(rec, analyzer.current_signature(dialog_id, cfg))
    resp = metrics["dialog"]["response_minutes"]
    msgs = [{**m, "response_minutes": resp.get(m["id"])} for m in d["messages"]]
    return {
        "dialog": {"dialog_id": d["dialog_id"], "player_id": d["player_id"], "manager": d["manager"],
                   "channel": d["channel"], "messages": msgs},
        "player": {k: p.get(k) for k in PLAYER_FIELDS if k in p},
        "metrics": metrics, "record": rec, "freshness": fresh,
        "cost": _cost_fields(estimate.estimate([dialog_id], cfg)),
    }


def _provider_http_error(e: ProviderError) -> HTTPException:
    status = {"auth": 401, "config": 400, "rate_limit": 429, "quota_daily": 429, "billing": 402,
              "unavailable": 503}.get(e.kind, 502)
    return HTTPException(status, {"kind": e.kind, "message": e.message})


@app.post("/api/analyze/{dialog_id}")
def analyze_one(dialog_id: str):
    _dialog_or_404(dialog_id)
    try:
        record = analyzer.analyze(dialog_id)
    except ProviderError as e:
        raise _provider_http_error(e)
    return {"record": record, "freshness": {"status": "fresh", "reasons": []}, "snapshot": snapshots.save()}


class AnalyzeAll(BaseModel):
    include_stale: bool = False
    force: bool = False


@app.post("/api/analyze-estimate")
def analyze_estimate(body: AnalyzeAll | None = None):
    """Оценка объёма и стоимости. Запросов к AI не делает."""
    body = body or AnalyzeAll()
    cfg = settings.load()
    ids = jobs.targets(cfg, body.include_stale, body.force)
    out = estimate.estimate(ids, cfg)
    out["has_key"] = bool(cfg["api_key"])
    new_count = len(jobs.targets(cfg, False, False))
    out["stale_count"] = len(jobs.targets(cfg, True, False)) - new_count
    out["analyzed_count"] = len(load_all()["order"]) - new_count   # диалоги с уже готовым результатом
    out["new_count"] = new_count
    return out


@app.post("/api/analyze-all")
def analyze_all(body: AnalyzeAll | None = None):
    body = body or AnalyzeAll()
    return jobs.start(include_stale=body.include_stale, force=body.force)


@app.get("/api/jobs/current")
def current_job():
    return jobs.current() or {"status": "none"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    return job


@app.get("/api/summary")
def summary():
    """Сводка для Team Lead и Head of VIP: приоритеты и сравнение менеджеров."""
    managers: dict[str, dict] = {}
    priorities = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    categories = {"rg_safety": 0, "operational": 0, "communication": 0}
    db = load_all()
    for did in db["order"]:
        d = db["dialogs"][did]
        m = managers.setdefault(d["manager"], {"manager": d["manager"], "dialogs": 0, "analyzed": 0,
                                               "_overall": [], "_q": {k: [] for k in QUALITY_KEYS},
                                               "violations": {"critical": 0, "high": 0, "medium": 0, "low": 0}})
        m["dialogs"] += 1
        rec = cache.get(did)
        if not rec:
            continue
        a = rec["analysis"]
        m["analyzed"] += 1
        m["_overall"].append(a["quality"]["overall"])
        for k in QUALITY_KEYS:
            if a["quality"].get(k) is not None:
                m["_q"][k].append(a["quality"][k])
        for v in a["violations"]:
            m["violations"][v["severity"]] += 1
            categories[v["category"]] += 1
        priorities[a["priority"]["level"]] += 1
    out = []
    for m in managers.values():
        avg = lambda xs: round(sum(xs) / len(xs)) if xs else None
        out.append({"manager": m["manager"], "dialogs": m["dialogs"], "analyzed": m["analyzed"],
                    "overall": avg(m["_overall"]), "quality": {k: avg(v) for k, v in m["_q"].items()},
                    "violations": m["violations"]})
    out.sort(key=lambda x: (x["overall"] is None, x["overall"] or 0))
    return {"managers": out, "priorities": priorities, "categories": categories,
            "analyzed": sum(m["analyzed"] for m in out), "total": len(db["order"])}


# ---------- данные ----------

class DataBody(BaseModel):
    dialogs_text: str | None = None
    players_text: str | None = None


@app.get("/api/data")
def data_info():
    return data.info()


@app.post("/api/data/check")
def data_check(body: DataBody):
    return data.check(body.dialogs_text, body.players_text)


@app.post("/api/data/apply")
def data_apply(body: DataBody):
    report = data.apply(body.dialogs_text, body.players_text)
    if report["ok"]:
        report["info"] = data.info()
    return report


# ---------- импорт готового анализа ----------

class ImportBody(BaseModel):
    text: str
    replace_existing: bool = False


@app.post("/api/import/check")
def import_check(body: ImportBody):
    return importer.check(body.text)


@app.post("/api/import")
def import_apply(body: ImportBody):
    return importer.apply(body.text, body.replace_existing)


@app.get("/api/schema")
def results_schema():
    return ImportFile.model_json_schema()


# ---------- правила анализа ----------

class RulesBody(BaseModel):
    sections: list[dict]
    note: str | None = None


class RestoreBody(BaseModel):
    version: int


@app.get("/api/rules")
def get_rules():
    return rules.state()


@app.put("/api/rules")
def put_rules(body: RulesBody):
    try:
        return rules.save(body.sections, body.note)
    except rules.RulesError as e:
        raise HTTPException(400, str(e))


@app.post("/api/rules/restore")
def restore_rules(body: RestoreBody):
    try:
        return rules.restore(body.version)
    except rules.RulesError as e:
        raise HTTPException(400, str(e))


# ---------- настройки AI ----------

class SettingsBody(BaseModel):
    provider: str
    model: str | None = None
    api_key: str | None = None
    concurrency: int | None = None
    make_active: bool = False


@app.get("/api/settings")
def get_settings():
    return settings.public()


@app.post("/api/settings")
def save_settings(body: SettingsBody):
    try:
        settings.save(body.provider, body.model, body.api_key, body.concurrency, body.make_active)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return settings.public()


@app.post("/api/settings/test")
def test_settings(body: SettingsBody):
    """Проверка подключения без генерации. Поля тела временно переопределяют сохранённые."""
    if body.provider not in settings.PROVIDERS:
        raise HTTPException(400, "Неизвестный провайдер")
    cfg = settings.load(body.provider)
    if body.model and body.model.strip():
        cfg["model"] = body.model.strip()
    if body.api_key and body.api_key.strip() and "•" not in body.api_key:
        cfg["api_key"] = body.api_key.strip()
    try:
        model = get_provider(cfg["provider"]).test_connection(cfg)
    except ProviderError as e:
        return {"ok": False, "kind": e.kind, "message": e.message}
    return {"ok": True, "message": f"Подключено: {model}"}


@app.get("/api/settings/models")
def settings_models(provider: str):
    if provider not in settings.PROVIDERS:
        raise HTTPException(400, "Неизвестный провайдер")
    cfg = settings.load(provider)
    curated = settings.PROVIDERS[provider]["models"]
    static = not cfg["api_key"]
    confirmed: list[str] = []
    if static:   # без ключа список взять негде: показываем заданный список моделей
        models = list(curated)
    else:
        try:
            confirmed = get_provider(provider).list_models(cfg)
        except ProviderError as e:
            raise _provider_http_error(e)
        # у OpenAI показываем новейшие модели с API, у остальных заданный список с пометкой неподтверждённых ключом
        models = confirmed if provider == "openai" else list(curated)
    rec = settings.PROVIDERS[provider]["recommended"]
    return {
        "models": models, "prices": estimate.model_prices(provider, models), "static": static,
        "unconfirmed": [m for m in models if m not in confirmed] if not static and provider != "openai" else [],
        "current": cfg["model"], "current_available": static or cfg["model"] in (confirmed or models),
        "recommended": rec if rec in models else None,
    }


STATIC = ROOT / "static"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
