"""Регрессионные сценарии ручного аудита (D003, D006, D008, D010, D011).

Ответы модели здесь моки: тесты проверяют, что правила в промпте универсальны, а код (схема, guardrails,
приоритет) корректно обрабатывает ожидаемые по этим правилам ответы. Оценки в коде не подставляются.
Фактическое поведение живой модели проверяет scripts/regression_check.py (платные запросы, только по явной команде).
"""
import pytest

from app import analyzer, cache, rules, settings
from tests.helpers import make_analysis, rec, viol


@pytest.fixture(autouse=True)
def key():
    settings.save("gemini", "m1", "k", make_active=True)


def run(fake, did, analysis):
    fake(fn=lambda cfg, s, u: analysis)
    return analyzer.analyze(did)["analysis"]


def points(did, player, manager, states=None):
    from app.data import load_all
    msgs = load_all()["dialogs"][did]["messages"]
    pl = [m["id"] for m in msgs if m["from"] == "player"]
    mg = [m["id"] for m in msgs if m["from"] == "manager"]
    return {
        "player_points": [{"message_id": i, "dissatisfaction": v, "state": (states or ["dissatisfaction"] * 9)[n]}
                          for n, (i, v) in enumerate(zip(pl, player))],
        "manager_points": [{"message_id": i, "engagement": v} for i, v in zip(mg, manager)],
    }


def test_rules_text_contains_universal_clauses():
    t = rules.prompt_text()
    for clause in ("formal_agreement", "Получение бонуса не восстанавливает доверие", "чарджбэк", "FALSE_PROMISE",
                   "CHASING_INCENTIVE", "НЕ является RG-сигналом", "«исключение»", "null", "ambiguous_distress",
                   "self_harm_risk", "Техническая блокировка"):
        assert clause in t, clause
    assert "D003" not in t and "D006" not in t and "P1003" not in t           # правила не подогнаны под диалоги
    assert "—" not in t


def test_d003_rules_split_false_promise_from_rg_and_no_rg_intervention(fake):
    a = make_analysis("D003", **points("D003", [70, 90, 55], [45, 30], ["irritation", "irritation", "detachment"]),
                      violations=[viol("FALSE_PROMISE", "high", ["D003#2"], title="Обещание гарантированного отыгрыша"),
                                  viol("CHASING_INCENTIVE", "medium", ["D003#2", "D003#4"], title="Стимул отыграться"),
                                  viol("LOW_EMPATHY", "medium", ["D003#2"], category="communication", title="Мало эмпатии")],
                      recommendations=[rec("Исправить обещание гарантированного отыгрыша, извиниться", "service", ["D003#2"]),
                                       rec("Поставить игроку лимит и паузу", "protective"),
                                       rec("Разобрать с менеджером формулировки бонусных предложений", "coaching")])
    out = run(fake, "D003", a)
    assert out["rg"]["signal"] == "none"                                       # проигрыш не RG-сигнал
    assert {v["code"] for v in out["violations"]} == {"FALSE_PROMISE", "CHASING_INCENTIVE", "LOW_EMPATHY"}
    assert not any(r["kind"] == "protective" for r in out["recommendations"])  # RG-интервенция не навязана
    assert out["priority"]["level"] == "high" and out["priority"]["reason"] == "Нарушение менеджера"
    assert out["player_points"][-1]["state"] == "detachment"                   # отстранённость, а не resolved


def test_d006_chargeback_mention_escalates_without_claiming_a_chargeback(fake):
    a = make_analysis("D006", **points("D006", [60, 80, 90], [30, 20]), risk_flags=["chargeback_threat", "churn_threat"],
                      violations=[viol("NO_ESCALATION", "high", ["D006#4"], title="Игнор упоминания чарджбэка"),
                                  viol("LOW_EMPATHY", "medium", ["D006#2", "D006#4"], category="communication"),
                                  viol("SLOW_RESPONSE", "medium", ["D006#2"], category="communication"),
                                  viol("NO_FOLLOW_UP", "medium", ["D006#4"], category="communication", title="Нет статуса")],
                      recommendations=[rec("Эскалировать вывод в платежи и сообщить игроку статус и срок", "service")])
    out = run(fake, "D006", a)
    assert out["priority"]["level"] == "high"
    assert {v["category"] for v in out["violations"]} == {"operational", "communication"}
    assert out["rg"]["signal"] == "none" and len(out["recommendations"]) <= 3
    assert not any("чарджбэк оформлен" in str(v).lower() for v in out["violations"])


def test_d008_positive_personalization_and_missing_followup_without_rg(fake):
    a = make_analysis("D008", **points("D008", [30, 50, 15, 20], [55, 70], ["dissatisfaction", "dissatisfaction", "positive", "neutral"]),
                      violations=[viol("NO_FOLLOW_UP", "medium", ["D008#6"], category="communication", title="Вопрос без ответа")],
                      recommendations=[rec("Ответить на вопрос о бонусах на завтра", "service", ["D008#6"])])
    out = run(fake, "D008", a)
    assert max(p["dissatisfaction"] for p in out["player_points"]) <= 60
    assert out["rg"]["signal"] == "none" and out["priority"]["level"] == "medium"
    assert [v["code"] for v in out["violations"]] == ["NO_FOLLOW_UP"]          # слово «исключение» не нарушение
    assert out["recommendations"][0]["blocked"] is False


def test_d010_incorrect_vs_d011_correct_self_exclusion_handling(fake):
    bad = make_analysis("D010", rg={"signal": "self_exclusion_request", "handled": "no", "message_refs": ["D010#1"]},
                        violations=[viol("INCENTIVE_AFTER_STOP_REQUEST", "critical", ["D010#2", "D010#4"], category="rg_safety",
                                         title="Кешбэк в ответ на самоисключение"),
                                    viol("IGNORED_SELF_EXCLUSION", "critical", ["D010#2"], category="rg_safety")],
                        recommendations=[rec("Предложить кешбэк чтобы удержать", "commercial")])
    out = run(fake, "D010", bad)
    assert out["priority"]["level"] == "critical"
    assert out["recommendations"][0]["kind"] == "protective" and out["recommendations"][0].get("source") == "guardrail"
    assert any(r.get("blocked") for r in out["recommendations"]) or len(out["recommendations"]) == 1

    good = make_analysis("D011", rg={"signal": "self_exclusion_request", "handled": "yes", "message_refs": ["D011#1"]},
                         quality={**make_analysis("D011")["quality"], "overall": 98})
    out = run(fake, "D011", good)
    assert out["violations"] == [] and out["quality"]["overall"] == 98         # корректная обработка не снижает оценку
    assert out["priority"]["level"] == "medium"
    assert cache.get("D011")["analysis"]["rg"]["handled"] == "yes"
