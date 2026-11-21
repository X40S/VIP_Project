"""Проверки в коде: ссылки, краткость, дубли, ограничения по безопасности, приоритет.

Работают независимо от модели и одинаково для AI-режима и импорта. Оценки не меняются.
"""
import re

from .schemas import CODE_CATEGORY, STOP_SIGNALS

RANKS = {"low": 0, "medium": 1, "high": 2, "critical": 3}
RANK_NAMES = {v: k for k, v in RANKS.items()}

COMMERCIAL_RE = re.compile(
    r"бонус|фриспин|free\s*spin|кешбэк|кэшбэк|cashback|reload|релоад|депозит|deposit|"
    r"оффер|offer|промо|promo|турнир|tournament",
    re.IGNORECASE,
)
NEGATION_RE = re.compile(
    r"\bне\b|нельзя|избег|без\s|запрещ|воздерж|отказ|\bno\b|\bnot\b|avoid|don't|do not|never|without|stop|прекрат",
    re.IGNORECASE,
)
RG_TOOL_RE = re.compile(r"лимит|пауз|самоисключ|self-?exclu|limit|cool.?off|break", re.IGNORECASE)

PROTECTIVE_TEXTS = {
    "self_exclusion_request": "Инициировать процедуру самоисключения, не предлагать бонусы и офферы.",
    "self_harm_risk": "Срочно передать обращение ответственной команде по безопасности игроков, дать ресурсы поддержки, без офферов.",
    "loss_of_control": "Предложить инструменты контроля игры и ресурсы помощи, без бонусов и офферов.",
    "serious_harm": "Предложить инструменты контроля игры и ресурсы помощи, без бонусов и офферов.",
}
DRIVER_LABELS = {
    "rg_open": "Запрос или сигнал безопасности без реакции",
    "rg_monitor": "Сигнал безопасности обработан, нужен контроль",
    "violation": "Нарушение менеджера",
    "chargeback": "Упоминание чарджбэка",
    "churn": "Риск ухода игрока",
}
MAX_RECS = 3
KIND_ORDER = {"protective": 0, "service": 1, "coaching": 2, "commercial": 3}


def shorten(text, max_sentences: int, max_chars: int):
    """Обрезает текст до N предложений и максимального числа символов (по границе слова)."""
    if not text:
        return text
    text = " ".join(str(text).split())
    parts = re.split(r"(?<=[.!?])\s+", text)
    text = " ".join(parts[:max_sentences])
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:- ")
        if not text.endswith((".", "!", "?")):
            text += "."
    return text


def _norm(s: str) -> str:
    return re.sub(r"[^\w]+", " ", s.lower()).strip()


def _clean_refs(refs, valid: set, where: str, notes: list) -> list:
    out = []
    for r in refs or []:
        if r in valid:
            if r not in out:
                out.append(r)
        else:
            notes.append(f"Недействительная ссылка {r!r} удалена ({where})")
    return out


def _looks_commercial(text: str) -> bool:
    return bool(COMMERCIAL_RE.search(text)) and not NEGATION_RE.search(text)


def _merge_violations(items: list, notes: list) -> list:
    """Одна проблема отображается один раз: объединяем по коду и по одинаковому названию."""
    merged: list[dict] = []
    for v in items:
        key_title = _norm(v["title"])
        target = next((m for m in merged if m["code"] == v["code"] or _norm(m["title"]) == key_title), None)
        if target is None:
            merged.append(v)
            continue
        notes.append(f"Объединены дубли нарушения {v['code']}")
        target["message_refs"] = list(dict.fromkeys(target["message_refs"] + v["message_refs"]))
        if RANKS[v["severity"]] > RANKS[target["severity"]]:
            target["severity"] = v["severity"]
    return merged


