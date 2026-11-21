"""Загрузка, проверка и подмена исходных данных (dialogs.json, players.csv).

Приложение работает только с файлами, которые загрузил пользователь: они сохраняются в data/current/.
Файлы в корне проекта не читаются и не изменяются.
"""
import csv
import hashlib
import io
import json
import threading
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURRENT_DIR = ROOT / "data" / "current"

DIALOG_KEYS = {"dialog_id", "player_id", "manager", "channel", "messages"}
MESSAGE_KEYS = {"from", "sent_at", "text"}
INT_FIELDS = {"deposits_30d_count", "deposits_prev_30d_count", "failed_deposits_7d", "bonuses_30d_count"}
FLOAT_FIELDS = {"deposits_30d_usd", "deposits_prev_30d_usd", "withdrawals_30d_usd",
                "pending_withdrawal_usd", "bonuses_30d_usd"}

_lock = threading.RLock()
_db: dict | None = None


class DataError(ValueError):
    pass


def message_id(dialog_id: str, index: int) -> str:
    """ID сообщения: D001#1 (нумерация с 1)."""
    return f"{dialog_id}#{index}"


# ---------- разбор ----------

def parse_players(text: str) -> dict:
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
        rows = list(reader)
    except csv.Error as e:
        raise DataError(f"players.csv: ошибка CSV: {e}")
    if not reader.fieldnames or "player_id" not in reader.fieldnames:
        raise DataError("players.csv: нет колонки player_id")
    players = {}
    for n, row in enumerate(rows, 2):
        out = {}
        for k, v in row.items():
            if k is None:
                continue
            v = (v or "").strip()
            try:
                out[k] = None if v == "" else int(v) if k in INT_FIELDS else float(v) if k in FLOAT_FIELDS else v
            except ValueError:
                raise DataError(f"players.csv, строка {n}: некорректное значение {k}={v!r}")
        if not out.get("player_id"):
            raise DataError(f"players.csv, строка {n}: пустой player_id")
        if out["player_id"] in players:
            raise DataError(f"players.csv: повторяющийся player_id {out['player_id']}")
        players[out["player_id"]] = out
    if not players:
        raise DataError("players.csv: нет строк с игроками")
    return players


def parse_dialogs(text: str) -> dict:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise DataError(f"dialogs.json: некорректный JSON ({e.msg}, строка {e.lineno})")
    items = raw.get("dialogs") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise DataError("dialogs.json: нужен непустой список dialogs")
    dialogs, order, latest = {}, [], None
    for d in items:
        if not isinstance(d, dict) or DIALOG_KEYS - d.keys():
            raise DataError(f"dialogs.json: у диалога нет полей {sorted(DIALOG_KEYS - set(d or {}))}")
        did = d["dialog_id"]
        if did in dialogs:
            raise DataError(f"dialogs.json: повторяющийся dialog_id {did}")
        if not isinstance(d["messages"], list) or not d["messages"]:
            raise DataError(f"{did}: нет сообщений")
        msgs = []
        for i, m in enumerate(d["messages"], 1):
            if not isinstance(m, dict) or MESSAGE_KEYS - m.keys():
                raise DataError(f"{did}#{i}: неполное сообщение")
            if m["from"] not in ("player", "manager"):
                raise DataError(f"{did}#{i}: неизвестный автор {m['from']!r}")
            try:
                t = datetime.fromisoformat(m["sent_at"])
            except (TypeError, ValueError):
                raise DataError(f"{did}#{i}: некорректная дата {m['sent_at']!r}")
            latest = t if latest is None or t > latest else latest
            msgs.append({"id": message_id(did, i), "index": i, **m})
        dialogs[did] = {**d, "messages": msgs}
        order.append(did)
    as_of = raw.get("as_of") if isinstance(raw, dict) else None
    return {"as_of": as_of or latest.date().isoformat(), "dialogs": dialogs, "order": order}


