"""短線台股／加權規則的回測。

只使用呼叫端傳入的價量。不讀成交明細、帳戶或任何私有檔案。
日 K 看不出同一根裡面停損和停利誰先碰到；兩種都碰到時沿用
backtest.py 的做法，先記停損，並把該筆標成 ambiguous。
貼近低檔、跌日放空、停損反手沒有分 K 就不計算，避免用日 K 湊結果。
"""

from __future__ import annotations

import pandas as pd

from .short_term_schema import validate_risk

_WEEKDAY = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
}

# 大型股看加權，中小型看櫃買。回測資料由呼叫端提供，這裡只決定代號。
CAP_SEGMENT_INDEX = {"large": "TAIEX", "small_mid": "TPEx"}


def index_id_for_cap_segment(segment: str) -> str:
    key = (segment or "").lower()
    if key in {"large", "large_cap", "taiex"}:
        return CAP_SEGMENT_INDEX["large"]
    if key in {"small_mid", "small", "mid", "tpex", "otc"}:
        return CAP_SEGMENT_INDEX["small_mid"]
    raise ValueError(f"未知的股本區間: {segment}")


def allowed_side_vs_ma(close: float, ma: float) -> str:
    """月線之上只做多，之下只做空，貼齊或資料不足則不開新方向。"""
    if pd.isna(close) or pd.isna(ma):
        return "flat"
    if close > ma:
        return "long"
    if close < ma:
        return "short"
    return "flat"


