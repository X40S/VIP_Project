"""Адаптер OpenAI (официальный SDK openai). Интерфейс: generate_json, test_connection, list_models."""
import re

import openai
from pydantic import ValidationError

from .base import ProviderError

REQUEST_TIMEOUT_S = 180
LIST_LIMIT = 6   # сколько новейших чат-моделей показывать
NON_CHAT = ("audio", "realtime", "image", "tts", "transcribe", "embedding", "moderation", "search",
            "whisper", "dall", "computer-use", "instruct", "sora")
_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")   # датированные копии не показываем, только основное имя


def _client(api_key: str):
    if not api_key:
        raise ProviderError("config", "API-ключ не задан.")
    # одна повторная попытка SDK на временные сбои, без массовых повторов (защита от лишних расходов)
    return openai.OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=1)


def _map_error(e: Exception) -> ProviderError:
    msg = (getattr(e, "message", None) or str(e))[:300]
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ProviderError("auth", f"Неверный ключ или нет доступа: {msg}")
    if isinstance(e, openai.NotFoundError):
        return ProviderError("config", f"Модель недоступна для этого ключа: {msg}")
    if isinstance(e, openai.RateLimitError):
        if "insufficient_quota" in str(getattr(e, "code", "")) + msg or "quota" in msg.lower():
            return ProviderError("billing", f"Исчерпана квота или нет средств на балансе: {msg}")
        return ProviderError("rate_limit", f"Превышен лимит запросов: {msg}")
    if isinstance(e, (openai.APITimeoutError, openai.APIConnectionError)):
        return ProviderError("unavailable", f"Сбой соединения или таймаут: {msg}")
    if isinstance(e, openai.APIStatusError):
        if e.status_code >= 500:
            return ProviderError("unavailable", f"Сервис недоступен ({e.status_code}): {msg}")
        return ProviderError("other", f"Ошибка API ({e.status_code}): {msg}")
    return ProviderError("other", f"Ошибка: {msg}")


def generate_json(settings: dict, system: str, user: str, schema) -> dict:
    """Один запрос со структурированным JSON-ответом. schema это Pydantic-модель."""
    client = _client(settings["api_key"])
    try:
        resp = client.chat.completions.parse(
            model=settings["model"], response_format=schema,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
    except ValidationError as e:
        raise ProviderError("bad_response", f"Ответ не соответствует схеме: {str(e)[:300]}")
    except openai.LengthFinishReasonError:
        raise ProviderError("bad_response", "Ответ обрезан по лимиту токенов.")
    except openai.ContentFilterFinishReasonError:
        raise ProviderError("refused", "Ответ заблокирован фильтром безопасности.")
    except openai.OpenAIError as e:
        raise _map_error(e)
    choice = resp.choices[0]
    if choice.message.refusal:
        raise ProviderError("refused", "Модель отказалась отвечать (политика безопасности).")
    if choice.finish_reason == "length":
        raise ProviderError("bad_response", "Ответ обрезан по лимиту токенов.")
    if choice.message.parsed is None:
        raise ProviderError("bad_response", "Пустой ответ модели")
    return choice.message.parsed.model_dump()


def test_connection(settings: dict) -> str:
    """Проверка ключа и модели без генерации (токены не тратятся)."""
    client = _client(settings["api_key"])
    try:
        return client.models.retrieve(settings["model"]).id
    except openai.OpenAIError as e:
        raise _map_error(e)
    except Exception as e:
        raise ProviderError("unavailable", f"Сбой соединения: {e}")


def is_chat_model(model_id: str) -> bool:
    return bool(re.match(r"^(gpt-|o\d)", model_id)) and not any(m in model_id for m in NON_CHAT) and not _SNAPSHOT.search(model_id)


def list_models(settings: dict) -> list[str]:
    """Новейшие чат-модели, доступные ключу (без датированных копий и неподходящих типов)."""
    client = _client(settings["api_key"])
    try:
        items = [m for m in client.models.list() if is_chat_model(m.id)]
    except openai.OpenAIError as e:
        raise _map_error(e)
    items.sort(key=lambda m: getattr(m, "created", 0) or 0, reverse=True)
    return [m.id for m in items[:LIST_LIMIT]]
