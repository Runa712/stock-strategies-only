"""短線策略 setup / risk 的欄位檢查。

既有策略沒有這兩個欄位時，loader 不會補上，驗證結果維持原樣。
這裡不讀任何成交紀錄。
"""

from __future__ import annotations

SIGNAL_TYPES = (
    "weekday_hold",
    "index_ma_trend",
    "prior_day_return",
    "breakout_high",
    "session_extreme",
    "session_direction",
    "gap_open",
    "stop_and_reverse",
    "resonance",
    "discipline",
)

_SETUP_KEYS = {
    "family",
    "role",
    "data_requirement",
    "side",
    "instrument",
    "signal",
    "exit",
    "filters",
    "checks",
    "notes",
    "risk_profile",
    "execution_note",
}

_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday"}
_DATA = {"daily", "intraday", "intraday_minute", "none"}
_ROLES = {"signal", "filter", "risk"}
_SIDES = {"long", "short", "both", "none"}
_INSTRUMENTS = {"TAIEX", "TPEx", "stock", "stock_or_futures"}


class SetupError(ValueError):
    """setup 或 risk 不符合 SCHEMA.md。"""


def _reject_unknown(obj: dict, allowed: set[str], where: str) -> None:
    extra = set(obj) - allowed
    if extra:
        raise SetupError(f"{where} 有未定義欄位: {', '.join(sorted(extra))}")


def _as_float(value, name: str, lo: float, hi: float) -> float:
    """接受 (lo, hi]。等於 lo 時先放行，由呼叫端決定要不要拒絕。"""
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise SetupError(f"{name} 必須是數字") from exc
    if num < lo or num > hi:
        raise SetupError(f"{name} 必須介於 {lo} 與 {hi} 之間")
    return num


def _pct(value, name: str) -> float:
    """停損、停利、漲幅：大於 0、不超過 50%。"""
    num = _as_float(value, name, 0.0, 0.5)
    if num <= 0:
        raise SetupError(f"{name} 必須大於 0")
    return num


def _pos_int(value, name: str, hi: int = 500) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError) as exc:
        raise SetupError(f"{name} 必須是整數") from exc
    if isinstance(value, bool) or num < 1 or num > hi:
        raise SetupError(f"{name} 必須是 1 到 {hi} 的整數")
    return num


def _strings(value, name: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x.strip() for x in value):
        raise SetupError(f"{name} 必須是非空字串陣列")
    return [x.strip() for x in value]


def _validate_filters(filters) -> list[dict]:
    if not isinstance(filters, list):
        raise SetupError("filters 必須是陣列")
    out = []
    for item in filters:
        if not isinstance(item, dict) or "type" not in item:
            raise SetupError("每個 filter 都要有 type")
        kind = item["type"]
        if kind == "close_above_ma":
            _reject_unknown(item, {"type", "ma_period", "instrument"}, "close_above_ma")
            out.append({
                "type": kind,
                "ma_period": _pos_int(item.get("ma_period"), "filters.ma_period"),
                "instrument": item.get("instrument"),
            })
            if out[-1]["instrument"] not in {"TAIEX", "TPEx"}:
                raise SetupError("close_above_ma 的 instrument 只能是 TAIEX 或 TPEx")
        elif kind in {"index_ma_trend", "resonance"}:
            _reject_unknown(item, {"type", "ref"}, kind)
            ref = item.get("ref")
            if not isinstance(ref, str) or not ref.strip():
                raise SetupError(f"{kind} 濾網要指名 ref")
            out.append({"type": kind, "ref": ref.strip()})
        else:
            raise SetupError(f"未知的 filter type: {kind}")
    return out


