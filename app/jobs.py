"""Фоновый массовый анализ с прогрессом. Запускается только по явной команде пользователя."""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import analyzer, cache, settings, snapshots
from .data import load_all
from .providers.base import FATAL_KINDS, ProviderError

_jobs: dict[str, dict] = {}
_guard = threading.Lock()
_current: str | None = None


def targets(cfg: dict, include_stale: bool = False, force: bool = False) -> list[str]:
    """Диалоги к анализу: новые, при include_stale ещё и устаревшие, при force все."""
    out = []
    for did in load_all()["order"]:
        if force:
            out.append(did)
            continue
        status = cache.freshness(cache.get(did), analyzer.current_signature(did, cfg))["status"]
        if status == "missing" or (include_stale and status == "stale"):
            out.append(did)
    return out


def start(include_stale: bool = False, force: bool = False) -> dict:
    """Запускает фоновую задачу. Если задача уже идёт, возвращает её."""
    global _current
    with _guard:
        if _current and _jobs[_current]["status"] == "running":
            return _view(_jobs[_current])
        cfg = settings.load()
        ids = targets(cfg, include_stale, force)
        job = {
            "id": uuid.uuid4().hex[:8], "status": "running" if ids else "done",
            "total": len(ids), "done": 0, "errors": [], "active": [],
            "started_at": time.time(), "finished_at": None if ids else time.time(),
            "aborted": False, "provider": cfg["provider"], "model": cfg["model"], "snapshot": None,
        }
        _jobs[job["id"]] = job
        _current = job["id"]
        if ids:
            threading.Thread(target=_run, args=(job, ids, cfg), daemon=True).start()
        return _view(job)


def _run(job: dict, ids: list[str], cfg: dict) -> None:
    def work(did: str):
        if job["aborted"]:
            return
        job["active"].append(did)
        try:
            analyzer.analyze(did, cfg)
        except ProviderError as e:
            job["errors"].append({"dialog_id": did, "kind": e.kind, "message": e.message})
            if e.kind in FATAL_KINDS:   # дальше будет то же самое, не тратим время и деньги
                job["aborted"] = True
        except Exception as e:
            job["errors"].append({"dialog_id": did, "kind": "other", "message": str(e)[:300]})
        finally:
            job["active"].remove(did)
            job["done"] += 1

    # Ошибки не повторяются автоматически: повтор запускает пользователь.
    with ThreadPoolExecutor(max_workers=int(cfg.get("concurrency", 2))) as pool:
        list(pool.map(work, ids))
    job["snapshot"] = snapshots.save() if job["done"] > len(job["errors"]) else None
    job["status"] = "aborted" if job["aborted"] else "done"
    job["finished_at"] = time.time()


def _view(job: dict) -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in job.items()}


def get(job_id: str) -> dict | None:
    job = _jobs.get(job_id)
    return _view(job) if job else None


def current() -> dict | None:
    return get(_current) if _current else None
