"""Регламенты анализа: хранение, редактирование и версионирование.

Активные правила лежат в rules/evaluation_rules.md, каждая версия сохраняется в rules/versions/vN.md.
Backend читает актуальные правила при каждом анализе, перезапуск не нужен.
"""
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .data import ROOT

RULES_DIR = ROOT / "rules"
ACTIVE = RULES_DIR / "evaluation_rules.md"
VERSIONS_DIR = RULES_DIR / "versions"
INDEX = RULES_DIR / "versions.json"
TITLE = "# Правила оценки переписок VIP-менеджеров"
MAX_TOTAL = 60_000
MAX_TITLE = 80

_lock = threading.RLock()
_HEAD_RE = re.compile(r"^##\s+(.+?)\s*$")
_META_RE = re.compile(r"^<!--\s*(.*?)\s*-->\s*$")
_BUILTIN_ID = re.compile(r"^b\d{2}$")


class RulesError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def parse(text: str) -> list[dict]:
    """Markdown -> секции [{id, title, body, custom, enabled}]."""
    sections, cur = [], None
    for line in text.splitlines():
        m = _HEAD_RE.match(line)
        if m:
            cur = {"title": m.group(1), "lines": [], "meta": {}, "head": True}
            sections.append(cur)
            continue
        if cur is None:
            continue
        mm = _META_RE.match(line)
        if mm and not cur["lines"] and "=" in mm.group(1):
            for kv in mm.group(1).split():
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    cur["meta"][k] = v
            continue
        cur["lines"].append(line)
    out = []
    for i, s in enumerate(sections, 1):
        meta = s["meta"]
        out.append({
            "id": meta.get("id") or f"b{i:02d}",
            "title": s["title"],
            "body": "\n".join(s["lines"]).strip("\n"),
            "custom": meta.get("custom") == "1",
            "enabled": meta.get("enabled", "1") != "0",
        })
    return out


def serialize(version: int, sections: list[dict]) -> str:
    parts = [TITLE, f"RULES_VERSION: v{version}", ""]
    for s in sections:
        parts.append(f"## {s['title']}")
        parts.append(f"<!-- id={s['id']} custom={'1' if s['custom'] else '0'} enabled={'1' if s['enabled'] else '0'} -->")
        parts.append(s["body"].strip("\n"))
        parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"


def _read_index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def ensure_initialized() -> None:
    """Первый запуск: текущий файл правил становится версией v1."""
    with _lock:
        if INDEX.exists():
            return
        sections = parse(ACTIVE.read_text(encoding="utf-8"))
        text = serialize(1, sections)
        _write(VERSIONS_DIR / "v1.md", text)
        _write(ACTIVE, text)
        _write(INDEX, json.dumps({"current": 1, "versions": [
            {"version": 1, "saved_at": _now(), "note": "Исходные правила"}]}, ensure_ascii=False, indent=1))


def current_number() -> int:
    ensure_initialized()
    return int(_read_index()["current"])


def current_version() -> str:
    """Идентификатор версии, например 'v3'. Читается с диска при каждом вызове."""
    return f"v{current_number()}"


def active_sections() -> list[dict]:
    ensure_initialized()
    return parse(ACTIVE.read_text(encoding="utf-8"))


def prompt_text() -> str:
    """Текст правил для промпта: только включённые секции, без служебных меток."""
    out = []
    for s in active_sections():
        if s["enabled"]:
            out.append(f"## {s['title']}\n{s['body'].strip()}")
    return "# Правила оценки\n\n" + "\n\n".join(out)


def state() -> dict:
    ensure_initialized()
    idx = _read_index()
    return {
        "version": f"v{idx['current']}",
        "saved_at": next((v["saved_at"] for v in idx["versions"] if v["version"] == idx["current"]), None),
        "sections": active_sections(),
        "versions": sorted(idx["versions"], key=lambda v: -v["version"]),
    }


def _validate(new: list[dict]) -> list[dict]:
    if not new:
        raise RulesError("Список правил пуст")
    seen, out = set(), []
    for s in new:
        title = (s.get("title") or "").strip()
        body = (s.get("body") or "").strip()
        sid = (s.get("id") or "").strip()
        if not title or len(title) > MAX_TITLE:
            raise RulesError(f"Название правила должно быть от 1 до {MAX_TITLE} символов")
        if _BUILTIN_ID.match(sid):
            custom, enabled = False, True        # встроенные блоки нельзя отключить (удалить можно)
        else:
            custom, enabled = True, bool(s.get("enabled", True))
            sid = sid if sid.startswith("c-") and sid not in seen else f"c-{uuid.uuid4().hex[:8]}"
        if enabled and not body:
            raise RulesError(f"Правило «{title}» не должно быть пустым")
        if sid in seen:
            raise RulesError("Повторяющийся идентификатор правила")
        seen.add(sid)
        out.append({"id": sid, "title": title, "body": body, "custom": custom, "enabled": enabled})
    if sum(len(s["body"]) + len(s["title"]) for s in out) > MAX_TOTAL:
        raise RulesError("Слишком большой объём правил")
    return out


def save(sections: list[dict], note: str | None = None) -> dict:
    """Сохраняет правила. Если содержимое изменилось, создаёт новую версию. Анализ не запускается."""
    with _lock:
        ensure_initialized()
        old = active_sections()
        new = _validate(sections)
        if serialize(0, new) == serialize(0, old):
            return state()
        idx = _read_index()
        n = max(v["version"] for v in idx["versions"]) + 1
        text = serialize(n, new)
        _write(VERSIONS_DIR / f"v{n}.md", text)
        _write(ACTIVE, text)
        idx["versions"].append({"version": n, "saved_at": _now(), "note": note or ""})
        idx["current"] = n
        _write(INDEX, json.dumps(idx, ensure_ascii=False, indent=1))
        return state()


def restore(version: int) -> dict:
    """Восстановление: содержимое старой версии сохраняется как новая версия, история не теряется."""
    with _lock:
        ensure_initialized()
        path = VERSIONS_DIR / f"v{int(version)}.md"
        if not path.exists():
            raise RulesError(f"Версия v{version} не найдена")
        old_sections = parse(path.read_text(encoding="utf-8"))
        return save(old_sections, note=f"Восстановлено из v{int(version)}")