def _validate_signal(signal: dict) -> dict:
    if not isinstance(signal, dict):
        raise SetupError("signal 必須是物件")
    kind = signal.get("type")
    if kind not in SIGNAL_TYPES:
        raise SetupError(f"未知的 signal.type: {kind}")

    if kind == "weekday_hold":
        _reject_unknown(signal, {
            "type", "entry_weekday", "entry_price", "exit_weekday", "exit_price",
            "require_next_session_weekday",
        }, "weekday_hold")
        entry_weekday = signal.get("entry_weekday")
        exit_weekday = signal.get("exit_weekday")
        nxt = signal.get("require_next_session_weekday")
        if entry_weekday not in _WEEKDAYS or exit_weekday not in _WEEKDAYS:
            raise SetupError("weekday_hold 的星期必須是 monday 到 friday")
        if signal.get("entry_price") != "close" or signal.get("exit_price") != "close":
            raise SetupError("weekday_hold 的進出場價必須是 close")
        if nxt != exit_weekday:
            raise SetupError("下一根 K 必須就是出場星期，否則跳過")
        return dict(signal)

    if kind == "index_ma_trend":
        _reject_unknown(signal, {"type", "ma_period", "benchmarks", "long_when", "short_when"}, kind)
        if signal.get("long_when") != "above_ma" or signal.get("short_when") != "below_ma":
            raise SetupError("月線濾網只允許線上做多、線下做空")
        benches = signal.get("benchmarks")
        if not isinstance(benches, list) or not benches:
            raise SetupError("benchmarks 必須是非空陣列")
        clean = []
        for row in benches:
            if not isinstance(row, dict):
                raise SetupError("benchmark 必須是物件")
            _reject_unknown(row, {"cap_segment", "index_id"}, "benchmark")
            if row.get("cap_segment") not in {"large", "small_mid"}:
                raise SetupError("cap_segment 只能是 large 或 small_mid")
            if row.get("index_id") not in {"TAIEX", "TPEx"}:
                raise SetupError("index_id 只能是 TAIEX 或 TPEx")
            clean.append({"cap_segment": row["cap_segment"], "index_id": row["index_id"]})
        return {
            "type": kind,
            "ma_period": _pos_int(signal.get("ma_period"), "ma_period"),
            "benchmarks": clean,
            "long_when": "above_ma",
            "short_when": "below_ma",
        }

    if kind == "prior_day_return":
        _reject_unknown(signal, {"type", "min_return", "includes_limit_up", "limit_up_return"}, kind)
        min_return = _pct(signal.get("min_return"), "min_return")
        limit_up = _pct(signal.get("limit_up_return"), "limit_up_return")
        if signal.get("includes_limit_up") is not True:
            raise SetupError("大漲隔日策略必須註明包含漲停")
        if limit_up < min_return:
            raise SetupError("漲停門檻不能低於大漲門檻，否則 5% 規則包不住漲停")
        return {
            "type": kind,
            "min_return": min_return,
            "includes_limit_up": True,
            "limit_up_return": limit_up,
        }

    if kind == "breakout_high":
        _reject_unknown(signal, {"type", "lookback"}, kind)
        return {"type": kind, "lookback": _pos_int(signal.get("lookback"), "lookback", hi=120)}

    if kind == "session_extreme":
        _reject_unknown(signal, {"type", "zone", "range_fraction"}, kind)
        zone = signal.get("zone")
        if zone not in {"near_low", "near_high"}:
            raise SetupError("zone 只能是 near_low 或 near_high")
        frac = _as_float(signal.get("range_fraction"), "range_fraction", 0.0, 0.5)
        if frac <= 0:
            raise SetupError("range_fraction 必須大於 0")
        return {"type": kind, "zone": zone, "range_fraction": frac}

    if kind == "session_direction":
        _reject_unknown(signal, {"type", "when"}, kind)
        when = signal.get("when")
        if when not in {"trading_below_prior_close", "trading_above_prior_close"}:
            raise SetupError("session_direction.when 不正確")
        return {"type": kind, "when": when}

    if kind == "gap_open":
        _reject_unknown(signal, {"type", "direction", "gap_return_at_most", "gap_return_at_least"}, kind)
        direction = signal.get("direction")
        if direction == "down":
            if "gap_return_at_least" in signal:
                raise SetupError("開低用 gap_return_at_most")
            gap = _as_float(signal.get("gap_return_at_most"), "gap_return_at_most", -0.5, 0.0)
            if gap >= 0:
                raise SetupError("開低門檻必須是負數")
            return {"type": kind, "direction": "down", "gap_return_at_most": gap}
        if direction == "up":
            gap = _pct(signal.get("gap_return_at_least"), "gap_return_at_least")
            return {"type": kind, "direction": "up", "gap_return_at_least": gap}
        raise SetupError("gap_open.direction 只能是 down 或 up")

    if kind == "stop_and_reverse":
        _reject_unknown(signal, {
            "type", "initial_side", "chase", "stop_pcts", "flip_to", "flip_price", "hold_until",
        }, kind)
        if signal.get("initial_side") != "long" or signal.get("flip_to") != "short":
            raise SetupError("停損反手目前只定義追多碰到停損後翻空")
        if signal.get("flip_price") != "stop" or signal.get("hold_until") != "session_close":
            raise SetupError("反手價必須是停損價，並持有到收盤")
        chase = signal.get("chase")
        if not isinstance(chase, dict):
            raise SetupError("chase 必須是物件")
        _reject_unknown(chase, {"range_fraction_from_high"}, "chase")
        frac = _as_float(chase.get("range_fraction_from_high"), "range_fraction_from_high", 0.0, 0.5)
        if frac <= 0:
            raise SetupError("range_fraction_from_high 必須大於 0")
        stops = signal.get("stop_pcts")
        if not isinstance(stops, list) or not stops:
            raise SetupError("stop_pcts 必須是非空陣列")
        clean_stops = [_pct(x, "stop_pcts") for x in stops]
        return {
            "type": kind,
            "initial_side": "long",
            "chase": {"range_fraction_from_high": frac},
            "stop_pcts": clean_stops,
            "flip_to": "short",
            "flip_price": "stop",
            "hold_until": "session_close",
        }

    if kind == "resonance":
        _reject_unknown(signal, {"type"}, kind)
        return {"type": kind}

    if kind == "discipline":
        _reject_unknown(signal, {"type"}, kind)
        return {"type": kind}

    raise SetupError(f"尚未實作的 signal.type: {kind}")


