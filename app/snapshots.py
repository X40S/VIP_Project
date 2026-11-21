"""Автосохранение результатов в results/analysis_ГГГГ-ММ-ДД_ЧЧ-ММ-СС.json.

Файлы имеют формат импорта (их можно загрузить обратно через «Импорт анализа»), не перезаписываются
и не удаляются. Сохраняются результаты всех диалогов текущего набора данных на момент сохранения.
"""
import json
from datetime import datetime

from . import cache, rules
from .data import ROOT, load_all
from .schemas import SCHEMA_VERSION

RESULTS_DIR = ROOT / "results"


def save() -> str | None:
    """Записывает новый файл со всеми текущими результатами. Возвращает имя файла или None, если сохранять нечего."""
    db = load_all()
    records = cache.all_records()
    results = []
    for did in db["order"]:
        rec = records.get(did)
        if rec:
            d = db["dialogs"][did]
            results.append({"dialog_id": did, "player_id": d["player_id"], "manager_id": d["manager"],
                            "analysis": rec["analysis"], "meta": rec["meta"]})
    if not results:
        return None
    now = datetime.now()
    payload = {"schema_version": SCHEMA_VERSION, "rules_version": rules.current_version(),
               "source": {"type": "app"}, "generated_at": now.isoformat(timespec="seconds"), "results": results}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    for n in range(100):   # при совпадении секунды добавляем номер, существующие файлы не трогаем
        path = RESULTS_DIR / f"analysis_{stamp}{'' if n == 0 else f'_{n + 1}'}.json"
        try:
            with open(path, "x", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            return path.name
        except FileExistsError:
            continue
    return None
