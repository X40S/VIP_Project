"""Детерминированные метрики и флаги. Это факты из данных, а не выводы модели."""
from datetime import date, datetime

from .data import as_of_date, parse_dt

# Пороги сигналов. Это эвристики для приоритизации, а не правила compliance.
PAYMENT_FAILURES_MIN = 3        # неудачных депозитов за 7 дней
DEPOSIT_DROP_PCT = -50.0        # падение суммы депозитов к предыдущим 30 дням
FREQ_SPIKE_RATIO = 2.0          # рост числа депозитов (раз)
INACTIVE_DAYS = 14              # дней без входа
BONUS_HEAVY_COUNT = 10          # бонусов за 30 дней
LONG_RESPONSE_MIN = 120         # ответ менеджера дольше 2 часов


def _pct(cur: float, prev: float):
    if prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def _days_since(s, today: date):
    if not s:
        return None
    return (today - date.fromisoformat(s[:10])).days


def _birthday_offset(birthday: str | None, today: date):
    """Смещение дня рождения от as_of в днях (отрицательное: уже прошёл), в пределах года."""
    if not birthday:
        return None
    b = date.fromisoformat(birthday)
    best = None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(year, b.month, b.day)
        except ValueError:  # 29 февраля
            d = date(year, b.month, 28)
        off = (d - today).days
        if best is None or abs(off) < abs(best):
            best = off
    return best


def player_metrics(p: dict) -> dict:
    """Метрики игрока. Отсутствующие в CSV поля дают None, динамика считается только при наличии данных."""
    today = as_of_date()
    g = p.get
    cur_usd, prev_usd = g("deposits_30d_usd"), g("deposits_prev_30d_usd")
    cur_n, prev_n = g("deposits_30d_count"), g("deposits_prev_30d_count")
    has_prev = prev_usd is not None and cur_usd is not None
    avg_cur = round(cur_usd / cur_n, 1) if cur_usd is not None and cur_n else None
    avg_prev = round(prev_usd / prev_n, 1) if prev_usd is not None and prev_n else None

    m = {
        "activity_change_available": bool(has_prev),
        "deposit_usd_change_pct": _pct(cur_usd, prev_usd) if has_prev else None,
        "deposit_count_change_pct": _pct(cur_n, prev_n) if cur_n is not None and prev_n is not None else None,
        "avg_deposit_usd": avg_cur,
        "avg_deposit_prev_usd": avg_prev,
        "days_since_login": _days_since(g("last_login_at"), today),
        "days_since_deposit": _days_since(g("last_deposit_at"), today),
        "days_since_withdrawal": _days_since(g("last_withdrawal_at"), today),
        "birthday_offset_days": _birthday_offset(g("birthday"), today),
    }

    flags = []
    if g("account_status") == "self_excluded":
        flags.append("SELF_EXCLUDED")
    if g("account_status") == "limit_set":
        flags.append("LIMIT_SET")
    if (g("failed_deposits_7d") or 0) >= PAYMENT_FAILURES_MIN:
        flags.append("PAYMENT_FAILURES")
    pct = m["deposit_usd_change_pct"]
    if pct is not None and pct <= DEPOSIT_DROP_PCT:
        flags.append("DEPOSIT_DROP")
    if prev_n and cur_n is not None and cur_n >= prev_n * FREQ_SPIKE_RATIO and avg_cur and avg_prev and avg_cur < avg_prev:
        flags.append("DEPOSIT_FREQUENCY_SPIKE")
    if (g("pending_withdrawal_usd") or 0) > 0:
        flags.append("PENDING_WITHDRAWAL")
    if m["days_since_login"] is not None and m["days_since_login"] > INACTIVE_DAYS:
        flags.append("INACTIVE")
    if (g("bonuses_30d_count") or 0) >= BONUS_HEAVY_COUNT:
        flags.append("BONUS_HEAVY")
    m["flags"] = flags
    return m


def dialog_metrics(dialog: dict) -> dict:
    """Тайминги переписки: ответы менеджера, неотвеченные сообщения, серия сообщений без ответа игрока."""
    msgs = dialog["messages"]
    response_times: dict[str, float] = {}   # message id менеджера -> минуты ожидания игрока
    pending_since: datetime | None = None
    run = max_run = 0

    for m in msgs:
        t = parse_dt(m["sent_at"])
        if m["from"] == "player":
            if pending_since is None:
                pending_since = t
            run = 0
        else:
            if pending_since is not None:
                response_times[m["id"]] = round((t - pending_since).total_seconds() / 60, 1)
                pending_since = None
            run += 1
            max_run = max(max_run, run)

    unanswered = 0
    for m in reversed(msgs):
        if m["from"] != "player":
            break
        unanswered += 1

    first_resp = None
    if msgs and msgs[0]["from"] == "player":
        first_resp = next(iter(response_times.values()), None)

    return {
        "response_minutes": response_times,
        "first_response_minutes": first_resp,
        "max_response_minutes": max(response_times.values()) if response_times else None,
        "long_responses": [i for i, v in response_times.items() if v > LONG_RESPONSE_MIN],
        "unanswered_player_messages": unanswered,
        "manager_messages_in_row": max_run,
        "started_by": msgs[0]["from"] if msgs else None,
        "player_replied": any(m["from"] == "player" for m in msgs),
    }


def full_metrics(dialog: dict, player: dict) -> dict:
    return {"player": player_metrics(player), "dialog": dialog_metrics(dialog)}