def _validate_exit(exit_rule, signal_type: str) -> dict | None:
    if exit_rule is None:
        return None
    if not isinstance(exit_rule, dict):
        raise SetupError("exit 必須是物件")
    mode = exit_rule.get("mode")
    if mode == "time":
        _reject_unknown(exit_rule, {"mode", "when"}, "exit")
        if not isinstance(exit_rule.get("when"), str) or not exit_rule["when"].strip():
            raise SetupError("時間出場要寫 when")
        return {"mode": "time", "when": exit_rule["when"].strip()}
    if mode == "bracket":
        _reject_unknown(exit_rule, {"mode", "stop_loss", "target_return", "session_exit", "same_bar"}, "exit")
        if exit_rule.get("session_exit") != "close":
            raise SetupError("括號單沒碰到停損停利時，以當日收盤出場")
        if exit_rule.get("same_bar") != "stop_first":
            raise SetupError("同一根 K 同時碰到停損與停利時，日 K 無法分先後，回測採停損優先")
        return {
            "mode": "bracket",
            "stop_loss": _pct(exit_rule.get("stop_loss"), "stop_loss"),
            "target_return": _pct(exit_rule.get("target_return"), "target_return"),
            "session_exit": "close",
            "same_bar": "stop_first",
        }
    if mode == "flip_to_close":
        _reject_unknown(exit_rule, {"mode", "session_exit"}, "exit")
        if exit_rule.get("session_exit") != "close":
            raise SetupError("反手後必須持有到收盤")
        if signal_type != "stop_and_reverse":
            raise SetupError("flip_to_close 只用於停損反手")
        return {"mode": "flip_to_close", "session_exit": "close"}
    if mode == "none":
        _reject_unknown(exit_rule, {"mode"}, "exit")
        return {"mode": "none"}
    raise SetupError(f"未知的 exit.mode: {mode}")