def _prepare_daily(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    out = df.copy()
    if "date" not in out.columns:
        raise ValueError("日 K 需要 date 欄")
    for col in ("open", "high", "low", "close"):
        if col not in out.columns:
            raise ValueError(f"日 K 需要 {col} 欄")
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["date"] = pd.to_datetime(out["date"])
    out = out.dropna(subset=["open", "high", "low", "close"]).sort_values("date")
    return out.reset_index(drop=True)


def _prepare_minutes(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame()
    out = df.copy()
    if "datetime" not in out.columns:
        raise ValueError("分 K 需要 datetime 欄")
    for col in ("open", "high", "low", "close"):
        if col not in out.columns:
            raise ValueError(f"分 K 需要 {col} 欄")
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "volume" not in out.columns:
        out["volume"] = 1.0
    else:
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0)
    out["datetime"] = pd.to_datetime(out["datetime"])
    out = out.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime")
    out["session"] = out["datetime"].dt.strftime("%Y-%m-%d")
    return out.reset_index(drop=True)


def _summarize(trades: list[dict], *, status: str = "ok", extra: dict | None = None) -> dict:
    rets = [float(t["return"]) for t in trades]
    n = len(rets)
    wins = sum(1 for r in rets if r > 0)
    out = {
        "status": status,
        "samples": n,
        "wins": wins,
        "losses": n - wins,
        "winrate": round(wins / n, 3) if n else None,
        "avg_return": round(sum(rets) / n, 4) if n else None,
        "ambiguous": sum(1 for t in trades if t.get("ambiguous")),
        "trades": trades,
    }
    if extra:
        out.update(extra)
    return out


def _insufficient(note: str) -> dict:
    return {"status": "insufficient_data", "samples": 0, "wins": 0, "losses": 0,
            "winrate": None, "avg_return": None, "ambiguous": 0, "trades": [], "note": note}


def resolve_bracket(
    entry: float,
    side: str,
    high: float,
    low: float,
    close: float,
    stop_pct: float,
    target_pct: float,
) -> dict:
    """停損與停利一起掛。只碰到一個就出那個；都沒碰到用收盤。

    同一根高低點同時涵蓋停損與停利時，無法知道先後，記停損並標 ambiguous。
    """
    entry = float(entry)
    if entry <= 0:
        raise ValueError("entry 必須為正")
    if side == "long":
        stop_px = entry * (1 - stop_pct)
        target_px = entry * (1 + target_pct)
        hit_stop = low <= stop_px
        hit_target = high >= target_px
        close_ret = (close - entry) / entry
    elif side == "short":
        stop_px = entry * (1 + stop_pct)
        target_px = entry * (1 - target_pct)
        hit_stop = high >= stop_px
        hit_target = low <= target_px
        close_ret = (entry - close) / entry
    else:
        raise ValueError(f"未知方向: {side}")

    ambiguous = bool(hit_stop and hit_target)
    if hit_stop:
        return {
            "exit": "stop",
            "return": -float(stop_pct),
            "ambiguous": ambiguous,
            "reason": "same_bar_stop_first" if ambiguous else "stop",
            "stop_price": stop_px,
            "target_price": target_px,
        }
    if hit_target:
        return {
            "exit": "target",
            "return": float(target_pct),
            "ambiguous": False,
            "reason": "target",
            "stop_price": stop_px,
            "target_price": target_px,
        }
    return {
        "exit": "close",
        "return": float(close_ret),
        "ambiguous": False,
        "reason": "session_close",
        "stop_price": stop_px,
        "target_price": target_px,
        "exit_price": float(close),
    }


def backtest_weekday_hold(
    df: pd.DataFrame | None,
    *,
    ma_period: int = 240,
    entry_weekday: str = "friday",
    exit_weekday: str = "monday",
) -> dict:
    """收盤站上年線才進場。進場價是進場日收盤，出場價是下一個指定星期的收盤。

    下一根如果不是那個星期（例如連假），這筆不做。
    """
    px = _prepare_daily(df)
    if len(px) <= ma_period:
        return _insufficient("日 K 不足以計算均線")
    entry_wd = _WEEKDAY[entry_weekday]
    exit_wd = _WEEKDAY[exit_weekday]
    px = px.copy()
    px["ma"] = px["close"].rolling(ma_period).mean()
    px["wd"] = px["date"].dt.dayofweek
    trades = []
    skipped_below_ma = 0
    skipped_no_exit_session = 0
    for i in range(len(px) - 1):
        row = px.iloc[i]
        if int(row["wd"]) != entry_wd or pd.isna(row["ma"]):
            continue
        nxt = px.iloc[i + 1]
        if int(nxt["wd"]) != exit_wd:
            skipped_no_exit_session += 1
            continue
        if not row["close"] > row["ma"]:
            skipped_below_ma += 1
            continue
        trades.append({
            "entry_date": row["date"],
            "exit_date": nxt["date"],
            "entry_price": float(row["close"]),
            "exit_price": float(nxt["close"]),
            "return": float(nxt["close"] / row["close"] - 1),
            "side": "long",
            "ambiguous": False,
        })
    return _summarize(trades, extra={
        "skipped_below_ma": skipped_below_ma,
        "skipped_no_exit_session": skipped_no_exit_session,
        "data_requirement": "daily",
    })


def backtest_prior_day_return(
    df: pd.DataFrame | None,
    *,
    min_return: float,
    side: str,
    stop_pct: float,
    target_pct: float,
) -> dict:
    """前一日收盤漲幅達門檻，隔日開盤進場。漲幅用已收盤的那一天，不含當天。"""
    px = _prepare_daily(df)
    if len(px) < 3:
        return _insufficient("日 K 不足")
    day_ret = px["close"].pct_change()
    trades = []
    for i in range(2, len(px)):
        prior = day_ret.iloc[i - 1]
        if pd.isna(prior) or prior < min_return:
            continue
        row = px.iloc[i]
        if row["open"] <= 0:
            continue
        leg = resolve_bracket(
            row["open"], side, row["high"], row["low"], row["close"], stop_pct, target_pct
        )
        leg.update({
            "signal_date": px.iloc[i - 1]["date"],
            "entry_date": row["date"],
            "entry_price": float(row["open"]),
            "side": side,
        })
        trades.append(leg)
    return _summarize(trades, extra={"data_requirement": "daily"})


def backtest_breakout_high(
    df: pd.DataFrame | None,
    *,
    lookback: int,
    side: str,
    stop_pct: float,
    target_pct: float,
) -> dict:
    """突破前 lookback 日最高（不含當天）。

    開盤已在突破價之上，以開盤價進。盤中才突破，以突破價進，
    並註明當天低點可能發生在突破之前，日 K 無法排除。
    """
    px = _prepare_daily(df)
    if len(px) <= lookback:
        return _insufficient("日 K 不足以計算前高")
    prior_high = px["high"].shift(1).rolling(lookback).max()
    trades = []
    intrabar = 0
    for i in range(len(px)):
        level = prior_high.iloc[i]
        if pd.isna(level):
            continue
        row = px.iloc[i]
        if row["high"] <= level:
            continue
        if row["open"] > level:
            entry = float(row["open"])
            path = "gap_open"
        else:
            entry = float(level)
            path = "intrabar"
            intrabar += 1
        leg = resolve_bracket(entry, side, row["high"], row["low"], row["close"], stop_pct, target_pct)
        leg.update({
            "entry_date": row["date"],
            "entry_price": entry,
            "side": side,
            "path": path,
        })
        if path == "intrabar":
            leg["path_note"] = "intrabar_low_may_precede_entry"
        trades.append(leg)
    return _summarize(trades, extra={"intrabar_entries": intrabar, "data_requirement": "daily"})


def backtest_gap_open(
    df: pd.DataFrame | None,
    *,
    direction: str,
    gap_return_at_most: float | None = None,
    gap_return_at_least: float | None = None,
    side: str,
    stop_pct: float,
    target_pct: float,
) -> dict:
    """開盤相對前收的跳空。開低：gap <= 門檻；開高：gap >= 門檻。進場價是開盤。"""
    px = _prepare_daily(df)
    if len(px) < 2:
        return _insufficient("日 K 不足")
    trades = []
    for i in range(1, len(px)):
        prev_close = float(px.iloc[i - 1]["close"])
        row = px.iloc[i]
        if prev_close <= 0 or row["open"] <= 0:
            continue
        gap = float(row["open"] / prev_close - 1)
        if direction == "down":
            if gap_return_at_most is None or gap > gap_return_at_most:
                continue
        elif direction == "up":
            if gap_return_at_least is None or gap < gap_return_at_least:
                continue
        else:
            raise ValueError(direction)
        leg = resolve_bracket(
            row["open"], side, row["high"], row["low"], row["close"], stop_pct, target_pct
        )
        leg.update({
            "entry_date": row["date"],
            "entry_price": float(row["open"]),
            "gap_return": gap,
            "side": side,
        })
        trades.append(leg)
    return _summarize(trades, extra={"data_requirement": "daily"})


def _walk_bracket(bars: pd.DataFrame, start: int, entry: float, side: str,
                  stop_pct: float, target_pct: float) -> dict:
    """從 start 這根開始往後看。進場若在上一根收盤，start 應是下一根。"""
    if start >= len(bars):
        return {
            "exit": "close",
            "return": 0.0,
            "ambiguous": False,
            "reason": "entered_at_close",
            "exit_price": entry,
        }
    for j in range(start, len(bars)):
        bar = bars.iloc[j]
        judged = resolve_bracket(
            entry, side, float(bar["high"]), float(bar["low"]), float(bar["close"]),
            stop_pct, target_pct,
        )
        if judged["exit"] != "close":
            judged["exit_time"] = bar["datetime"]
            return judged
    last = float(bars.iloc[-1]["close"])
    if side == "long":
        ret = (last - entry) / entry
    else:
        ret = (entry - last) / entry
    return {
        "exit": "close",
        "return": float(ret),
        "ambiguous": False,
        "reason": "session_close",
        "exit_price": last,
    }


def _session_groups(minute_df: pd.DataFrame | None) -> list[pd.DataFrame]:
    bars = _prepare_minutes(minute_df)
    if bars.empty:
        return []
    return [g.reset_index(drop=True) for _, g in bars.groupby("session", sort=True)]


def backtest_session_extreme(
    minute_df: pd.DataFrame | None,
    *,
    zone: str,
    range_fraction: float,
    side: str,
    stop_pct: float,
    target_pct: float,
) -> dict:
    """用當時已走出的高低區間判斷低檔或高檔，不用收盤後才知道的全日區間。

    第一根只用來建立區間。之後收盤落在區間下緣（或上緣）才進場，
    停損停利從下一根開始算。
    """
    groups = _session_groups(minute_df)
    if not groups:
        return {
            "status": "requires_intraday_data",
            "data_requirement": "intraday",
            "samples": 0,
            "note": "低接需要盤中分 K。",
        }
    threshold = range_fraction if zone == "near_low" else 1 - range_fraction
    trades = []
    for bars in groups:
        run_high = None
        run_low = None
        entry_i = None
        entry = None
        for i in range(len(bars)):
            bar = bars.iloc[i]
            if run_high is None:
                run_high = float(bar["high"])
                run_low = float(bar["low"])
                continue
            span = run_high - run_low
            if span > 0:
                pos = (float(bar["close"]) - run_low) / span
                hit = pos <= threshold if zone == "near_low" else pos >= threshold
                if hit:
                    entry_i = i
                    entry = float(bar["close"])
                    break
            run_high = max(run_high, float(bar["high"]))
            run_low = min(run_low, float(bar["low"]))
        if entry_i is None:
            continue
        leg = _walk_bracket(bars, entry_i + 1, entry, side, stop_pct, target_pct)
        leg.update({
            "entry_time": bars.iloc[entry_i]["datetime"],
            "entry_price": entry,
            "side": side,
            "session": bars.iloc[0]["session"],
        })
        trades.append(leg)
    return _summarize(trades, extra={"data_requirement": "intraday"})


def backtest_session_direction(
    minute_df: pd.DataFrame | None,
    *,
    when: str,
    side: str,
    stop_pct: float,
    target_pct: float,
    prior_close: float | None = None,
) -> dict:
    """價格跌破前收才放空、站上前收才做多。前收來自上一節的收盤，不用當天收盤。"""
    groups = _session_groups(minute_df)
    if not groups:
        return {
            "status": "requires_intraday_data",
            "data_requirement": "intraday",
            "samples": 0,
            "note": "跌日放空需要盤中分 K。",
        }
    trades = []
    last_close = None if prior_close is None else float(prior_close)
    for bars in groups:
        if last_close is None or last_close <= 0:
            last_close = float(bars.iloc[-1]["close"])
            continue
        entry_i = None
        entry = None
        for i in range(len(bars)):
            price = float(bars.iloc[i]["close"])
            below = price < last_close
            if (when == "trading_below_prior_close" and below) or (
                when == "trading_above_prior_close" and not below and price > last_close
            ):
                entry_i = i
                entry = price
                break
        if entry_i is not None:
            leg = _walk_bracket(bars, entry_i + 1, entry, side, stop_pct, target_pct)
            leg.update({
                "entry_time": bars.iloc[entry_i]["datetime"],
                "entry_price": entry,
                "prior_close": last_close,
                "side": side,
                "session": bars.iloc[0]["session"],
            })
            trades.append(leg)
        last_close = float(bars.iloc[-1]["close"])
    return _summarize(trades, extra={"data_requirement": "intraday"})


def _chase_entry(bars: pd.DataFrame, fraction_from_high: float):
    """收盤落在當時已知區間的上緣才算追多。區間不含這一根，避免用收盤後的高低。"""
    threshold = 1 - fraction_from_high
    run_high = None
    run_low = None
    for i in range(len(bars)):
        bar = bars.iloc[i]
        if run_high is None:
            run_high = float(bar["high"])
            run_low = float(bar["low"])
            continue
        span = run_high - run_low
        if span > 0:
            pos = (float(bar["close"]) - run_low) / span
            if pos >= threshold:
                return i, float(bar["close"])
        run_high = max(run_high, float(bar["high"]))
        run_low = min(run_low, float(bar["low"]))
    return None


def backtest_stop_and_reverse(
    minute_df: pd.DataFrame | None,
    *,
    stop_pcts: list[float],
    fraction_from_high: float = 0.2,
) -> dict:
    """追多碰到停損後，在停損價翻空，持有到該節最後一筆收盤。

    沒有翻成空單的追多不計入樣本。每個停損幅度各自統計。
    """
    groups = _session_groups(minute_df)
    if not groups:
        return {
            "status": "requires_intraday_data",
            "data_requirement": "intraday_minute",
            "samples": 0,
            "by_stop": {},
            "note": "停損反手需要一分 K。未提供 minute_df 時不回測。",
        }
    by_stop = {}
    for stop_pct in stop_pcts:
        trades = []
        for bars in groups:
            found = _chase_entry(bars, fraction_from_high)
            if found is None:
                continue
            entry_i, entry = found
            stop_px = entry * (1 - float(stop_pct))
            flipped = None
            for j in range(entry_i + 1, len(bars)):
                if float(bars.iloc[j]["low"]) <= stop_px:
                    flipped = j
                    break
            if flipped is None:
                continue
            exit_px = float(bars.iloc[-1]["close"])
            combined = ((stop_px - entry) + (stop_px - exit_px)) / entry
            trades.append({
                "flipped": True,
                "entry_price": entry,
                "stop_price": stop_px,
                "exit_price": exit_px,
                "return": float(combined),
                "long_return": float((stop_px - entry) / entry),
                "short_return": float((stop_px - exit_px) / stop_px),
                "entry_time": bars.iloc[entry_i]["datetime"],
                "flip_time": bars.iloc[flipped]["datetime"],
                "session": bars.iloc[0]["session"],
                "ambiguous": False,
                "side": "short",
            })
        by_stop[format(float(stop_pct), "g")] = _summarize(trades)
    samples = sum(block["samples"] for block in by_stop.values())
    return {
        "status": "ok",
        "data_requirement": "intraday_minute",
        "samples": samples,
        "by_stop": by_stop,
    }


def intraday_vwap(minute_df: pd.DataFrame) -> pd.Series:
    """典型價乘量的累計 VWAP。呼叫端要自己停在進場那一根，不要用到未來的量。"""
    bars = _prepare_minutes(minute_df)
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    volume = bars["volume"].astype(float)
    denom = volume.cumsum().replace(0, pd.NA)
    return (typical * volume).cumsum() / denom


def passes_resonance(
    *,
    side: str,
    index_return: float,
    stock_close: float,
    stock_ma20: float,
    entry: float,
    vwap: float,
    max_vwap_distance: float = 0.005,
) -> dict:
    """三項都要過。指數漲跌與月線請傳進場當下已經知道的數字。

    max_vwap_distance 預設 0.5%，是濾網寬度，不是從歷史配出來的最佳距離。
    """
    if side == "long":
        index_ok = index_return > 0
        ma_ok = stock_close > stock_ma20
    elif side == "short":
        index_ok = index_return < 0
        ma_ok = stock_close < stock_ma20
    else:
        raise ValueError(f"共振只判斷多或空: {side}")
    if vwap is None or vwap <= 0 or entry is None or entry <= 0:
        vwap_ok = False
    else:
        vwap_ok = abs(entry / vwap - 1) <= max_vwap_distance
    return {
        "index_same_direction": bool(index_ok),
        "stock_ma20_same_direction": bool(ma_ok),
        "entry_near_vwap": bool(vwap_ok),
        "pass": bool(index_ok and ma_ok and vwap_ok),
    }


def _clock_minutes(value) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if hasattr(value, "hour"):
        return int(value.hour) * 60 + int(value.minute)
    text = str(value)
    hh, mm = text.split(":")
    return int(hh) * 60 + int(mm)


def review_discipline(events: list[dict], rules: dict) -> dict:
    """依時間檢查短線紀律。被擋下的進場不計入當日筆數。

    events 每一筆要有 time、stock_id、action。
    action 為 entry 時還要有 side；平倉（exit 或 stop_out）要有 realized_twd。
    stop_out 還要有 side，用來判斷之後是不是反手。
    """
    rules = validate_risk(rules)
    open_min = _clock_minutes(rules["session_open"])
    no_entry_before = open_min + int(rules["no_entry_first_minutes"])
    per_stock_cap = int(rules["max_trades_per_stock_per_day"])
    day_cap = int(rules["max_trades_per_day"])
    loss_trigger = int(rules["consecutive_losses_for_rest"])
    rest_minutes = int(rules["rest_minutes"])
    lock = float(rules["daily_profit_lock_twd"])
    giveback = float(rules["daily_giveback_of_peak"])
    exceptions = set(rules["reversal_exception_strategy_ids"])

    per_stock: dict[str, int] = {}
    accepted = 0
    day_pnl = 0.0
    peak = 0.0
    consecutive_losses = 0
    rest_until = None
    stopped_side: dict[str, str] = {}
    violations = []

    for index, event in enumerate(events):
        action = event.get("action")
        when = _clock_minutes(event.get("time"))
        stock = str(event.get("stock_id", ""))
        if action in {"exit", "stop_out"}:
            realized = float(event.get("realized_twd", 0))
            day_pnl += realized
            peak = max(peak, day_pnl)
            if realized < 0:
                consecutive_losses += 1
                if consecutive_losses >= loss_trigger:
                    rest_until = when + rest_minutes
                    consecutive_losses = 0
            else:
                consecutive_losses = 0
            if action == "stop_out" and event.get("side"):
                stopped_side[stock] = event["side"]
            continue
        if action != "entry":
            raise ValueError(f"未知的 action: {action}")

        reasons = []
        if when < no_entry_before:
            reasons.append("first_15_minutes")
        if accepted >= day_cap:
            reasons.append("max_trades_per_day")
        if per_stock.get(stock, 0) >= per_stock_cap:
            reasons.append("max_trades_per_stock")
        if rest_until is not None and when < rest_until:
            reasons.append("rest_after_losses")
        if day_pnl >= lock:
            reasons.append("daily_profit_lock")
        if peak > 0 and day_pnl <= peak * (1 - giveback):
            reasons.append("daily_giveback")
        if rules["no_reversal_after_stop"]:
            prev = stopped_side.get(stock)
            side = event.get("side")
            strategy_id = event.get("strategy_id")
            if prev and side and side != prev and strategy_id not in exceptions:
                reasons.append("reversal_after_stop")
        if reasons:
            violations.append({
                "index": index,
                "rules": reasons,
                "time": event.get("time"),
                "stock_id": stock,
            })
            continue
        accepted += 1
        per_stock[stock] = per_stock.get(stock, 0) + 1

    return {
        "violations": violations,
        "accepted_entries": accepted,
        "blocked": len(violations),
    }


def _needs_minutes(setup: dict, minute_df) -> dict | None:
    req = setup.get("data_requirement")
    if req in {"intraday", "intraday_minute"} and (minute_df is None or len(minute_df) == 0):
        return {
            "status": "requires_intraday_data",
            "data_requirement": req,
            "samples": 0,
            "note": "此策略需要盤中分 K。未提供 minute_df 時不拿日 K 代替。",
        }
    return None


def _bracket_pcts(setup: dict) -> tuple[float, float]:
    exit_rule = setup["exit"]
    return float(exit_rule["stop_loss"]), float(exit_rule["target_return"])


def run_short_term_backtest(
    strategy: dict,
    price_df: pd.DataFrame | None = None,
    minute_df: pd.DataFrame | None = None,
    prior_close: float | None = None,
) -> dict:
    """依策略 JSON 選擇回測。濾網與紀律不產生損益數字。"""
    setup = strategy.get("setup") or {}
    if setup.get("family") != "short_term":
        raise ValueError("不是短線策略")
    kind = (setup.get("signal") or {}).get("type")
    if setup.get("role") == "filter":
        return {
            "status": "filter_only",
            "samples": 0,
            "signal": kind,
            "note": "這是濾網，沒有獨立的進出場損益。",
        }
    if setup.get("role") == "risk":
        return {
            "status": "risk_only",
            "samples": 0,
            "signal": kind,
            "note": "這是紀律規則。用 review_discipline 檢查當天的進出場，不回測價量。",
        }
    blocked = _needs_minutes(setup, minute_df)
    if blocked:
        return blocked

    if kind == "weekday_hold":
        ma_period = 240
        for item in setup.get("filters") or []:
            if item.get("type") == "close_above_ma":
                ma_period = int(item["ma_period"])
        signal = setup["signal"]
        return backtest_weekday_hold(
            price_df,
            ma_period=ma_period,
            entry_weekday=signal["entry_weekday"],
            exit_weekday=signal["exit_weekday"],
        )
    if kind == "prior_day_return":
        stop, target = _bracket_pcts(setup)
        return backtest_prior_day_return(
            price_df,
            min_return=float(setup["signal"]["min_return"]),
            side=setup["side"],
            stop_pct=stop,
            target_pct=target,
        )
    if kind == "breakout_high":
        stop, target = _bracket_pcts(setup)
        return backtest_breakout_high(
            price_df,
            lookback=int(setup["signal"]["lookback"]),
            side=setup["side"],
            stop_pct=stop,
            target_pct=target,
        )
    if kind == "gap_open":
        stop, target = _bracket_pcts(setup)
        signal = setup["signal"]
        return backtest_gap_open(
            price_df,
            direction=signal["direction"],
            gap_return_at_most=signal.get("gap_return_at_most"),
            gap_return_at_least=signal.get("gap_return_at_least"),
            side=setup["side"],
            stop_pct=stop,
            target_pct=target,
        )
    if kind == "session_extreme":
        stop, target = _bracket_pcts(setup)
        signal = setup["signal"]
        return backtest_session_extreme(
            minute_df,
            zone=signal["zone"],
            range_fraction=float(signal["range_fraction"]),
            side=setup["side"],
            stop_pct=stop,
            target_pct=target,
        )
    if kind == "session_direction":
        stop, target = _bracket_pcts(setup)
        return backtest_session_direction(
            minute_df,
            when=setup["signal"]["when"],
            side=setup["side"],
            stop_pct=stop,
            target_pct=target,
            prior_close=prior_close,
        )
    if kind == "stop_and_reverse":
        signal = setup["signal"]
        return backtest_stop_and_reverse(
            minute_df,
            stop_pcts=list(signal["stop_pcts"]),
            fraction_from_high=float(signal["chase"]["range_fraction_from_high"]),
        )
    raise ValueError(f"無法回測的 signal.type: {kind}")
