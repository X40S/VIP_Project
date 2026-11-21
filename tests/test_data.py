import json

from app import data
from app.data import load_all

DIALOGS = json.dumps({"dialogs": [{"dialog_id": "X1", "player_id": "PX", "manager": "Ann", "channel": "telegram",
                                   "messages": [{"from": "player", "sent_at": "2026-12-01T10:00:00", "text": "hi"},
                                                {"from": "manager", "sent_at": "2026-12-01T10:05:00", "text": "hello"}]}]})
PLAYERS = "player_id,manager,account_status\nPX,Ann,active\n"


def test_fixture_dataset_loads():
    db = load_all()
    assert len(db["dialogs"]) == 40 and len(db["players"]) == 50 and db["as_of"] == "2026-11-18"


def test_nothing_is_loaded_until_the_user_uploads_files(empty_data):
    """Файлы проекта в корне не подставляются: без загрузки набор пустой."""
    db = load_all()
    assert db["dialogs"] == {} and db["players"] == {} and db["order"] == [] and data.info()["dialogs"] == 0


def test_check_reports_counts_and_errors_without_saving():
    rep = data.check(DIALOGS, PLAYERS)
    assert rep["ok"] and rep["dialogs"]["count"] == 1 and rep["players"]["count"] == 1
    assert len(load_all()["dialogs"]) == 40               # проверка ничего не сохранила
    bad = data.check("{oops", None)
    assert not bad["ok"] and "некорректный JSON" in bad["dialogs"]["error"]
    assert not data.check(None, "name,age\nx,1\n")["players"]["ok"]
    mismatch = data.check(DIALOGS, "player_id\nPOTHER\n")
    assert not mismatch["ok"] and "PX" in mismatch["error"]


def test_both_files_are_required_when_nothing_is_loaded(empty_data):
    rep = data.check(DIALOGS, None)
    assert not rep["ok"] and rep["missing"] == ["players"] and rep["dialogs"]["ok"]
    assert not data.apply(DIALOGS, None)["ok"] and load_all()["dialogs"] == {}
    assert data.apply(DIALOGS, PLAYERS)["ok"] and list(load_all()["dialogs"]) == ["X1"]


def test_apply_replaces_dataset_and_missing_columns_do_not_break_metrics():
    assert data.apply(DIALOGS, PLAYERS)["ok"]
    db = load_all()
    assert list(db["dialogs"]) == ["X1"] and db["as_of"] == "2026-12-01"    # as_of берётся из переписки
    from app.metrics import full_metrics
    m = full_metrics(db["dialogs"]["X1"], db["players"]["PX"])
    assert m["player"]["activity_change_available"] is False and m["player"]["deposit_usd_change_pct"] is None
    assert data.info() == {"dialogs": 1, "players": 1, "as_of": "2026-12-01"}


def test_one_new_file_can_replace_just_that_file():
    data.apply(DIALOGS, PLAYERS)
    new_players = "player_id,manager\nPX,Bob\nPY,Bob\n"
    assert data.apply(None, new_players)["ok"] and len(load_all()["players"]) == 2 and list(load_all()["dialogs"]) == ["X1"]
