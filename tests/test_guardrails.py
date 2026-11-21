from app import guardrails
from app.data import load_all
from app.metrics import full_metrics
from tests.helpers import make_analysis, rec, viol


def run(did, **over):
    d = load_all()["dialogs"][did]
    return guardrails.apply(make_analysis(did, **over), d, full_metrics(d, load_all()["players"][d["player_id"]]))


def test_invalid_refs_removed_and_timeline_authors_checked():
    a = make_analysis("D002")
    a["violations"] = [viol("UNRESOLVED_REQUEST", "medium", ["D002#1", "D999#9"])]
    a["player_points"].append({"message_id": "D002#2", "dissatisfaction": 10, "state": "neutral"})  # менеджер
    out = run("D002", violations=a["violations"], player_points=a["player_points"])
    assert out["violations"][0]["message_refs"] == ["D002#1"]
    assert [p["message_id"] for p in out["player_points"]] == ["D002#1"] or "D002#2" not in [p["message_id"] for p in out["player_points"]]


def test_category_is_derived_from_code():
    out = run("D002", violations=[viol("FALSE_PROMISE", "high", ["D002#2"], category="communication")])
    assert out["violations"][0]["category"] == "operational"


def test_one_problem_is_shown_once_with_merged_refs():
    out = run("D006", violations=[
        viol("NO_FOLLOW_UP", "medium", ["D006#2"], title="Нет статуса"),
        viol("NO_FOLLOW_UP", "high", ["D006#4"], title="Нет следующего шага"),
        viol("DRY_TONE", "low", ["D006#2"], title="Сухой тон"),
    ])
    assert len(out["violations"]) == 2
    nf = next(v for v in out["violations"] if v["code"] == "NO_FOLLOW_UP")
    assert nf["message_refs"] == ["D006#2", "D006#4"] and nf["severity"] == "high"


def test_text_is_shortened():
    long = "Первое предложение. Второе предложение. Третье предложение, которое лишнее."
    out = run("D002", quality={**make_analysis("D002")["quality"], "summary": long},
              profile={"summary": long}, violations=[viol("DRY_TONE", "low", ["D002#2"], explanation="А. Б. В.")])
    assert out["quality"]["summary"] == "Первое предложение. Второе предложение."
    assert out["profile"]["summary"].count(".") == 2
    assert out["violations"][0]["explanation"] == "А."


def test_max_three_unique_recommendations():
    recs = [rec("Ответить игроку", "service"), rec("Ответить игроку!", "service"), rec("Разобрать диалог", "coaching"),
            rec("Проверить статус вывода", "service"), rec("Уточнить условия", "service")]
    out = run("D002", recommendations=recs)
    assert len(out["recommendations"]) == 3
    assert len({guardrails._norm(r["text"]) for r in out["recommendations"]}) == 3


# ---------- Responsible Gambling ----------

def test_plain_loss_does_not_block_or_force_rg_intervention():
    """Обычный проигрыш без RG-сигнала: бонусные рекомендации не блокируются, RG-меры не навязываются."""
    out = run("D003", recommendations=[rec("Предложить небольшой бонус после решения вопроса", "commercial"),
                                       rec("Предложить игроку поставить лимит и сделать паузу", "protective")])
    kinds = [(r["kind"], r["blocked"]) for r in out["recommendations"]]
    assert ("commercial", False) in kinds
    assert not any(r["kind"] == "protective" for r in out["recommendations"])
    assert out["priority"]["level"] == "low"


def test_self_exclusion_unhandled_is_critical_and_blocks_incentives():
    out = run("D010", rg={"signal": "self_exclusion_request", "handled": "no", "message_refs": ["D010#1"]},
              violations=[viol("INCENTIVE_AFTER_STOP_REQUEST", "critical", ["D010#2"], category="rg_safety")],
              recommendations=[rec("Предложить кешбэк 20%", "commercial"),
                               rec("Начислить фриспины чтобы удержать", "service")])
    assert out["priority"]["level"] == "critical"
    assert all(r["blocked"] for r in out["recommendations"] if r["kind"] == "protective" and "original_text" in r)
    assert out["recommendations"][0]["source"] == "guardrail"
    assert not any(guardrails.COMMERCIAL_RE.search(r["text"]) for r in out["recommendations"]
                   if not r.get("source") and not r["blocked"])


def test_correctly_handled_self_exclusion_not_a_manager_problem():
    out = run("D011", rg={"signal": "self_exclusion_request", "handled": "yes", "message_refs": ["D011#1"]},
              recommendations=[rec("Выдать офферы через месяц", "commercial")])
    assert out["violations"] == []
    assert out["priority"]["level"] == "medium"          # контроль, но не critical
    assert out["recommendations"][0]["blocked"] is True   # запрет офферов сохраняется
    assert not any(r.get("source") == "guardrail" for r in out["recommendations"])


def test_self_harm_risk_unhandled_gets_protective_action():
    out = run("D003", rg={"signal": "self_harm_risk", "handled": "no", "message_refs": ["D003#3"]})
    assert out["priority"]["level"] == "critical"
    assert "безопасности" in out["recommendations"][0]["text"] and out["recommendations"][0]["kind"] == "protective"


def test_ambiguous_distress_blocks_commercial_but_not_critical():
    out = run("D003", rg={"signal": "ambiguous_distress", "handled": "no", "message_refs": []},
              recommendations=[rec("Предложить бонус", "commercial")])
    assert out["recommendations"][0]["blocked"] is True
    assert out["priority"]["level"] == "medium"


def test_limit_set_alone_only_adds_caution():
    out = run("D004", recommendations=[rec("Предложить небольшой бонус", "commercial")])
    r = out["recommendations"][0]
    assert r["blocked"] is False and "лимит" in r["caution"]


def test_negated_service_recommendation_is_kept_under_restriction():
    out = run("D003", rg={"signal": "loss_of_control", "handled": "partly", "message_refs": []},
              recommendations=[rec("Не предлагать бонусы, обсудить паузу", "service")])
    assert out["recommendations"][0]["blocked"] is False


# ---------- операционные сигналы и приоритет ----------

def test_chargeback_threat_raises_priority_to_high():
    out = run("D006", risk_flags=["chargeback_threat"])
    assert out["priority"]["level"] == "high" and "чарджбэк" in out["priority"]["reason"].lower()


def test_priority_levels_follow_violation_severity_and_unanswered_tail():
    assert run("D002")["priority"]["level"] == "low"
    assert run("D002", violations=[viol("MISINFORMATION", "high", ["D002#2"])])["priority"]["level"] == "high"
    # вежливое закрытие диалога игроком без ответа менеджера само по себе приоритет не повышает
    assert run("D008")["priority"]["level"] == "low"
    assert run("D008", violations=[viol("NO_FOLLOW_UP", "medium", ["D008#6"], category="communication")])["priority"]["level"] == "medium"
    assert run("D002", violations=[viol("DRY_TONE", "low", ["D002#2"], category="communication")])["priority"]["level"] == "low"
