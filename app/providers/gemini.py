"""Адаптер Google Gemini (google-genai). Интерфейс: generate_json, test_connection, list_models."""
import json
import random
import time

from google import genai
from google.genai import errors, types

from .base import ProviderError

RETRY_DELAYS = (8, 20, 45, 90)   # секунды между повторами при 429/503/таймауте
REQUEST_TIMEOUT_MS = 120_000     # один HTTP-запрос не должен висеть дольше 2 минут
DEADLINE_S = 330                 # общий предел на диалог, включая повторы


def _client(api_key: str):
    if not api_key:
        raise ProviderError("config", "API-ключ не задан. Введите его в Settings -> AI Settings.")
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))


def _daily_quota_message(e) -> str | None:
    """Если исчерпан суточный лимит, повторы бессмысленны: возвращает понятное сообщение."""
    details = (getattr(e, "details", None) or {}).get("error", {}).get("details", [])
    viol, retry = None, None
    for d in details:
        if d.get("@type", "").endswith("QuotaFailure"):
            viol = next(iter(d.get("violations", [])), None)
        elif d.get("@type", "").endswith("RetryInfo"):
            retry = d.get("retryDelay")
    if not viol or "PerDay" not in str(viol.get("quotaId", "")):
        return None
    model = (viol.get("quotaDimensions") or {}).get("model", "?")
    when = ""
    try:
        sec = int(float(str(retry).rstrip("s")))
        when = f" Сброс примерно через {sec // 3600} ч {sec % 3600 // 60} мин."
    except (TypeError, ValueError):
        pass
    return (f"Суточный лимит запросов исчерпан для модели {model} (лимит: {viol.get('quotaValue', '?')} в сутки).{when} "
            "Выберите другую модель в Настройках AI, подождите сброса лимита или включите оплату в Google AI Studio.")


def _map_error(e: errors.APIError) -> ProviderError:
    code = getattr(e, "code", None)
    msg = (getattr(e, "message", None) or str(e))[:300]
    if code in (401, 403) or (code == 400 and "API key" in msg):
        return ProviderError("auth", f"Неверный ключ или нет доступа ({code}): {msg}")
    if code == 404:
        return ProviderError("config", f"Модель недоступна для этого ключа: {msg} Выберите другую в Settings -> AI Settings.")
    if code == 429:
        daily = _daily_quota_message(e)
        if daily:
            return ProviderError("quota_daily", daily)
        return ProviderError("rate_limit", f"Превышен лимит запросов: {msg}")
    if code and code >= 500:
        return ProviderError("unavailable", f"Сервис недоступен ({code}): {msg}")
    return ProviderError("other", f"Ошибка API ({code}): {msg}")


def generate_json(settings: dict, system: str, user: str, schema) -> dict:
    """Возвращает разобранный JSON. schema это Pydantic-модель."""
    client = _client(settings["api_key"])
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0.2,
    )
    last: ProviderError | None = None
    t0 = time.time()
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            resp = client.models.generate_content(model=settings["model"], contents=user, config=config)
        except errors.APIError as e:
            last = _map_error(e)
        except Exception as e:  # сеть, таймаут запроса
            last = ProviderError("unavailable", f"Сбой соединения или таймаут ({type(e).__name__}): {e}")
        else:
            last = None
        if last is not None:
            delay = RETRY_DELAYS[attempt] + random.uniform(0, 3) if attempt < len(RETRY_DELAYS) else None
            retryable = last.kind in ("rate_limit", "unavailable")
            if retryable and delay is not None and time.time() - t0 + delay < DEADLINE_S:
                time.sleep(delay)
                continue
            raise last

        if not resp.candidates:
            reason = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
            raise ProviderError("refused", f"Модель не вернула ответ (блокировка: {reason})")
        text = resp.text
        if not text:
            raise ProviderError("bad_response", "Пустой ответ модели")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ProviderError("bad_response", f"Ответ не является JSON: {e}")
    raise last or ProviderError("other", "Неизвестная ошибка")


def test_connection(settings: dict) -> str:
    """Проверка ключа и модели без генерации (квота запросов не тратится)."""
    client = _client(settings["api_key"])
    try:
        info = client.models.get(model=settings["model"])
    except errors.APIError as e:
        raise _map_error(e)
    except Exception as e:
        raise ProviderError("unavailable", f"Сбой соединения: {e}")
    return (info.name or settings["model"]).split("/", 1)[-1]


NON_TEXT_MARKERS = ("image", "tts", "transcribe", "computer-use", "robotics", "nano-banana", "embedding")


def is_text_model(name: str) -> bool:
    """Подходит ли модель для текстового анализа с JSON-ответом (исключаем image, tts и т.п.)."""
    return name.startswith("gemini") and not any(mk in name for mk in NON_TEXT_MARKERS)


def list_models(settings: dict) -> list[str]:
    client = _client(settings["api_key"])
    try:
        names = [m.name.split("/", 1)[-1] for m in client.models.list()
                 if "generateContent" in (m.supported_actions or [])]
    except errors.APIError as e:
        raise _map_error(e)
    return sorted(n for n in names if is_text_model(n))
