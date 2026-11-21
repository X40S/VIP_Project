import pytest

from app.providers import gemini
from app.providers.base import ProviderError


class Resp:
    candidates = [object()]
    text = '{"ok": true}'


class FakeModels:
    def __init__(self, behaviours):
        self.behaviours, self.calls = list(behaviours), 0

    def generate_content(self, **kw):
        self.calls += 1
        b = self.behaviours.pop(0)
        if isinstance(b, Exception):
            raise b
        return b


class FakeClient:
    def __init__(self, behaviours):
        self.models = FakeModels(behaviours)


def _run(monkeypatch, behaviours, **patch):
    client = FakeClient(behaviours)
    monkeypatch.setattr(gemini, "_client", lambda key: client)
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    for k, v in patch.items():
        monkeypatch.setattr(gemini, k, v)
    return client


def test_timeout_is_retried_then_succeeds(monkeypatch):
    c = _run(monkeypatch, [TimeoutError("read timed out"), Resp()])
    assert gemini.generate_json({"api_key": "k", "model": "m"}, "s", "u", dict) == {"ok": True}
    assert c.models.calls == 2


def test_persistent_timeout_fails_not_hangs(monkeypatch):
    c = _run(monkeypatch, [TimeoutError("t")] * 5)
    with pytest.raises(ProviderError) as e:
        gemini.generate_json({"api_key": "k", "model": "m"}, "s", "u", dict)
    assert e.value.kind == "unavailable" and c.models.calls == 5


def test_deadline_stops_retries_early(monkeypatch):
    c = _run(monkeypatch, [TimeoutError("t")] * 5, DEADLINE_S=1)
    with pytest.raises(ProviderError):
        gemini.generate_json({"api_key": "k", "model": "m"}, "s", "u", dict)
    assert c.models.calls == 1


def test_request_timeout_is_configured():
    assert 0 < gemini.REQUEST_TIMEOUT_MS <= 180_000


def _api_error(violation_id):
    from google.genai import errors
    body = {"error": {"code": 429, "message": "You exceeded your current quota", "status": "RESOURCE_EXHAUSTED",
                      "details": [
                          {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                              {"quotaId": violation_id, "quotaValue": "20",
                               "quotaDimensions": {"model": "gemini-3.8-flash"}}]},
                          {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "28355s"}]}}
    return errors.APIError(429, body)


def test_daily_quota_is_not_retried_and_explained(monkeypatch):
    c = _run(monkeypatch, [_api_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")] * 5)
    # дневной лимит: повторы бессмысленны, но _map_error вернёт quota_daily; retry только для rate_limit/unavailable
    with pytest.raises(ProviderError) as e:
        gemini.generate_json({"api_key": "k", "model": "m"}, "s", "u", dict)
    assert e.value.kind == "quota_daily" and c.models.calls == 1
    assert "gemini-3.8-flash" in e.value.message and "7 ч 52 мин" in e.value.message


def test_per_minute_quota_still_retried(monkeypatch):
    c = _run(monkeypatch, [_api_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"), Resp()])
    assert gemini.generate_json({"api_key": "k", "model": "m"}, "s", "u", dict) == {"ok": True}
    assert c.models.calls == 2