def _validate_checks(checks) -> list[dict]:
    if not isinstance(checks, list) or not checks:
        raise SetupError("共振 checks 必須是非空陣列")
    required = {"index_same_direction", "stock_ma20_same_direction", "entry_near_vwap"}
    out = []
    seen = set()
    for item in checks:
        if not isinstance(item, dict):
            raise SetupError("check 必須是物件")
        cid = item.get("id")
        if cid not in required:
            raise SetupError(f"未知的共振項目: {cid}")
        seen.add(cid)
        desc = item.get("description")
        if not isinstance(desc, str) or not desc.strip():
            raise SetupError("每個共振項目都要有 description")
        clean = {"id": cid, "description": desc.strip()}
        if cid == "stock_ma20_same_direction":
            _reject_unknown(item, {"id", "description", "ma_period"}, cid)
            clean["ma_period"] = _pos_int(item.get("ma_period"), "ma_period")
        elif cid == "entry_near_vwap":
            _reject_unknown(item, {"id", "description", "max_distance"}, cid)
            dist = _as_float(item.get("max_distance"), "max_distance", 0.0, 0.05)
            if dist <= 0:
                raise SetupError("max_distance 必須大於 0")
            clean["max_distance"] = dist
        else:
            _reject_unknown(item, {"id", "description"}, cid)
        out.append(clean)
    if seen != required:
        raise SetupError("共振必須同時包含大盤方向、個股月線、貼近 VWAP")
    return out


def validate_setup(setup: dict) -> dict:
    """檢查並回傳乾淨的 setup。未知欄位會失敗，避免默默丟掉規則。"""
    if not isinstance(setup, dict):
        raise SetupError("setup 必須是物件")
    _reject_unknown(setup, _SETUP_KEYS, "setup")
    if setup.get("family") != "short_term":
        raise SetupError("family 必須是 short_term")
    role = setup.get("role")
    if role not in _ROLES:
        raise SetupError("role 必須是 signal、filter 或 risk")
    data_requirement = setup.get("data_requirement")
    if data_requirement not in _DATA:
        raise SetupError("data_requirement 不正確")
    side = setup.get("side")
    if side not in _SIDES:
        raise SetupError("side 不正確")

    signal = _validate_signal(setup.get("signal"))
    clean: dict = {
        "family": "short_term",
        "role": role,
        "data_requirement": data_requirement,
        "side": side,
        "signal": signal,
    }
    if "instrument" in setup:
        if setup["instrument"] not in _INSTRUMENTS:
            raise SetupError("instrument 不正確")
        clean["instrument"] = setup["instrument"]
    if "exit" in setup:
        clean["exit"] = _validate_exit(setup.get("exit"), signal["type"])
    if "filters" in setup:
        clean["filters"] = _validate_filters(setup.get("filters"))
    if "checks" in setup:
        clean["checks"] = _validate_checks(setup.get("checks"))
    if "notes" in setup:
        clean["notes"] = _strings(setup.get("notes"), "notes")
    if "risk_profile" in setup:
        ref = setup.get("risk_profile")
        if not isinstance(ref, str) or not ref.strip():
            raise SetupError("risk_profile 必須是策略 id")
        clean["risk_profile"] = ref.strip()
    if "execution_note" in setup:
        note = setup.get("execution_note")
        if not isinstance(note, str) or not note.strip():
            raise SetupError("execution_note 必須是字串")
        clean["execution_note"] = note.strip()

    _check_role_fit(clean)
    return clean


