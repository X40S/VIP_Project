from app.data import load_all
from app.metrics import dialog_metrics, player_metrics


def _get(did):
    db = load_all()
    d = db["dialogs"][did]
    return d, db["players"][d["player_id"]]


def test_schema_loaded():
    db = load_all()
    assert len(db["dialogs"]) == 40 and len(db["players"]) == 50
    assert db["dialogs"]["D001"]["messages"][0]["id"] == "D001#1"


def test_d001_response_time_and_flags():
    d, p = _get("D001")
    dm = dialog_metrics(d)
    assert dm["first_response_minutes"] == 95.0  # 21:40 -> 23:15
    assert "PAYMENT_FAILURES" in player_metrics(p)["flags"]


def test_d003_frequency_spike():
    _, p = _get("D003")
    assert "DEPOSIT_FREQUENCY_SPIKE" in player_metrics(p)["flags"]


def test_d013_deposit_drop():
    _, p = _get("D013")
    pm = player_metrics(p)
    assert pm["deposit_usd_change_pct"] < -90 and "DEPOSIT_DROP" in pm["flags"]


def test_self_excluded_and_limit_flags():
    _, p11 = _get("D011")
    _, p04 = _get("D004")
    assert "SELF_EXCLUDED" in player_metrics(p11)["flags"]
    assert "LIMIT_SET" in player_metrics(p04)["flags"]
    assert "SELF_EXCLUDED" not in player_metrics(p04)["flags"]


def test_unanswered_and_series():
    d16, _ = _get("D016")
    assert dialog_metrics(d16)["unanswered_player_messages"] == 2
    d20, _ = _get("D020")
    dm = dialog_metrics(d20)
    assert dm["manager_messages_in_row"] == 5 and dm["unanswered_player_messages"] == 1


def test_long_response_d012():
    d, _ = _get("D012")
    dm = dialog_metrics(d)
    # игрок написал в 10:12, менеджер ответил в 19:32
    assert dm["max_response_minutes"] == 560.0 and dm["long_responses"] == ["D012#3"]


def test_birthday_offset_d015():
    _, p = _get("D015")
    assert player_metrics(p)["birthday_offset_days"] is not None
