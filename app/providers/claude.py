"""Адаптер Anthropic (официальный SDK anthropic). Интерфейс: generate_json, test_connection, list_models."""
import anthropic
from pydantic import ValidationError

from .base import ProviderError

REQUEST_TIMEOUT_S = 180
MAX_TOKENS = 16000


def _client(api_key: str):
    if not api_key:
        raise ProviderError("config", "API-ключ не задан.")
    # одна повторная попытка SDK на временные сбои, без массовых повторов (защита от лишних расходов)
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=1)


def _map_error(e: Exception) -> ProviderError:
    msg = (getattr(e, "message", None) or str(e))[:300]
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ProviderError("auth", f"Неверный ключ или нет доступа: {msg}")
    if isinstance(e, anthropic.NotFoundError):
        return ProviderError("config", f"Модель недоступна для этого ключа: {msg}")
    if isinstance(e, anthropic.RateLimitError):
        return ProviderError("rate_limit", f"Превышен лимит запросов: {msg}")
    if isinstance(e, anthropic.BadRequestError) and "credit balance" in msg.lower():
        return ProviderError("billing", f"Недостаточно средств на балансе: {msg}")
    if isinstance(e, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return ProviderError("unavailable", f"Сбой соединения или таймаут: {msg}")
    if isinstance(e, anthropic.APIStatusError):
        if e.status_code >= 500:
            return ProviderError("unavailable", f"Сервис недоступен ({e.status_code}): {msg}")
        return ProviderError("other", f"Ошибка API ({e.status_code}): {msg}")
    return ProviderError("other", f"Ошибка: {msg}")


def generate_json(settings: dict, system: str, user: str, schema) -> dict:
    """Один запрос со структурированным JSON-ответом. schema это Pydantic-модель."""
    client = _client(settings["api_key"])
    try:
        resp = client.messages.parse(
            model=settings["model"], max_tokens=MAX_TOKENS,
            # одинаковые для всех диалогов правила кэшируются Anthropic (повторное чтение дешевле входных токенов)
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}], output_format=schema,
        )
    except ValidationError as e:
        raise ProviderError("bad_response", f"Ответ не соответствует схеме: {str(e)[:300]}")
    except anthropic.APIError as e:
        raise _map_error(e)
    if resp.stop_reason == "refusal":
        raise ProviderError("refused", "Модель отказалась отвечать (политика безопасности).")
    if resp.stop_reason == "max_tokens":
        raise ProviderError("bad_response", "Ответ обрезан по лимиту токенов.")
    if resp.parsed_output is None:
        raise ProviderError("bad_response", "Пустой ответ модели")
    return resp.parsed_output.model_dump()


def test_connection(settings: dict) -> str:
    """Проверка ключа и модели без генерации (токены не тратятся)."""
    client = _client(settings["api_key"])
    try:
        info = client.models.retrieve(settings["model"])
    except anthropic.APIError as e:
        raise _map_error(e)
    except Exception as e:
        raise ProviderError("unavailable", f"Сбой соединения: {e}")
    return info.id


def list_models(settings: dict) -> list[str]:
    client = _client(settings["api_key"])
    try:
        return [m.id for m in client.models.list(limit=100) if m.id.startswith("claude")]
    except anthropic.APIError as e:
        raise _map_error(e)
