import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app import analyzer, estimate, settings
from app.providers import claude
from app.providers.base import FATAL_KINDS, ProviderError
from app.schemas import Analysis
from tests.helpers import make_analysis


# ---------- настройки и переключение провайдеров ----------

def test_settings_default_is_gemini_and_both_providers_listed():
    pub = settings.public()
    assert pub["provider"] == "gemini" and set(pub["providers"]) == {"gemini", "anthropic", "openai"}
    assert pub["providers"]["gemini"]["model"] == "gemini-3.8-flash"


def test_saving_other_provider_does_not_switch_active_without_confirmation():
    settings.save("gemini", "gemini-3.8-flash", "AIzaGEMINIKEY00", make_active=True)
    settings.save("anthropic", "claude-opus-5-5", "sk-ant-SECRETKEY99")
    assert settings.load()["provider"] == "gemini"
    settings.save("anthropic", make_active=True)
    cfg = settings.load()
    assert cfg["provider"] == "anthropic" and cfg["api_key"] == "sk-ant-SECRETKEY99"
    assert settings.load("gemini")["api_key"] == "AIzaGEMINIKEY00"        # ключ Gemini сохранён
    assert "SECRETKEY" not in json.dumps(settings.public())               # ключи не возвращаются в браузер


def test_legacy_flat_settings_file_is_migrated_without_losing_key():
    settings.SETTINGS_PATH.parent.mkdir(parents=True)
    settings.SETTINGS_PATH.write_text(json.dumps({"provider": "gemini", "model": "gemini-3.8-flash",
                                                  "api_key": "OLDKEY12345", "concurrency": 2}), encoding="utf-8")
    cfg = settings.load()
    assert cfg["api_key"] == "OLDKEY12345" and cfg["provider"] == "gemini"


def test_masked_key_does_not_overwrite_saved_key():
    settings.save("gemini", None, "REALKEY123456")
    settings.save("gemini", None, settings.public()["providers"]["gemini"]["api_key_masked"])
    assert settings.load("gemini")["api_key"] == "REALKEY123456"


def test_analyzer_uses_selected_provider_and_same_rules(fake, monkeypatch):
    prov = fake(fn=lambda cfg, s, u: make_analysis("D002"))
    seen = []
    monkeypatch.setattr(analyzer, "get_provider", lambda name: (seen.append(name), prov)[1])
    settings.save("gemini", "g-model", "k1", make_active=True)
    analyzer.analyze("D002")
    settings.save("anthropic", "c-model", "k2", make_active=True)
    analyzer.analyze("D002")
    assert seen == ["gemini", "anthropic"]
    assert prov.prompts[0][0] == prov.prompts[1][0]       # одинаковые правила для обоих провайдеров


def test_no_automatic_switch_from_gemini_to_anthropic_on_error(fake):
    def fail(cfg, s, u):
        raise ProviderError("quota_daily", "лимит")
    prov = fake(fn=fail)
    settings.save("gemini", "g", "k", make_active=True)
    with pytest.raises(ProviderError):
        analyzer.analyze("D002")
    assert settings.load()["provider"] == "gemini" and prov.calls == 1


# ---------- адаптер Anthropic (моки) ----------

class FakeMessages:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.kwargs = result, exc, None

    def parse(self, **kw):
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return self.result


class FakeModels:
    def __init__(self, ids=(), exc=None):
        self.ids, self.exc = ids, exc

    def list(self, limit=100):
        return [SimpleNamespace(id=i) for i in self.ids]

    def retrieve(self, model):
        if self.exc:
            raise self.exc
        return SimpleNamespace(id=model)


def _client(monkeypatch, messages=None, models=None):
    c = SimpleNamespace(messages=messages, models=models)
    monkeypatch.setattr(claude, "_client", lambda key: c)
    return c


def _resp(stop="end_turn", parsed=True):
    out = Analysis.model_validate(make_analysis("D002")) if parsed else None
    return SimpleNamespace(stop_reason=stop, parsed_output=out)


def _status_error(cls, code, message="x"):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx.Response(code, request=req), body=None)


