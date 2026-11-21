"""Общий интерфейс провайдера LLM."""


class ProviderError(Exception):
    """kind: auth | rate_limit | quota_daily | billing | unavailable | bad_response | refused | config | other"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


# ошибки, после которых продолжать массовый анализ бессмысленно
FATAL_KINDS = ("auth", "config", "quota_daily", "billing")


def get_provider(name: str):
    if name == "gemini":
        from . import gemini
        return gemini
    if name == "anthropic":
        from . import claude
        return claude
    if name == "openai":
        from . import gpt
        return gpt
    raise ProviderError("config", f"Неизвестный провайдер: {name}")
