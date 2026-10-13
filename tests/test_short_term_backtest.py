"""用合成價量檢查短線規則。不讀任何成交紀錄。"""
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_strategies.evaluate import evaluate
from stock_strategies.loader import get_strategy
from stock_strategies.short_term import (
    allowed_side_vs_ma,
    backtest_breakout_high,
    backtest_gap_open,
    backtest_prior_day_return,
    backtest_session_direction,
    backtest_session_extreme,
    backtest_stop_and_reverse,
    backtest_weekday_hold,
    index_id_for_cap_segment,
    intraday_vwap,
    passes_resonance,
    resolve_bracket,
    review_discipline,
    run_short_term_backtest,
)

ROOT = Path(__file__).resolve().parents[1]


def _daily(rows):
    return pd.DataFrame(rows)


def _minutes(day, prices):
    """prices: (open, high, low, close, volume)。"""
    start = pd.Timestamp(f"{day} 09:00")
    rows = []
    for i, (o, h, l, c, v) in enumerate(prices):
        rows.append({
            "datetime": start + pd.Timedelta(minutes=i),
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        })
    return pd.DataFrame(rows)


def test_bracket_exits_whichever_side_is_alone():
    target = resolve_bracket(100, "long", high=104, low=99, close=101, stop_pct=0.025, target_pct=0.035)
    assert target["exit"] == "target"
    assert target["ambiguous"] is False
    stopped = resolve_bracket(100, "long", high=101, low=97, close=99, stop_pct=0.025, target_pct=0.035)
    assert stopped["exit"] == "stop"
    assert stopped["return"] == pytest.approx(-0.025)
    both = resolve_bracket(100, "long", high=110, low=90, close=100, stop_pct=0.025, target_pct=0.035)
    assert both["exit"] == "stop"
    assert both["ambiguous"] is True
    assert both["reason"] == "same_bar_stop_first"
    held = resolve_bracket(100, "short", high=101, low=99, close=99.5, stop_pct=0.02, target_pct=0.03)
    assert held["exit"] == "close"
    assert held["return"] == pytest.approx(0.005)


def test_bull_monday_enters_friday_close_only_above_ma():
    dates = pd.bdate_range("2020-01-06", periods=260)
    close = [100.0] * len(dates)
    # 年線可用之後的第一個週五拉高，下週一收更高。
    fridays = [i for i, d in enumerate(dates) if d.dayofweek == 4 and i > 240]
    mondays = [i for i, d in enumerate(dates) if d.dayofweek == 0]
    fri = next(i for i in fridays if i + 1 < len(dates) and dates[i + 1].dayofweek == 0)
    mon = fri + 1
    assert dates[mon].dayofweek == 0
    close[fri] = 130.0
    close[mon] = 143.0
    later = next(i for i in fridays if i > mon and i + 1 < len(dates))
    close[later] = 80.0
    df = pd.DataFrame({
        "date": dates,
        "open": [1.0] * len(dates),  # 週一開盤故意不是進場價
        "high": close,
        "low": close,
        "close": close,
    })
    result = backtest_weekday_hold(df, ma_period=240)
    assert result["samples"] == 1
    trade = result["trades"][0]
    assert trade["entry_price"] == pytest.approx(130)
    assert trade["exit_price"] == pytest.approx(143)
    assert trade["return"] == pytest.approx(143 / 130 - 1)
    assert result["skipped_below_ma"] >= 1

    # 拿掉那個週一，下一根變成週二，這筆不做。
    holiday = df.drop(index=mon).reset_index(drop=True)
    skipped = backtest_weekday_hold(holiday, ma_period=240)
    assert skipped["samples"] == 0
    assert skipped["skipped_no_exit_session"] >= 1
    assert mondays  # 日曆裡確實有週一，避免測試資料退化


def test_monday_strategy_file_uses_the_same_rule():
    strategy = get_strategy("taiex-bull-monday")
    dates = pd.bdate_range("2020-01-06", periods=250)
    close = [100.0] * len(dates)
    fri = next(i for i, d in enumerate(dates) if d.dayofweek == 4 and i > 240)
    close[fri] = 120
    close[fri + 1] = 126
    df = pd.DataFrame({"date": dates, "open": close, "high": close, "low": close, "close": close})
    result = run_short_term_backtest(strategy, price_df=df)
    assert result["samples"] == 1
    assert result["trades"][0]["entry_price"] == pytest.approx(120)


