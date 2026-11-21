"""Сборка промпта, вызов провайдера, валидация и guardrails для одного диалога."""
import json
import threading
import time
from datetime import datetime, timezone

from pydantic import ValidationError

from . import cache, guardrails, rules, settings
from .data import ROOT, dialog_hash, load_all
from .metrics import full_metrics
from .providers.base import ProviderError, get_provider
from .schemas import SCHEMA_VERSION, Analysis

PROMPT_VERSION = 2
ENGINE = f"s{SCHEMA_VERSION}p{PROMPT_VERSION}"

SAFETY_PATH = ROOT / "rules" / "safety_baseline.md"   # обязательные меры, не редактируются через интерфейс правил

OUTPUT_INSTRUCTIONS = """
# Формат ответа
Верни один JSON-объект строго по заданной схеме. Для каждого сообщения игрока верни точку в `player_points`, для каждого сообщения менеджера точку в `manager_points`. Используй только ID сообщений из входных данных. Метрики и флаги в `computed_metrics` вычислены кодом и достоверны. Приоритет не заполняй. Пиши кратко: каждое текстовое поле (`summary`, `explanation`, `text`, `title`) не длиннее 1-2 предложений, без повторов и пересказа переписки.
""".strip()

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _dialog_lock(dialog_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(dialog_id, threading.Lock())


def system_prompt() -> str:
    """Читает актуальные правила при каждом вызове, поэтому изменения применяются без перезапуска."""
    safety = SAFETY_PATH.read_text(encoding="utf-8").strip()
    return rules.prompt_text() + "\n\n" + safety + "\n\n" + OUTPUT_INSTRUCTIONS


PLAYER_PROMPT_FIELDS = (
    "player_id", "geo", "language", "status", "account_status", "vip_since", "deposits_30d_usd",
    "deposits_30d_count", "deposits_prev_30d_usd", "deposits_prev_30d_count", "failed_deposits_7d",
    "withdrawals_30d_usd", "pending_withdrawal_usd", "bonuses_30d_usd", "bonuses_30d_count",
    "last_deposit_at", "last_login_at", "birthday", "favorite_game",
)


def build_user_prompt(dialog: dict, player: dict, metrics: dict) -> str:
    resp = metrics["dialog"]["response_minutes"]
    messages = []
    for m in dialog["messages"]:
        item = {"id": m["id"], "from": m["from"], "sent_at": m["sent_at"], "text": m["text"]}
        if m["id"] in resp:
            item["minutes_player_waited_for_this_reply"] = resp[m["id"]]
        messages.append(item)
    payload = {
        "as_of": load_all()["as_of"],
        "dialog": {"id": dialog["dialog_id"], "channel": dialog["channel"],
                   "manager": dialog["manager"], "messages": messages},
        "player_card": {k: player.get(k) for k in PLAYER_PROMPT_FIELDS if k in player},
        "computed_metrics": {"player": metrics["player"],
                             "dialog": {k: v for k, v in metrics["dialog"].items() if k != "response_minutes"}},
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def current_signature(dialog_id: str, cfg: dict | None = None) -> dict:
    cfg = cfg or settings.load()
    db = load_all()
    d = db["dialogs"][dialog_id]
    return {
        "provider": cfg["provider"], "model": cfg["model"],
        "rules_version": rules.current_version(), "engine": ENGINE,
        "dialog_hash": dialog_hash(d, db["players"][d["player_id"]]),
    }


def build_record(dialog_id: str, analysis: dict, meta_extra: dict, cfg: dict | None = None) -> dict:
    """Единая сборка записи результата для обоих режимов (после guardrails)."""
    db = load_all()
    dialog = db["dialogs"][dialog_id]
    metrics = full_metrics(dialog, db["players"][dialog["player_id"]])
    final = guardrails.apply(analysis, dialog, metrics)
    return {
        "dialog_id": dialog_id, "player_id": dialog["player_id"], "manager_id": dialog["manager"],
        "analysis": final, "meta": {**current_signature(dialog_id, cfg), **meta_extra},
    }


def analyze(dialog_id: str, cfg: dict | None = None) -> dict:
    """Анализ одного диалога через API. Сохраняет запись и возвращает её. Ошибки: ProviderError."""
    cfg = cfg or settings.load()
    if not cfg.get("api_key"):
        raise ProviderError("config", "API-ключ не задан. Введите его в Настройках.")
    db = load_all()
    dialog = db["dialogs"][dialog_id]
    player = db["players"][dialog["player_id"]]
    metrics = full_metrics(dialog, player)
    provider = get_provider(cfg["provider"])
    system, user = system_prompt(), build_user_prompt(dialog, player, metrics)

    with _dialog_lock(dialog_id):
        t0 = time.time()
        validated = None
        for attempt in range(2):   # один повтор, если ответ не прошёл схему
            raw = provider.generate_json(cfg, system, user, Analysis)
            try:
                validated = Analysis.model_validate(raw).model_dump()
                break
            except ValidationError as e:
                if attempt == 1:
                    raise ProviderError("bad_response", f"Ответ не соответствует схеме: {str(e)[:300]}")
        record = build_record(dialog_id, validated, {
            "source": cfg["provider"],
            "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "duration_s": round(time.time() - t0, 1),
        }, cfg)
        cache.put(dialog_id, record)
        return record
