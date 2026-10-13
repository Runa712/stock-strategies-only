"""短線策略 JSON 必須過 SCHEMA，舊策略的驗證結果不新增欄位。"""
import json
from pathlib import Path

import pytest

from stock_strategies.loader import StrategyError, validate_strategy
from stock_strategies.short_term_schema import SIGNAL_TYPES

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ROOT / "strategies"
SCHEMA = (STRATEGIES / "SCHEMA.md").read_text(encoding="utf-8")

NEW_IDS = [
    "taiex-bull-monday",
    "index-ma20-trend",
    "next-day-long-after-surge",
    "breakout-20d-high",
    "dip-near-session-low",
    "down-day-short",
    "gap-down-short",
    "stop-and-reverse",
    "resonance-filters",
    "short-term-discipline",
]

BRACKET = {
    "next-day-long-after-surge": (0.025, 0.035),
    "breakout-20d-high": (0.02, 0.025),
    "dip-near-session-low": (0.01, 0.02),
    "down-day-short": (0.018, 0.025),
    "gap-down-short": (0.02, 0.03),
}

BANNED = ("trades.csv", "trades_all", "tw_stock.db", "sqlite", "FINMIND_TOKEN", "勝率", "期望值")


def _load(sid: str) -> dict:
    return json.loads((STRATEGIES / f"{sid}.json").read_text(encoding="utf-8"))


def test_v1_strategies_do_not_grow_keys():
    for sid in ("default", "conservative"):
        raw = _load(sid)
        clean = validate_strategy(raw)
        assert set(clean) == {
            "id", "name", "description", "source", "created_at", "updated_at", "params",
        }
        assert "setup" not in clean
        assert "risk" not in clean
        for key, value in raw["params"].items():
            assert clean["params"][key] == value


def test_schema_documents_every_signal_type():
    for kind in SIGNAL_TYPES:
        assert kind in SCHEMA


@pytest.mark.parametrize("sid", NEW_IDS)
def test_new_strategy_validates(sid):
    raw = _load(sid)
    assert raw["id"] == sid
    clean = validate_strategy(raw)
    assert clean["setup"]["family"] == "short_term"
    assert clean["setup"]["signal"]["type"] in SIGNAL_TYPES
    assert clean["params"]["fundamental_pass_required"] is False
    assert clean["params"]["hold_days"] == 1
    blob = json.dumps(raw, ensure_ascii=False)
    for word in BANNED:
        assert word not in blob


def test_bracket_levels_match_the_rules():
    for sid, (stop, target) in BRACKET.items():
        clean = validate_strategy(_load(sid))
        exit_rule = clean["setup"]["exit"]
        assert exit_rule["mode"] == "bracket"
        assert exit_rule["same_bar"] == "stop_first"
        assert exit_rule["stop_loss"] == pytest.approx(stop)
        assert exit_rule["target_return"] == pytest.approx(target)


def test_monday_rule_is_friday_close_above_annual_ma():
    setup = validate_strategy(_load("taiex-bull-monday"))["setup"]
    assert setup["signal"]["entry_weekday"] == "friday"
    assert setup["signal"]["entry_price"] == "close"
    assert setup["signal"]["exit_weekday"] == "monday"
    assert setup["filters"][0]["ma_period"] == 240
    assert setup["filters"][0]["instrument"] == "TAIEX"
    assert "週一開盤" in _load("taiex-bull-monday")["description"]


def test_index_filter_uses_taiex_and_tpex():
    signal = validate_strategy(_load("index-ma20-trend"))["setup"]["signal"]
    assert signal["ma_period"] == 20
    assert {row["cap_segment"]: row["index_id"] for row in signal["benchmarks"]} == {
        "large": "TAIEX",
        "small_mid": "TPEx",
    }


def test_stop_and_reverse_lists_four_stops_and_needs_minutes():
    setup = validate_strategy(_load("stop-and-reverse"))["setup"]
    assert setup["data_requirement"] == "intraday_minute"
    assert setup["signal"]["stop_pcts"] == pytest.approx([0.01, 0.015, 0.02, 0.03])
    assert setup["risk_profile"] == "short-term-discipline"


def test_intraday_setups_are_marked():
    dip = validate_strategy(_load("dip-near-session-low"))["setup"]
    down = validate_strategy(_load("down-day-short"))["setup"]
    assert dip["data_requirement"] == "intraday"
    assert dip["signal"]["range_fraction"] == pytest.approx(0.2)
    assert down["data_requirement"] == "intraday"
    assert down["side"] == "short"


def test_discipline_thresholds():
    risk = validate_strategy(_load("short-term-discipline"))["risk"]
    assert risk["no_entry_first_minutes"] == 15
    assert risk["max_trades_per_stock_per_day"] == 2
    assert risk["no_reversal_after_stop"] is True
    assert risk["reversal_exception_strategy_ids"] == ["stop-and-reverse"]
    assert risk["daily_profit_lock_twd"] == 4000
    assert risk["daily_giveback_of_peak"] == pytest.approx(0.5)
    assert risk["consecutive_losses_for_rest"] == 2
    assert risk["rest_minutes"] == 30
    assert risk["max_trades_per_day"] == 5


def test_resonance_has_three_checks():
    checks = validate_strategy(_load("resonance-filters"))["setup"]["checks"]
    assert [c["id"] for c in checks] == [
        "index_same_direction",
        "stock_ma20_same_direction",
        "entry_near_vwap",
    ]
    vwap = next(c for c in checks if c["id"] == "entry_near_vwap")
    assert vwap["max_distance"] == pytest.approx(0.005)


def test_unknown_setup_field_is_rejected():
    raw = _load("gap-down-short")
    raw["setup"]["secret_stat"] = 1
    with pytest.raises(StrategyError):
        validate_strategy(raw)


def test_engine_source_has_no_private_paths():
    blob = (ROOT / "stock_strategies" / "short_term.py").read_text(encoding="utf-8")
    blob += (ROOT / "stock_strategies" / "short_term_schema.py").read_text(encoding="utf-8")
    for word in ("trades.csv", "trades_all", "tw_stock.db", "sqlite", "FINMIND_TOKEN"):
        assert word not in blob
