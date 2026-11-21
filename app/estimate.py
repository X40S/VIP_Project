"""Приблизительная оценка объёма и стоимости анализа. Запросов к API не делает."""
from . import analyzer
from .data import load_all
from .metrics import full_metrics

# USD за 1 млн токенов (вход, выход). Только подтверждённые цены официального прайса Anthropic.
ANTHROPIC_PRICES = {
    "claude-fable-5-2": (8.0, 40.0), "claude-fable-5-1": (10.0, 50.0), "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50), "claude-haiku-4-5": (1.0, 5.0),
}
CHARS_PER_TOKEN = 3.0        # грубая оценка для русского и английского текста
OUTPUT_TOKENS_PER_DIALOG = 4000   # ответ плюс размышления модели


def _input_tokens(dialog_ids: list[str]) -> int:
    db = load_all()
    system_chars = len(analyzer.system_prompt())
    total = 0
    for did in dialog_ids:
        d = db["dialogs"][did]
        p = db["players"][d["player_id"]]
        total += int((system_chars + len(analyzer.build_user_prompt(d, p, full_metrics(d, p)))) / CHARS_PER_TOKEN)
    return total


def cost_usd(provider: str, model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Стоимость в USD или None, если цена модели не подтверждена (например, у Gemini она зависит от тарифа)."""
    if provider == "anthropic" and model in ANTHROPIC_PRICES:
        pin, pout = ANTHROPIC_PRICES[model]
        return round((tokens_in * pin + tokens_out * pout) / 1_000_000, 4)
    return None


def _note(provider: str, cost: float | None) -> str | None:
    if cost is not None:
        return None
    names = {"gemini": "Google", "openai": "OpenAI"}
    return f"Стоимость зависит от вашего тарифа {names[provider]}" if provider in names else "Цена модели неизвестна"


def estimate(dialog_ids: list[str], cfg: dict) -> dict:
    tokens_in = _input_tokens(dialog_ids)
    tokens_out = OUTPUT_TOKENS_PER_DIALOG * len(dialog_ids)
    cost = cost_usd(cfg["provider"], cfg["model"], tokens_in, tokens_out)
    return {"provider": cfg["provider"], "model": cfg["model"], "count": len(dialog_ids),
            "input_tokens": tokens_in, "output_tokens": tokens_out, "cost_usd": cost, "note": _note(cfg["provider"], cost)}


def model_prices(provider: str, models: list[str]) -> dict:
    """Ориентировочная цена анализа одного среднего диалога для каждой модели (None, если цена неизвестна)."""
    ids = load_all()["order"]
    avg_in = _input_tokens(ids) // max(len(ids), 1)
    return {m: cost_usd(provider, m, avg_in, OUTPUT_TOKENS_PER_DIALOG) for m in models}