def test_index_filter_direction_and_cap_segment():
    assert allowed_side_vs_ma(101, 100) == "long"
    assert allowed_side_vs_ma(99, 100) == "short"
    assert allowed_side_vs_ma(100, 100) == "flat"
    assert index_id_for_cap_segment("large") == "TAIEX"
    assert index_id_for_cap_segment("small_mid") == "TPEx"
    with pytest.raises(ValueError):
        index_id_for_cap_segment("unknown")
    assert run_short_term_backtest(get_strategy("index-ma20-trend"))["status"] == "filter_only"


def test_next_day_long_uses_prior_close_not_the_same_day():
    df = _daily([
        {"date": "2024-01-02", "open": 100, "high": 100, "low": 100, "close": 100},
        {"date": "2024-01-03", "open": 100, "high": 106, "low": 100, "close": 106},
        {"date": "2024-01-04", "open": 106, "high": 120, "low": 105, "close": 110},
    ])
    result = backtest_prior_day_return(df, min_return=0.05, side="long", stop_pct=0.025, target_pct=0.035)
    assert result["samples"] == 1
    assert result["trades"][0]["entry_date"] == pd.Timestamp("2024-01-04")
    assert result["trades"][0]["exit"] == "target"
    assert result["trades"][0]["entry_price"] == pytest.approx(106)


def test_breakout_gap_enters_at_open_and_flags_intrabar():
    rows = []
    for i in range(20):
        rows.append({"date": pd.Timestamp("2024-01-02") + pd.Timedelta(days=i),
                     "open": 10, "high": 10, "low": 9, "close": 10})
    # 跳空突破
    rows.append({"date": rows[-1]["date"] + pd.Timedelta(days=1),
                 "open": 11, "high": 12, "low": 10.9, "close": 11.2})
    # 盤中才突破。前高已被上一根的 12 抬高，這根高點要再超過它。
    rows.append({"date": rows[-1]["date"] + pd.Timedelta(days=1),
                 "open": 9, "high": 13, "low": 8, "close": 11})
    result = backtest_breakout_high(pd.DataFrame(rows), lookback=20, side="long",
                                    stop_pct=0.02, target_pct=0.025)
    assert result["samples"] == 2
    assert result["trades"][0]["path"] == "gap_open"
    assert result["trades"][0]["entry_price"] == pytest.approx(11)
    assert result["trades"][0]["exit"] == "target"
    assert result["trades"][1]["path"] == "intrabar"
    assert result["trades"][1]["path_note"] == "intrabar_low_may_precede_entry"
    assert result["intrabar_entries"] == 1


def test_gap_down_short_from_the_open():
    df = _daily([
        {"date": "2024-01-02", "open": 100, "high": 100, "low": 100, "close": 100},
        {"date": "2024-01-03", "open": 99, "high": 100, "low": 98, "close": 99},
        {"date": "2024-01-04", "open": 97, "high": 98.5, "low": 94, "close": 96},
    ])
    result = backtest_gap_open(
        df, direction="down", gap_return_at_most=-0.02, side="short", stop_pct=0.02, target_pct=0.03,
    )
    assert result["samples"] == 1
    trade = result["trades"][0]
    assert trade["entry_price"] == pytest.approx(97)
    assert trade["exit"] == "target"
    assert trade["return"] == pytest.approx(0.03)
    wired = run_short_term_backtest(get_strategy("gap-down-short"), price_df=df)
    assert wired["samples"] == 1


def test_dip_and_down_day_require_minutes_and_then_bracket():
    assert run_short_term_backtest(get_strategy("dip-near-session-low"))["status"] == "requires_intraday_data"
    assert run_short_term_backtest(get_strategy("down-day-short"))["status"] == "requires_intraday_data"

    dip = _minutes("2024-06-03", [
        (100, 100, 100, 100, 10),
        (100, 110, 100, 110, 10),
        (110, 110, 101, 101, 10),  # (101-100)/(110-100) = 0.1，進在 101
        (101, 104, 101, 103, 10),
    ])
    dipped = backtest_session_extreme(
        dip, zone="near_low", range_fraction=0.2, side="long", stop_pct=0.01, target_pct=0.02,
    )
    assert dipped["samples"] == 1
    assert dipped["trades"][0]["entry_price"] == pytest.approx(101)
    assert dipped["trades"][0]["exit"] == "target"

    down = _minutes("2024-06-04", [
        (99, 99.5, 98.5, 99, 10),
        (99, 99.5, 96, 96.2, 10),
    ])
    shorted = backtest_session_direction(
        down, when="trading_below_prior_close", side="short",
        stop_pct=0.018, target_pct=0.025, prior_close=100,
    )
    assert shorted["samples"] == 1
    assert shorted["trades"][0]["entry_price"] == pytest.approx(99)
    assert shorted["trades"][0]["exit"] == "target"


