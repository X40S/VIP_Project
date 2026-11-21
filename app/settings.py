"""Настройки AI: хранятся на сервере в config/settings.json (не в git).

Ключи вводятся через интерфейс (Настройки). Переменные окружения GEMINI_API_KEY и
ANTHROPIC_API_KEY поддерживаются как необязательный запасной вариант.
"""
import json
import os
import threading
from pathlib import Path

from .data import ROOT

SETTINGS_PATH = ROOT / "config" / "settings.json"
_lock = threading.Lock()

# models: показываемый список моделей (у OpenAI без ключа это запасной список, с ключом берутся новейшие с API)
PROVIDERS = {
    "gemini": {"label": "Google Gemini", "env": "GEMINI_API_KEY", "default_model": "gemini-3.8-flash",
               "recommended": "gemini-3.8-flash",
               "models": ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"]},
    "anthropic": {"label": "Anthropic", "env": "ANTHROPIC_API_KEY", "default_model": "claude-opus-5-5",
                  "recommended": "claude-opus-5-5",
                  "models": ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]},
    "openai": {"label": "OpenAI", "env": "OPENAI_API_KEY", "default_model": "gpt-5-mini",
               "recommended": "gpt-5-mini", "models": ["gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4.1-mini"]},
}
DEFAULT_PROVIDER = "gemini"
DEFAULT_CONCURRENCY = 2


def _read() -> dict:
    """Читает файл и приводит старый плоский формат к многопровайдерному, не теряя ключ."""
    try:
        raw = json.loads(Path(SETTINGS_PATH).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"provider": DEFAULT_PROVIDER, "providers": {}, "concurrency": DEFAULT_CONCURRENCY}
    if "providers" not in raw:
        prov = raw.get("provider", DEFAULT_PROVIDER)
        raw = {"provider": prov, "concurrency": raw.get("concurrency", DEFAULT_CONCURRENCY),
               "providers": {prov: {"model": raw.get("model", ""), "api_key": raw.get("api_key", "")}}}
    raw.setdefault("provider", DEFAULT_PROVIDER)
    raw.setdefault("concurrency", DEFAULT_CONCURRENCY)
    return raw


def _provider_cfg(raw: dict, name: str) -> dict:
    p = raw["providers"].get(name, {})
    meta = PROVIDERS[name]
    key = p.get("api_key") or os.environ.get(meta["env"], "")
    return {"provider": name, "model": p.get("model") or meta["default_model"], "api_key": key,
            "concurrency": raw["concurrency"]}


def load(provider: str | None = None) -> dict:
    """Настройки провайдера (по умолчанию активного) вместе с ключом. Только для серверного кода."""
    raw = _read()
    name = provider or raw["provider"]
    if name not in PROVIDERS:
        name = DEFAULT_PROVIDER
    return _provider_cfg(raw, name)


def mask(key: str) -> str:
    if not key:
        return ""
    return key[:4] + "•" * 8 + key[-3:] if len(key) > 8 else "•" * len(key)


def public() -> dict:
    """Настройки для браузера: ключи только в маскированном виде."""
    raw = _read()
    provs = {}
    for name, meta in PROVIDERS.items():
        c = _provider_cfg(raw, name)
        provs[name] = {"label": meta["label"], "model": c["model"], "recommended": meta["recommended"],
                       "has_key": bool(c["api_key"]), "api_key_masked": mask(c["api_key"])}
    return {"provider": raw["provider"], "concurrency": raw["concurrency"], "providers": provs}


def save(provider: str, model: str | None = None, api_key: str | None = None,
         concurrency: int | None = None, make_active: bool = False) -> None:
    """Обновляет только переданные поля провайдера. Пустой или маскированный ключ не затирает сохранённый.

    Активный провайдер меняется только при make_active=True (явное решение пользователя).
    """
    if provider not in PROVIDERS:
        raise ValueError(f"Неизвестный провайдер: {provider}")
    with _lock:
        cur = _read()
        p = cur["providers"].setdefault(provider, {})
        if model and model.strip():
            p["model"] = model.strip()
        if api_key and api_key.strip() and "•" not in api_key:
            p["api_key"] = api_key.strip()
        if concurrency:
            cur["concurrency"] = max(1, min(int(concurrency), 6))
        if make_active:
            cur["provider"] = provider
        Path(SETTINGS_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(SETTINGS_PATH).write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
