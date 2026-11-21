import shutil
from pathlib import Path

import pytest

from app import analyzer, cache, data, jobs, main, rules, settings, snapshots

REAL_RULES = Path(__file__).resolve().parent.parent / "rules" / "evaluation_rules.md"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _blocked(*a, **k):
    raise AssertionError("Тест попытался обратиться к реальному AI-провайдеру")


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Изолирует кэш, настройки, правила и данные; запрещает реальные запросы к AI."""
    monkeypatch.setattr(cache, "CACHE_PATH", tmp_path / "cache" / "analysis.json")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "config" / "settings.json")
    rdir = tmp_path / "rules"
    rdir.mkdir()
    shutil.copyfile(REAL_RULES, rdir / "evaluation_rules.md")
    monkeypatch.setattr(rules, "RULES_DIR", rdir)
    monkeypatch.setattr(rules, "ACTIVE", rdir / "evaluation_rules.md")
    monkeypatch.setattr(rules, "VERSIONS_DIR", rdir / "versions")
    monkeypatch.setattr(rules, "INDEX", rdir / "versions.json")
    monkeypatch.setattr(snapshots, "RESULTS_DIR", tmp_path / "results")
    cur = tmp_path / "data" / "current"
    cur.mkdir(parents=True)
    for name in ("dialogs.json", "players.csv"):   # как будто пользователь загрузил эти файлы
        shutil.copyfile(FIXTURES / name, cur / name)
    monkeypatch.setattr(data, "CURRENT_DIR", cur)
    data.invalidate()
    monkeypatch.setattr(jobs, "_current", None)
    monkeypatch.setattr(analyzer, "get_provider", _blocked)
    monkeypatch.setattr(main, "get_provider", _blocked)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield
    data.invalidate()


class FakeProvider:
    """Подставной провайдер: возвращает заранее заданный ответ."""

    def __init__(self, raw=None, fn=None):
        self.raw, self.fn, self.calls = raw, fn, 0
        self.prompts = []

    def generate_json(self, cfg, system, user, schema):
        self.calls += 1
        self.prompts.append((system, user))
        if self.fn:
            return self.fn(cfg, system, user)
        return self.raw

    def test_connection(self, cfg):
        return cfg["model"]

    def list_models(self, cfg):
        return ["m-a", "m-b"]


@pytest.fixture
def fake(monkeypatch):
    """Подключает подставной провайдер к анализатору и API."""
    def install(raw=None, fn=None):
        p = FakeProvider(raw, fn)
        monkeypatch.setattr(analyzer, "get_provider", lambda name: p)
        monkeypatch.setattr(main, "get_provider", lambda name: p)
        return p
    return install


@pytest.fixture
def empty_data():
    """Состояние «файлы ещё не загружены»."""
    for name in ("dialogs.json", "players.csv"):
        (data.CURRENT_DIR / name).unlink()
    data.invalidate()