def _build(dialogs_text: str, players_text: str) -> dict:
    db = parse_dialogs(dialogs_text)
    db["players"] = parse_players(players_text)
    missing = sorted({d["player_id"] for d in db["dialogs"].values()} - db["players"].keys())
    if missing:
        raise DataError(f"Игроки из диалогов не найдены в players.csv: {', '.join(missing[:5])}"
                        + (" ..." if len(missing) > 5 else ""))
    return db


# ---------- текущий набор данных (только загруженные пользователем файлы) ----------

EMPTY = {"as_of": None, "dialogs": {}, "order": [], "players": {}}


def _texts() -> tuple[str | None, str | None]:
    """Тексты загруженных файлов или None, если файл ещё не загружен."""
    def read(name):
        p = CURRENT_DIR / name
        return p.read_text(encoding="utf-8") if p.exists() else None
    return read("dialogs.json"), read("players.csv")


def load_all() -> dict:
    """{'as_of', 'dialogs': {id: dialog}, 'order': [ids], 'players': {id: player}}. Пусто, пока файлы не загружены."""
    global _db
    with _lock:
        if _db is None:
            d, p = _texts()
            _db = _build(d, p) if d is not None and p is not None else dict(EMPTY)
        return _db


def invalidate() -> None:
    global _db
    with _lock:
        _db = None


def check(dialogs_text: str | None, players_text: str | None) -> dict:
    """Проверка без сохранения. Отсутствующий файл берётся из уже загруженного набора, нужны оба файла."""
    cur_d, cur_p = _texts()
    report = {"dialogs": None, "players": None, "ok": False, "error": None, "missing": []}
    d_text, p_text = dialogs_text or cur_d, players_text or cur_p
    if dialogs_text is not None:
        try:
            report["dialogs"] = {"ok": True, "count": len(parse_dialogs(dialogs_text)["dialogs"])}
        except DataError as e:
            report["dialogs"] = {"ok": False, "error": str(e)}
    if players_text is not None:
        try:
            report["players"] = {"ok": True, "count": len(parse_players(players_text))}
        except DataError as e:
            report["players"] = {"ok": False, "error": str(e)}
    if any(r and not r["ok"] for r in (report["dialogs"], report["players"])):
        return report
    report["missing"] = [k for k, t in (("dialogs", d_text), ("players", p_text)) if t is None]
    if report["missing"]:
        return report   # второй файл ещё не выбран
    try:
        db = _build(d_text, p_text)
        report["ok"] = True
        report["total"] = {"dialogs": len(db["dialogs"]), "players": len(db["players"])}
    except DataError as e:
        report["error"] = str(e)
    return report


def apply(dialogs_text: str | None, players_text: str | None) -> dict:
    """Сохраняет загруженные файлы как рабочий набор. Прежние файлы результатов не затрагиваются."""
    report = check(dialogs_text, players_text)
    if not report["ok"]:
        return report
    with _lock:
        CURRENT_DIR.mkdir(parents=True, exist_ok=True)
        if dialogs_text is not None:
            (CURRENT_DIR / "dialogs.json").write_text(dialogs_text, encoding="utf-8")
        if players_text is not None:
            (CURRENT_DIR / "players.csv").write_text(players_text, encoding="utf-8")
        invalidate()
    return report


def info() -> dict:
    db = load_all()
    return {"dialogs": len(db["dialogs"]), "players": len(db["players"]), "as_of": db["as_of"]}


# ---------- вспомогательные ----------

def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def as_of_date() -> date:
    return date.fromisoformat(load_all()["as_of"][:10])


def dialog_hash(dialog: dict, player: dict) -> str:
    """Хэш содержимого диалога и данных игрока (для проверки актуальности результата)."""
    payload = {
        "as_of": load_all()["as_of"],
        "dialog": {
            "id": dialog["dialog_id"], "manager": dialog["manager"], "channel": dialog["channel"],
            "messages": [[m["from"], m["sent_at"], m["text"]] for m in dialog["messages"]],
        },
        "player": player,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