def _check_role_fit(setup: dict) -> None:
    kind = setup["signal"]["type"]
    role = setup["role"]
    side = setup["side"]
    need = setup["data_requirement"]

    if kind == "weekday_hold":
        filters = setup.get("filters") or []
        if not any(f["type"] == "close_above_ma" for f in filters):
            raise SetupError("週一做多必須帶 close_above_ma 濾網")
        if side != "long" or role != "signal" or need != "daily":
            raise SetupError("weekday_hold 必須是做多訊號，且用日 K")
        if setup.get("exit", {}).get("mode") != "time":
            raise SetupError("weekday_hold 必須是時間出場")

    if kind == "index_ma_trend":
        if role != "filter" or side != "both" or need != "daily":
            raise SetupError("月線濾網是雙向濾網，用日 K")
        ids = {row["index_id"] for row in setup["signal"]["benchmarks"]}
        if ids != {"TAIEX", "TPEx"}:
            raise SetupError("大型股用 TAIEX、中小型用 TPEx，兩個都要列")

    if kind == "session_extreme":
        zone = setup["signal"]["zone"]
        expect = "long" if zone == "near_low" else "short"
        if side != expect or role != "signal" or need != "intraday":
            raise SetupError("貼近低檔做多、貼近高檔做空，且需要盤中資料")
        if setup.get("exit", {}).get("mode") != "bracket":
            raise SetupError("低接／高空必須同時帶停損與停利")

    if kind == "session_direction":
        when = setup["signal"]["when"]
        expect = "short" if when == "trading_below_prior_close" else "long"
        if side != expect or role != "signal" or need != "intraday":
            raise SetupError("跌日放空、漲日做多，且需要盤中資料才知道當天方向")
        if setup.get("exit", {}).get("mode") != "bracket":
            raise SetupError("順當天方向必須同時帶停損與停利")

    if kind == "gap_open":
        direction = setup["signal"]["direction"]
        expect = "short" if direction == "down" else "long"
        if side != expect or role != "signal" or need != "daily":
            raise SetupError("跳空策略的方向與資料需求不一致")
        if setup.get("exit", {}).get("mode") != "bracket":
            raise SetupError("跳空策略必須同時帶停損與停利")

    if kind in {"prior_day_return", "breakout_high"}:
        if side != "long" or role != "signal" or need != "daily":
            raise SetupError(f"{kind} 必須是做多訊號，且用日 K")
        if setup.get("exit", {}).get("mode") != "bracket":
            raise SetupError(f"{kind} 必須同時帶停損與停利")

    if kind == "stop_and_reverse":
        if role != "signal" or need != "intraday_minute":
            raise SetupError("停損反手需要一分 K，沒有分 K 只保留定義")
        if side != "long":
            raise SetupError("停損反手的初始部位是追多")
        if setup.get("exit", {}).get("mode") != "flip_to_close":
            raise SetupError("停損反手出場方式必須是 flip_to_close")

    if kind == "resonance":
        if role != "filter" or need != "intraday" or "checks" not in setup:
            raise SetupError("共振是盤中濾網，必須帶三項 checks")

    if kind == "discipline":
        if role != "risk" or side != "none" or need != "none":
            raise SetupError("紀律規則本身不是進出場，也不需要價量")
        if setup.get("exit", {}).get("mode") != "none":
            raise SetupError("紀律策略沒有出場價")