def test_stop_and_reverse_flips_at_stop_and_holds_to_close():
    bare = run_short_term_backtest(get_strategy("stop-and-reverse"), price_df=pd.DataFrame())
    assert bare["status"] == "requires_intraday_data"
    assert bare["samples"] == 0

    bars = _minutes("2024-06-05", [
        (100, 100, 100, 100, 10),
        (100, 110, 100, 110, 10),
        (110, 110, 108, 109, 10),  # 上緣，追多 109
        (109, 109, 107, 107.5, 10),  # 1% 停損 107.91，這根碰到
        (107.5, 108, 105, 105, 10),
    ])
    result = backtest_stop_and_reverse(bars, stop_pcts=[0.01, 0.015, 0.02, 0.03])
    assert set(result["by_stop"]) == {"0.01", "0.015", "0.02", "0.03"}
    trade = result["by_stop"]["0.01"]["trades"][0]
    entry = 109
    stop_px = entry * 0.99
    exit_px = 105
    assert trade["entry_price"] == pytest.approx(entry)
    assert trade["stop_price"] == pytest.approx(stop_px)
    assert trade["exit_price"] == pytest.approx(exit_px)
    assert trade["return"] == pytest.approx(((stop_px - entry) + (stop_px - exit_px)) / entry)
    # 3% 停損價 105.73，這段最低 105 仍會碰到；改成最低只到 108，則 1% 會翻、較寬的不一定。
    # 低點 107 會碰到 1%（107.91），碰不到 3%（105.73）。
    quiet = _minutes("2024-06-06", [
        (100, 100, 100, 100, 5),
        (100, 110, 100, 110, 5),
        (110, 110, 108, 109, 5),
        (109, 109, 107, 108, 5),
    ])
    wide = backtest_stop_and_reverse(quiet, stop_pcts=[0.01, 0.03])
    assert wide["by_stop"]["0.01"]["samples"] == 1
    assert wide["by_stop"]["0.03"]["samples"] == 0


def test_resonance_needs_all_three_and_vwap_is_causal_volume():
    assert passes_resonance(
        side="long", index_return=0.01, stock_close=110, stock_ma20=100,
        entry=100.2, vwap=100, max_vwap_distance=0.005,
    )["pass"]
    near = passes_resonance(
        side="long", index_return=0.01, stock_close=110, stock_ma20=100,
        entry=100.5, vwap=100, max_vwap_distance=0.005,
    )
    assert near["entry_near_vwap"] is True
    assert passes_resonance(
        side="long", index_return=0.01, stock_close=110, stock_ma20=100,
        entry=101, vwap=100, max_vwap_distance=0.005,
    )["pass"] is False
    assert passes_resonance(
        side="short", index_return=-0.01, stock_close=90, stock_ma20=100,
        entry=100, vwap=100,
    )["pass"]
    assert passes_resonance(
        side="long", index_return=0, stock_close=110, stock_ma20=100, entry=100, vwap=100,
    )["index_same_direction"] is False

    bars = _minutes("2024-06-07", [
        (10, 10, 10, 10, 100),
        (12, 12, 12, 12, 100),
    ])
    vwap = intraday_vwap(bars)
    # 典型價就是收盤。第一根 10；第二根 (10*100 + 12*100) / 200 = 11。
    assert vwap.iloc[0] == pytest.approx(10)
    assert vwap.iloc[1] == pytest.approx(11)