def test_claude_generate_json_uses_structured_output(monkeypatch):
    m = FakeMessages(_resp())
    _client(monkeypatch, messages=m)
    out = claude.generate_json({"api_key": "k", "model": "claude-opus-5-5"}, "sys", "usr", Analysis)
    assert out["quality"]["overall"] == 70
    assert m.kwargs["output_format"] is Analysis and m.kwargs["model"] == "claude-opus-5-5"
    assert m.kwargs["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert "temperature" not in m.kwargs


@pytest.mark.parametrize("resp,kind", [(_resp("refusal", False), "refused"), (_resp("max_tokens", False), "bad_response"),
                                       (_resp("end_turn", False), "bad_response")])
def test_claude_stop_reasons(monkeypatch, resp, kind):
    _client(monkeypatch, messages=FakeMessages(resp))
    with pytest.raises(ProviderError) as e:
        claude.generate_json({"api_key": "k", "model": "m"}, "s", "u", Analysis)
    assert e.value.kind == kind


@pytest.mark.parametrize("cls,code,kind", [(anthropic.AuthenticationError, 401, "auth"),
                                           (anthropic.NotFoundError, 404, "config"),
                                           (anthropic.RateLimitError, 429, "rate_limit"),
                                           (anthropic.InternalServerError, 500, "unavailable")])
def test_claude_error_mapping(monkeypatch, cls, code, kind):
    _client(monkeypatch, messages=FakeMessages(exc=_status_error(cls, code)))
    with pytest.raises(ProviderError) as e:
        claude.generate_json({"api_key": "k", "model": "m"}, "s", "u", Analysis)
    assert e.value.kind == kind


def test_claude_billing_error_is_fatal(monkeypatch):
    err = _status_error(anthropic.BadRequestError, 400, "Your credit balance is too low")
    _client(monkeypatch, messages=FakeMessages(exc=err))
    with pytest.raises(ProviderError) as e:
        claude.generate_json({"api_key": "k", "model": "m"}, "s", "u", Analysis)
    assert e.value.kind == "billing" and "billing" in FATAL_KINDS


def test_claude_models_listing_and_connection_test_make_no_generation(monkeypatch):
    m = FakeMessages()
    _client(monkeypatch, messages=m, models=FakeModels(["claude-opus-5-5", "claude-haiku-5-5", "other-model"]))
    assert claude.list_models({"api_key": "k"}) == ["claude-opus-5-5", "claude-haiku-5-5"]
    assert claude.test_connection({"api_key": "k", "model": "claude-opus-5-5"}) == "claude-opus-5-5"
    assert m.kwargs is None                                   # messages.parse не вызывался
    _client(monkeypatch, models=FakeModels(exc=_status_error(anthropic.AuthenticationError, 401)))
    with pytest.raises(ProviderError) as e:
        claude.test_connection({"api_key": "bad", "model": "m"})
    assert e.value.kind == "auth"


def test_claude_requires_key():
    with pytest.raises(ProviderError) as e:
        claude.generate_json({"api_key": "", "model": "m"}, "s", "u", Analysis)
    assert e.value.kind == "config"


# ---------- оценка стоимости ----------

def test_estimate_makes_no_requests_and_prices_known_models():
    ids = ["D001", "D002"]
    a = estimate.estimate(ids, {"provider": "anthropic", "model": "claude-opus-5-5"})
    assert a["count"] == 2 and a["input_tokens"] > 1000 and a["cost_usd"] > 0
    b = estimate.estimate(ids, {"provider": "anthropic", "model": "claude-unknown-9"})
    assert b["cost_usd"] is None
    g = estimate.estimate(ids, {"provider": "gemini", "model": "gemini-3.8-flash"})
    assert g["cost_usd"] is None and "тариф" in g["note"]


# ---------- OpenAI (моки) ----------

def test_curated_model_lists_per_provider():
    assert settings.PROVIDERS["anthropic"]["models"] == ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    assert len(settings.PROVIDERS["gemini"]["models"]) == 5 and len(settings.PROVIDERS["openai"]["models"]) == 5


def test_openai_chat_model_filter_and_listing(monkeypatch):
    from app.providers import gpt
    for ok in ("gpt-5", "gpt-5-mini", "o3"):
        assert gpt.is_chat_model(ok)
    for bad in ("gpt-4o-audio-preview", "text-embedding-3-large", "gpt-image-1", "gpt-5-2025-08-07", "whisper-1", "gpt-4o-realtime-preview"):
        assert not gpt.is_chat_model(bad)
    items = [SimpleNamespace(id=f"gpt-{i}", created=i) for i in range(10)] + [SimpleNamespace(id="whisper-1", created=99)]
    monkeypatch.setattr(gpt, "_client", lambda key: SimpleNamespace(models=SimpleNamespace(list=lambda: items)))
    assert gpt.list_models({"api_key": "k"}) == ["gpt-9", "gpt-8", "gpt-7", "gpt-6", "gpt-5", "gpt-4"]


def _oa_resp(parsed=True, refusal=None, finish="stop"):
    out = Analysis.model_validate(make_analysis("D002")) if parsed else None
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(parsed=out, refusal=refusal))])