def validate_risk(risk: dict) -> dict:
    """短線紀律。金額是規則門檻，不是某人的帳戶餘額。"""
    if not isinstance(risk, dict):
        raise SetupError("risk 必須是物件")
    allowed = {
        "session_open",
        "no_entry_first_minutes",
        "max_trades_per_stock_per_day",
        "no_reversal_after_stop",
        "reversal_exception_strategy_ids",
        "daily_profit_lock_twd",
        "daily_giveback_of_peak",
        "consecutive_losses_for_rest",
        "rest_minutes",
        "max_trades_per_day",
    }
    _reject_unknown(risk, allowed, "risk")
    missing = allowed - set(risk)
    if missing:
        raise SetupError(f"risk 缺少欄位: {', '.join(sorted(missing))}")

    session_open = risk.get("session_open")
    if not isinstance(session_open, str) or len(session_open) != 5 or session_open[2] != ":":
        raise SetupError("session_open 格式必須是 HH:MM")
    hh, mm = session_open.split(":")
    if not (hh.isdigit() and mm.isdigit()):
        raise SetupError("session_open 格式必須是 HH:MM")
    if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
        raise SetupError("session_open 時間不正確")

    giveback = _as_float(risk.get("daily_giveback_of_peak"), "daily_giveback_of_peak", 0.0, 1.0)
    if giveback <= 0:
        raise SetupError("daily_giveback_of_peak 必須大於 0")
    lock = _as_float(risk.get("daily_profit_lock_twd"), "daily_profit_lock_twd", 0.0, 1_000_000_000)
    if lock <= 0:
        raise SetupError("daily_profit_lock_twd 必須大於 0")
    if not isinstance(risk.get("no_reversal_after_stop"), bool):
        raise SetupError("no_reversal_after_stop 必須是布林")

    return {
        "session_open": session_open,
        "no_entry_first_minutes": _pos_int(risk.get("no_entry_first_minutes"), "no_entry_first_minutes", hi=120),
        "max_trades_per_stock_per_day": _pos_int(
            risk.get("max_trades_per_stock_per_day"), "max_trades_per_stock_per_day", hi=20
        ),
        "no_reversal_after_stop": risk["no_reversal_after_stop"],
        "reversal_exception_strategy_ids": _strings(
            risk.get("reversal_exception_strategy_ids"), "reversal_exception_strategy_ids"
        ),
        "daily_profit_lock_twd": lock,
        "daily_giveback_of_peak": giveback,
        "consecutive_losses_for_rest": _pos_int(
            risk.get("consecutive_losses_for_rest"), "consecutive_losses_for_rest", hi=10
        ),
        "rest_minutes": _pos_int(risk.get("rest_minutes"), "rest_minutes", hi=240),
        "max_trades_per_day": _pos_int(risk.get("max_trades_per_day"), "max_trades_per_day", hi=50),
    }


def cross_check_short_term(strategy: dict) -> None:
    """setup 與扁平 params、risk 之間不能互相矛盾。"""
    setup = strategy.get("setup")
    if not setup:
        if "risk" in strategy:
            raise SetupError("有 risk 時必須有 setup")
        return
    params = strategy.get("params") or {}
    if params.get("fundamental_pass_required"):
        raise SetupError("短線策略不把基本面當成進場條件")
    if int(params.get("hold_days", 0)) != 1:
        raise SetupError("短線策略的 hold_days 必須是 1，真正出場寫在 setup")
    if setup.get("role") == "risk" and "risk" not in strategy:
        raise SetupError("紀律策略必須帶 risk")
    if setup.get("role") != "risk" and "risk" in strategy:
        raise SetupError("紀律數字集中在 short-term-discipline，其他策略用 risk_profile 引用")

    exit_rule = setup.get("exit") or {}
    if exit_rule.get("mode") == "bracket":
        if abs(float(params.get("stop_loss", -1)) - exit_rule["stop_loss"]) > 1e-9:
            raise SetupError("params.stop_loss 必須與 setup.exit.stop_loss 相同")
        if abs(float(params.get("target_return", -1)) - exit_rule["target_return"]) > 1e-9:
            raise SetupError("params.target_return 必須與 setup.exit.target_return 相同")
    if setup["signal"]["type"] == "stop_and_reverse":
        smallest = min(setup["signal"]["stop_pcts"])
        if abs(float(params.get("stop_loss", -1)) - smallest) > 1e-9:
            raise SetupError("params.stop_loss 必須等於最小的反手停損")