def test_discipline_blocks_the_stated_cases():
    rules = json.loads((ROOT / "strategies" / "short-term-discipline.json").read_text())["risk"]

    early = review_discipline([
        {"time": "09:05", "stock_id": "2330", "action": "entry", "side": "long"},
        {"time": "09:15", "stock_id": "2330", "action": "entry", "side": "long"},
    ], rules)
    assert early["violations"][0]["rules"] == ["first_15_minutes"]
    assert early["accepted_entries"] == 1

    third = review_discipline([
        {"time": "09:20", "stock_id": "2330", "action": "entry", "side": "long"},
        {"time": "10:00", "stock_id": "2330", "action": "entry", "side": "long"},
        {"time": "11:00", "stock_id": "2330", "action": "entry", "side": "long"},
    ], rules)
    assert third["violations"][0]["rules"] == ["max_trades_per_stock"]
    assert third["accepted_entries"] == 2

    sixth = review_discipline(
        [{"time": "09:30", "stock_id": str(2300 + i), "action": "entry", "side": "long"} for i in range(6)],
        rules,
    )
    assert sixth["accepted_entries"] == 5
    assert "max_trades_per_day" in sixth["violations"][0]["rules"]

    locked = review_discipline([
        {"time": "09:20", "stock_id": "2330", "action": "entry", "side": "long"},
        {"time": "10:00", "stock_id": "2330", "action": "exit", "realized_twd": 4000},
        {"time": "10:30", "stock_id": "2317", "action": "entry", "side": "long"},
    ], rules)
    assert locked["violations"][0]["rules"] == ["daily_profit_lock"]

    given_back = review_discipline([
        {"time": "09:20", "stock_id": "2330", "action": "entry", "side": "long"},
        {"time": "10:00", "stock_id": "2330", "action": "exit", "realized_twd": 3000},
        {"time": "10:20", "stock_id": "2317", "action": "entry", "side": "long"},
        {"time": "10:40", "stock_id": "2317", "action": "exit", "realized_twd": -1500},
        {"time": "11:00", "stock_id": "2303", "action": "entry", "side": "long"},
    ], rules)
    assert "daily_giveback" in given_back["violations"][0]["rules"]
    assert "daily_profit_lock" not in given_back["violations"][0]["rules"]

    rest = review_discipline([
        {"time": "09:20", "stock_id": "2330", "action": "entry", "side": "long"},
        {"time": "09:30", "stock_id": "2330", "action": "exit", "realized_twd": -100},
        {"time": "09:40", "stock_id": "2317", "action": "entry", "side": "long"},
        {"time": "09:50", "stock_id": "2317", "action": "exit", "realized_twd": -100},
        {"time": "10:00", "stock_id": "2303", "action": "entry", "side": "long"},
        {"time": "10:20", "stock_id": "2308", "action": "entry", "side": "long"},
    ], rules)
    assert [v["time"] for v in rest["violations"]] == ["10:00"]
    assert rest["violations"][0]["rules"] == ["rest_after_losses"]
    assert rest["accepted_entries"] == 3

    flip = review_discipline([
        {"time": "09:20", "stock_id": "2330", "action": "entry", "side": "long"},
        {"time": "09:40", "stock_id": "2330", "action": "stop_out", "side": "long", "realized_twd": -50},
        {"time": "10:00", "stock_id": "2330", "action": "entry", "side": "short", "strategy_id": "gap-down-short"},
        {"time": "10:10", "stock_id": "2330", "action": "entry", "side": "long", "strategy_id": "gap-down-short"},
    ], rules)
    assert len(flip["violations"]) == 1
    assert flip["violations"][0]["rules"] == ["reversal_after_stop"]
    assert flip["accepted_entries"] == 2

    exception = review_discipline([
        {"time": "09:20", "stock_id": "2317", "action": "entry", "side": "long"},
        {"time": "09:40", "stock_id": "2317", "action": "stop_out", "side": "long", "realized_twd": -50},
        {"time": "10:00", "stock_id": "2317", "action": "entry", "side": "short", "strategy_id": "stop-and-reverse"},
    ], rules)
    assert exception["violations"] == []
    assert exception["accepted_entries"] == 2


def test_evaluate_does_not_score_short_term_strategies(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("短線策略不該抓基本面或日 K")

    monkeypatch.setattr("stock_strategies.evaluate.get_fundamental", boom)
    monkeypatch.setattr("stock_strategies.evaluate.get_price_history", boom)
    result = evaluate("2330", "台積電", strategy=get_strategy("taiex-bull-monday"))
    assert result["action"] == "SKIP"
    assert result["setup_summary"]["signal"] == "weekday_hold"
    assert "signal_score" not in result
