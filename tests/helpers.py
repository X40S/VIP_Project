"""Вспомогательные функции тестов: валидные ответы модели (моки)."""
import copy

from app.data import load_all

QUALITY_KEYS = ["tone", "empathy", "engagement", "personalization", "problem_understanding",
                "completeness", "follow_up", "next_steps_clarity"]


def make_analysis(dialog_id: str, **over) -> dict:
    """Минимальный валидный Analysis для диалога. over переопределяет верхнеуровневые поля."""
    d = load_all()["dialogs"][dialog_id]
    a = {
        "profile": {"summary": "Регулярный VIP-игрок."},
        "quality": {**{k: 70 for k in QUALITY_KEYS}, "overall": 70, "summary": "Работа в целом корректная."},
        "player_points": [{"message_id": m["id"], "dissatisfaction": 30, "state": "neutral"}
                          for m in d["messages"] if m["from"] == "player"],
        "manager_points": [{"message_id": m["id"], "engagement": 60}
                           for m in d["messages"] if m["from"] == "manager"],
        "rg": {"signal": "none", "handled": "not_applicable", "message_refs": []},
        "risk_flags": [], "violations": [], "recommendations": [],
    }
    a.update(copy.deepcopy(over))
    return a


def viol(code, severity, refs, title=None, category="operational", explanation="Пояснение."):
    return {"title": title or code, "category": category, "code": code, "severity": severity,
            "explanation": explanation, "message_refs": refs}


def rec(text, kind="service", refs=None):
    return {"text": text, "kind": kind, "message_refs": refs or []}


def import_file(results, rules_version="v1", model="claude-code-test", schema_version=2):
    return {"schema_version": schema_version, "rules_version": rules_version,
            "source": {"type": "claude_code", "model": model}, "generated_at": "2026-11-20T10:00:00",
            "results": results}


def import_result(dialog_id, **over):
    db = load_all()
    d = db["dialogs"][dialog_id]
    return {"dialog_id": dialog_id, "player_id": d["player_id"], "manager_id": d["manager"],
            "analysis": make_analysis(dialog_id, **over)}