class _OAChat:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.kwargs = result, exc, None
        self.completions = self

    def parse(self, **kw):
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return self.result


def _oa_client(monkeypatch, chat=None, models=None):
    from app.providers import gpt
    monkeypatch.setattr(gpt, "_client", lambda key: SimpleNamespace(chat=chat, models=models))
    return gpt


def test_openai_generate_json_uses_structured_output(monkeypatch):
    chat = _OAChat(_oa_resp())
    gpt = _oa_client(monkeypatch, chat=chat)
    out = gpt.generate_json({"api_key": "k", "model": "gpt-5-mini"}, "sys", "usr", Analysis)
    assert out["quality"]["overall"] == 70
    assert chat.kwargs["response_format"] is Analysis and chat.kwargs["model"] == "gpt-5-mini"
    assert chat.kwargs["messages"][0] == {"role": "system", "content": "sys"} and "temperature" not in chat.kwargs


@pytest.mark.parametrize("resp,kind", [(_oa_resp(parsed=False, refusal="no"), "refused"), (_oa_resp(finish="length"), "bad_response"),
                                       (_oa_resp(parsed=False), "bad_response")])
def test_openai_stop_reasons(monkeypatch, resp, kind):
    gpt = _oa_client(monkeypatch, chat=_OAChat(resp))
    with pytest.raises(ProviderError) as e:
        gpt.generate_json({"api_key": "k", "model": "m"}, "s", "u", Analysis)
    assert e.value.kind == kind


def _oa_status(cls, code, message="x", body=None):
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return cls(message, response=httpx.Response(code, request=req), body=body)


def test_openai_error_mapping_and_billing_is_fatal(monkeypatch):
    import openai
    cases = [(openai.AuthenticationError, 401, "x", "auth"), (openai.NotFoundError, 404, "x", "config"),
             (openai.RateLimitError, 429, "slow down", "rate_limit"),
             (openai.RateLimitError, 429, "You exceeded your current quota", "billing"),
             (openai.InternalServerError, 500, "x", "unavailable")]
    for cls, code, text, kind in cases:
        gpt = _oa_client(monkeypatch, chat=_OAChat(exc=_oa_status(cls, code, text)))
        with pytest.raises(ProviderError) as e:
            gpt.generate_json({"api_key": "k", "model": "m"}, "s", "u", Analysis)
        assert e.value.kind == kind, (cls, kind)


def test_openai_connection_test_makes_no_generation_and_requires_key(monkeypatch):
    chat = _OAChat()
    gpt = _oa_client(monkeypatch, chat=chat, models=SimpleNamespace(retrieve=lambda m: SimpleNamespace(id=m)))
    assert gpt.test_connection({"api_key": "k", "model": "gpt-5"}) == "gpt-5" and chat.kwargs is None
    from app.providers import gpt as real
    monkeypatch.undo()
    with pytest.raises(ProviderError) as e:
        real.generate_json({"api_key": "", "model": "m"}, "s", "u", Analysis)
    assert e.value.kind == "config"


def test_openai_in_settings_estimate_and_models_endpoint():
    settings.save("openai", "gpt-5-mini", "sk-openai-KEY00000", make_active=True)
    assert settings.load()["provider"] == "openai" and settings.load("openai")["model"] == "gpt-5-mini"
    est = estimate.estimate(["D001"], settings.load())
    assert est["cost_usd"] is None and "OpenAI" in est["note"]
