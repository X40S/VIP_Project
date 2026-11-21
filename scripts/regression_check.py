"""Живая проверка правил на диалогах D003, D006, D008, D010, D011.

ВНИМАНИЕ: делает реальные платные запросы к выбранному в настройках провайдеру (5 запросов).
Запуск только по явной команде: python -m scripts.regression_check --confirm
Результаты сохраняются в кэш как обычные результаты анализа.
"""
import sys

from app import analyzer, settings
from app.providers.base import ProviderError

CHECKS = {
    "D003": lambda a: [
        ("RG-сигнал отсутствует (проигрыш не доказывает зависимость)", a["rg"]["signal"] == "none"),
        ("FALSE_PROMISE выявлено", any(v["code"] == "FALSE_PROMISE" for v in a["violations"])),
        ("нет автоматической RG-интервенции", not any(r["kind"] == "protective" for r in a["recommendations"])),
        ("пик недовольства 80-100", 80 <= max(p["dissatisfaction"] for p in a["player_points"]) <= 100),
        ("последнее состояние игрока не resolved", a["player_points"][-1]["state"] != "resolved"),
    ],
    "D006": lambda a: [
        ("chargeback_threat отмечен", "chargeback_threat" in a["risk_flags"]),
        ("NO_ESCALATION выявлено", any(v["code"] == "NO_ESCALATION" for v in a["violations"])),
        ("приоритет не ниже high", a["priority"]["level"] in ("high", "critical")),
    ],
    "D008": lambda a: [
        ("RG-сигнал отсутствует", a["rg"]["signal"] == "none"),
        ("пик недовольства не выше 60", max(p["dissatisfaction"] for p in a["player_points"]) <= 60),
        ("вопрос без ответа отмечен", any(v["code"] in ("NO_FOLLOW_UP", "UNRESOLVED_REQUEST") for v in a["violations"])),
        ("нет критических нарушений", not any(v["severity"] == "critical" for v in a["violations"])),
    ],
    "D010": lambda a: [
        ("запрос на самоисключение распознан", a["rg"]["signal"] == "self_exclusion_request"),
        ("нарушение категории rg_safety", any(v["category"] == "rg_safety" for v in a["violations"])),
        ("приоритет critical", a["priority"]["level"] == "critical"),
    ],
    "D011": lambda a: [
        ("запрос на самоисключение распознан", a["rg"]["signal"] == "self_exclusion_request"),
        ("обработан корректно (handled=yes)", a["rg"]["handled"] == "yes"),
        ("нарушений нет", not a["violations"]),
    ],
}


def main() -> int:
    if "--confirm" not in sys.argv:
        print(__doc__)
        return 1
    cfg = settings.load()
    print(f"Провайдер: {cfg['provider']}, модель: {cfg['model']}")
    failed = 0
    for did, check in CHECKS.items():
        try:
            a = analyzer.analyze(did, cfg)["analysis"]
        except ProviderError as e:
            print(f"{did}: ошибка {e.kind}: {e.message}")
            return 2
        print(f"\n{did}: приоритет {a['priority']['level']}, оценка {a['quality']['overall']}")
        for text, ok in check(a):
            failed += not ok
            print(f"  [{'OK' if ok else 'FAIL'}] {text}")
    print(f"\nНе пройдено проверок: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