def apply(analysis: dict, dialog: dict, metrics: dict) -> dict:
    """Возвращает копию анализа с исправлениями и приоритетом."""
    a = {k: (list(v) if isinstance(v, list) else v) for k, v in analysis.items()}
    notes: list[str] = []
    by_id = {m["id"]: m for m in dialog["messages"]}
    valid = set(by_id)

    # 1. Эмоциональная динамика: только свои сообщения, без дублей.
    for key, author in (("player_points", "player"), ("manager_points", "manager")):
        seen, kept = set(), []
        for p in a.get(key, []):
            mid = p["message_id"]
            if mid not in valid or by_id[mid]["from"] != author or mid in seen:
                notes.append(f"Точка {mid!r} отброшена ({key})")
                continue
            seen.add(mid)
            kept.append(p)
        a[key] = kept

    # 2. Ссылки, категории, краткость.
    a["rg"]["message_refs"] = _clean_refs(a["rg"]["message_refs"], valid, "rg", notes)
    for v in a["violations"]:
        v["message_refs"] = _clean_refs(v["message_refs"], valid, "violations", notes)
        v["category"] = CODE_CATEGORY.get(v["code"], v["category"])
        v["title"] = shorten(v["title"], 1, 70).rstrip(".")
        v["explanation"] = shorten(v["explanation"], 1, 200)
    for r in a["recommendations"]:
        r["message_refs"] = _clean_refs(r["message_refs"], valid, "recommendations", notes)
        r["text"] = shorten(r["text"], 1, 160)
    a["profile"] = {"summary": shorten(a["profile"].get("summary"), 2, 260)}
    a["quality"]["summary"] = shorten(a["quality"]["summary"], 2, 260)
    a["violations"] = _merge_violations(a["violations"], notes)
    a["violations"].sort(key=lambda v: -RANKS[v["severity"]])

    # 3. Контекст безопасности.
    flags = metrics["player"]["flags"]
    signal = a["rg"]["signal"]
    handled = a["rg"]["handled"]
    stop = signal in STOP_SIGNALS
    restrict = stop or signal == "ambiguous_distress" or "SELF_EXCLUDED" in flags
    limit_only = "LIMIT_SET" in flags and not restrict

    blocked = 0
    kept_recs = []
    for rec in a["recommendations"]:
        rec["blocked"] = False
        if restrict and (rec["kind"] == "commercial" or (rec["kind"] == "service" and _looks_commercial(rec["text"]))):
            rec["original_text"] = rec["text"]
            rec["text"] = "Рекомендация заблокирована правилами безопасности."
            rec["kind"], rec["blocked"] = "protective", True
            blocked += 1
        elif signal == "none" and "SELF_EXCLUDED" not in flags and rec["kind"] == "protective" and RG_TOOL_RE.search(rec["text"]):
            notes.append("Удалена RG-рекомендация без RG-сигнала")
            continue
        elif limit_only and rec["kind"] == "commercial":
            rec["caution"] = "Установлен депозитный лимит: не подталкивать к обходу."
        kept_recs.append(rec)
    if blocked:
        notes.append(f"Заблокировано рекомендаций: {blocked}")
    a["recommendations"] = kept_recs

    # 4. Обязательная защитная реакция, если сигнал безопасности не закрыт.
    if stop and handled != "yes" and not any(r["kind"] == "protective" and not r["blocked"] for r in a["recommendations"]):
        a["recommendations"].insert(0, {"text": PROTECTIVE_TEXTS[signal], "kind": "protective",
                                        "message_refs": a["rg"]["message_refs"][:1], "blocked": False,
                                        "source": "guardrail"})
        notes.append("Добавлена защитная рекомендация")

    # 5. Не более трёх уникальных рекомендаций.
    seen, uniq = set(), []
    for r in sorted(a["recommendations"], key=lambda r: KIND_ORDER.get(r["kind"], 9)):
        k = _norm(r["text"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    a["recommendations"] = uniq[:MAX_RECS]

    a["priority"] = compute_priority(a, metrics)
    a["guardrail_notes"] = notes
    return a


def compute_priority(a: dict, metrics: dict) -> dict:
    """Приоритет: максимум из сигнала безопасности, нарушений и операционных сигналов."""
    signal, handled = a["rg"]["signal"], a["rg"]["handled"]
    drivers: dict[str, int] = {}

    if signal in STOP_SIGNALS:
        if handled == "yes":
            drivers["rg_monitor"] = 2 if signal == "self_harm_risk" else 1
        else:
            drivers["rg_open"] = 3 if handled in ("no", "not_applicable") else 2
    elif signal == "ambiguous_distress" and handled != "yes":
        drivers["rg_monitor"] = 1
    if a["violations"]:
        drivers["violation"] = max(RANKS[v["severity"]] for v in a["violations"])
    if "chargeback_threat" in a["risk_flags"]:
        drivers["chargeback"] = 2
    if "churn_threat" in a["risk_flags"]:
        drivers["churn"] = 1

    rank = max(drivers.values(), default=0)
    top = [k for k, v in drivers.items() if v == rank and rank > 0]
    return {
        "level": RANK_NAMES[rank],
        "reason": ", ".join(DRIVER_LABELS[k] for k in top) if top else "Существенных рисков нет",
        "drivers": sorted(drivers),
    }
