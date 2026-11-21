import pytest

from app import analyzer, cache, jobs, rules
from tests.helpers import make_analysis


def _sections():
    return rules.state()["sections"]


def test_initial_version_and_sections():
    st = rules.state()
    assert st["version"] == "v1" and len(st["sections"]) >= 10
    titles = " ".join(s["title"] for s in st["sections"])
    for needed in ("Тон", "Эмпатия", "Вовлечённость", "Персонализация", "Follow-up", "Responsible Gambling",
                   "Операционные", "Бонусная", "Достоверность", "Эмоциональная", "Приоритеты"):
        assert needed in titles


def test_edit_creates_new_version_and_keeps_old():
    secs = _sections()
    secs[1]["body"] += "\nНовая строка правила."
    st = rules.save(secs, note="правка")
    assert st["version"] == "v2" and [v["version"] for v in st["versions"]] == [2, 1]
    assert (rules.VERSIONS_DIR / "v1.md").exists() and (rules.VERSIONS_DIR / "v2.md").exists()
    assert "Новая строка правила." in rules.ACTIVE.read_text(encoding="utf-8")


def test_saving_same_content_does_not_create_version():
    assert rules.save(_sections())["version"] == "v1"


def test_new_rules_are_used_by_next_prompt_without_restart():
    before = analyzer.system_prompt()
    secs = _sections()
    secs.append({"id": "", "title": "Мои правила", "body": "Уникальное_правило_123", "custom": True, "enabled": True})
    rules.save(secs)
    after = analyzer.system_prompt()
    assert "Уникальное_правило_123" not in before and "Уникальное_правило_123" in after
    # обязательные меры безопасности остаются всегда
    assert "Обязательные меры безопасности" in after


def test_custom_rule_can_be_disabled_any_block_deleted_builtin_not_disabled():
    secs = _sections()
    secs.append({"id": "", "title": "Мое", "body": "Текст_мой", "custom": True, "enabled": True})
    secs = rules.save(secs)["sections"]
    custom = next(s for s in secs if s["custom"])
    custom["enabled"] = False
    st = rules.save(secs)
    assert "Текст_мой" not in rules.prompt_text()
    # встроенное правило нельзя отключить: флаг игнорируется
    secs = st["sections"]
    secs[0]["enabled"] = False
    assert rules.save(secs)["sections"][0]["enabled"] is True
    # удалить можно любой блок, в том числе встроенный и пользовательский
    first_title = rules.state()["sections"][0]["title"]
    st = rules.save(rules.state()["sections"][1:])
    assert all(s["title"] != first_title for s in st["sections"])
    st = rules.save([s for s in st["sections"] if not s["custom"]])
    assert not any(s["custom"] for s in st["sections"])
    # пустой набор правил не сохраняется
    with pytest.raises(rules.RulesError):
        rules.save([])


def test_mandatory_safety_stays_even_if_all_rule_blocks_are_removed():
    secs = _sections()
    rules.save(secs[:1])
    sp = analyzer.system_prompt()
    assert "Обязательные меры безопасности" in sp and "самоисключение" in sp.lower()


def test_restore_brings_back_deleted_block():
    secs = _sections()
    removed = secs[3]["title"]
    rules.save(secs[:3] + secs[4:])
    assert all(s["title"] != removed for s in rules.state()["sections"])
    st = rules.restore(1)
    assert any(s["title"] == removed and not s["custom"] for s in st["sections"])


def test_validation_errors():
    secs = _sections()
    secs[0]["title"] = " "
    with pytest.raises(rules.RulesError):
        rules.save(secs)
    secs = _sections() + [{"id": "", "title": "Пустое", "body": "", "custom": True, "enabled": True}]
    with pytest.raises(rules.RulesError):
        rules.save(secs)


def test_restore_creates_new_version_with_old_content():
    secs = _sections()
    original = secs[1]["body"]
    secs[1]["body"] = "Изменённый текст."
    rules.save(secs)
    st = rules.restore(1)
    assert st["version"] == "v3"
    assert next(s for s in st["sections"] if s["id"] == secs[1]["id"])["body"] == original
    assert (rules.VERSIONS_DIR / "v2.md").exists()   # история не удаляется
    with pytest.raises(rules.RulesError):
        rules.restore(99)


def test_rules_change_does_not_trigger_analysis_and_marks_result_stale(fake):
    provider = fake(raw=None, fn=lambda cfg, s, u: make_analysis("D002"))
    from app import settings
    settings.save("gemini", "m1", "k", make_active=True)
    analyzer.analyze("D002")
    assert provider.calls == 1
    old = cache.get("D002")
    assert old["meta"]["rules_version"] == "v1"
    secs = _sections()
    secs[1]["body"] += " Ещё."
    rules.save(secs)
    assert provider.calls == 1                          # правка правил ничего не запускает
    assert cache.get("D002") == old                     # результат не пересчитан и не изменён
    f = cache.freshness(old, analyzer.current_signature("D002"))
    assert f["status"] == "stale" and f["reasons"][0]["label"] == "Правила изменены"
    # массовый анализ по умолчанию не трогает устаревшие результаты
    assert "D002" not in jobs.targets(settings.load())
    assert "D002" in jobs.targets(settings.load(), include_stale=True)
