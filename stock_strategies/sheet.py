"""
本地 CSV / Google Sheet 雙模式 Watchlist 管理

優先順序：
1. 有 GOOGLE_SHEET_ID + GOOGLE_CREDS_JSON → 用 Google Sheet
2. 否則 → 用本地 data/ 資料夾的 CSV
"""
import os
import csv
import json
from pathlib import Path

# 模式偵測
_LOCAL_MODE = not (os.environ.get("GOOGLE_SHEET_ID") and os.environ.get("GOOGLE_CREDS_JSON"))
_DATA_DIR = Path(os.environ.get("LOCAL_DATA_DIR", "data"))
_WATCHLIST_CSV = _DATA_DIR / "watchlist.csv"
_SIGNALS_CSV = _DATA_DIR / "signals.csv"
_PERFORMANCE_CSV = _DATA_DIR / "performance.csv"

SIGNALS_HEADERS = [
    "date", "stock_id", "name", "action", "signal_score",
    "entry_price", "stop_loss_price", "target_price",
    "rr_ratio", "position_pct", "winrate", "samples",
    "tech_signals", "risk_notes",
]

PERFORMANCE_HEADERS = [
    "signal_date", "stock_id", "name", "entry_close", "entry_open",
    "t5_date", "t5_close", "t5_ret",
    "t10_date", "t10_close", "t10_ret",
    "t20_date", "t20_close", "t20_ret",
    "hit_target", "hit_stop", "status",
]


# ---------- Google Sheet 後端（只在有憑證時才 import） ----------

def get_gsheet():
    import gspread
    from google.oauth2.service_account import Credentials
    creds_json = os.environ["GOOGLE_CREDS_JSON"]
    creds_dict = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])


def _read_watchlist_gsheet() -> list[dict]:
    sh = get_gsheet()
    ws = sh.worksheet("Watchlist")
    rows = ws.get_all_records()
    return [r for r in rows if str(r.get("enabled", "")).upper() in ("TRUE", "1", "YES")]


def _append_signals_gsheet(signals: list[dict]):
    if not signals:
        return
    sh = get_gsheet()
    try:
        ws = sh.worksheet("Signals")
    except Exception:
        ws = sh.add_worksheet(title="Signals", rows=1000, cols=20)
        ws.append_row(SIGNALS_HEADERS)
    rows = []
    for s in signals:
        c = s.get("components", {})
        rows.append([
            s.get("date", ""), s.get("stock_id", ""), s.get("name", ""),
            s.get("action", ""), s.get("signal_score", ""),
            s.get("entry_price", ""), s.get("stop_loss_price", ""),
            s.get("target_price", ""), s.get("risk_reward_ratio", ""),
            s.get("position_size_pct", ""), c.get("backtest_winrate", ""),
            c.get("backtest_samples", ""), ", ".join(c.get("tech_signals", [])),
            " / ".join(s.get("risk_notes", [])),
        ])
    ws.append_rows(rows)


def _read_performance_gsheet() -> list[dict]:
    sh = get_gsheet()
    try:
        ws = sh.worksheet("Performance")
    except Exception:
        return []
    return ws.get_all_records()


def _write_performance_gsheet(records: list[dict]):
    sh = get_gsheet()
    try:
        ws = sh.worksheet("Performance")
        ws.clear()
    except Exception:
        rows_alloc = max(2000, len(records) + 100)
        ws = sh.add_worksheet(title="Performance", rows=rows_alloc, cols=len(PERFORMANCE_HEADERS))
    ws.append_row(PERFORMANCE_HEADERS)
    if not records:
        return
    ws.append_rows([[r.get(h, "") for h in PERFORMANCE_HEADERS] for r in records])


def _append_performance_gsheet(record: dict):
    """單筆寫入（用 append_row 而非整頁重寫，避免夜盤後重跑的 race condition）"""
    sh = get_gsheet()
    try:
        ws = sh.worksheet("Performance")
    except Exception:
        ws = sh.add_worksheet(title="Performance", rows=2000, cols=len(PERFORMANCE_HEADERS))
        ws.append_row(PERFORMANCE_HEADERS)
    ws.append_row([record.get(h, "") for h in PERFORMANCE_HEADERS])


# ---------- 本地 CSV 後端 ----------

def _ensure_data_dir():
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_watchlist_csv() -> list[dict]:
    if not _WATCHLIST_CSV.exists():
        return []
    with _WATCHLIST_CSV.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if str(r.get("enabled", "")).strip().upper() in ("", "TRUE", "1", "YES")]


def _append_signals_csv(signals: list[dict]):
    if not signals:
        return
    _ensure_data_dir()
    is_new = not _SIGNALS_CSV.exists()
    with _SIGNALS_CSV.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(SIGNALS_HEADERS)
        for s in signals:
            c = s.get("components", {})
            w.writerow([
                s.get("date", ""), s.get("stock_id", ""), s.get("name", ""),
                s.get("action", ""), s.get("signal_score", ""),
                s.get("entry_price", ""), s.get("stop_loss_price", ""),
                s.get("target_price", ""), s.get("risk_reward_ratio", ""),
                s.get("position_size_pct", ""), c.get("backtest_winrate", ""),
                c.get("backtest_samples", ""), ", ".join(c.get("tech_signals", [])),
                " / ".join(s.get("risk_notes", [])),
            ])


def _read_performance_csv() -> list[dict]:
    if not _PERFORMANCE_CSV.exists():
        return []
    with _PERFORMANCE_CSV.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_performance_csv(records: list[dict]):
    _ensure_data_dir()
    with _PERFORMANCE_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PERFORMANCE_HEADERS)
        w.writeheader()
        for r in records:
            w.writerow({h: r.get(h, "") for h in PERFORMANCE_HEADERS})


def _append_performance_csv(record: dict):
    """單筆寫入 CSV"""
    _ensure_data_dir()
    is_new = not _PERFORMANCE_CSV.exists()
    with _PERFORMANCE_CSV.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PERFORMANCE_HEADERS)
        if is_new:
            w.writeheader()
        w.writerow({h: record.get(h, "") for h in PERFORMANCE_HEADERS})


# ---------- 對外統一介面 ----------

def read_watchlist() -> list[dict]:
    return _read_watchlist_gsheet() if not _LOCAL_MODE else _read_watchlist_csv()


def append_signals(signals: list[dict]):
    (_append_signals_gsheet if not _LOCAL_MODE else _append_signals_csv)(signals)


def read_performance() -> list[dict]:
    return _read_performance_gsheet() if not _LOCAL_MODE else _read_performance_csv()


def write_performance(records: list[dict]):
    (_write_performance_gsheet if not _LOCAL_MODE else _write_performance_csv)(records)


def append_performance(record: dict):
    """單筆寫入（供 performance.py 夜盤後逐筆更新用）"""
    (_append_performance_gsheet if not _LOCAL_MODE else _append_performance_csv)(record)


# ---------- Watchlist 編輯功能（僅 Google Sheet 模式支援） ----------

def add_to_watchlist(stock_id: str, name: str = "") -> dict:
    if _LOCAL_MODE:
        return {"status": "unsupported_in_local_mode", "hint": "請直接編輯 data/watchlist.csv"}
    sh = get_gsheet()
    ws = sh.worksheet("Watchlist")
    headers = _ensure_watchlist_headers(ws)
    sid_col = headers.index("stock_id") + 1
    name_col = headers.index("name") + 1 if "name" in headers else None
    en_col = headers.index("enabled") + 1
    rows = ws.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("stock_id", "")).strip() == str(stock_id).strip():
            current = str(r.get("enabled", "")).upper()
            if current in ("TRUE", "1", "YES"):
                return {"status": "exists", "stock_id": stock_id, "name": r.get("name", name)}
            ws.update_cell(i, en_col, "TRUE")
            return {"status": "reenabled", "stock_id": stock_id, "name": r.get("name", name)}
    new_row = [""] * len(headers)
    new_row[sid_col - 1] = str(stock_id)
    if name_col is not None:
        new_row[name_col - 1] = name
    new_row[en_col - 1] = "TRUE"
    ws.append_row(new_row)
    return {"status": "added", "stock_id": stock_id, "name": name}


def remove_from_watchlist(stock_id: str) -> dict:
    if _LOCAL_MODE:
        return {"status": "unsupported_in_local_mode", "hint": "請直接編輯 data/watchlist.csv"}
    sh = get_gsheet()
    ws = sh.worksheet("Watchlist")
    headers = _ensure_watchlist_headers(ws)
    if "enabled" not in headers:
        return {"status": "no_enabled_column"}
    en_col = headers.index("enabled") + 1
    rows = ws.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("stock_id", "")).strip() == str(stock_id).strip():
            ws.update_cell(i, en_col, "FALSE")
            return {"status": "disabled", "stock_id": stock_id}
    return {"status": "not_found", "stock_id": stock_id}


def _ensure_watchlist_headers(ws) -> list[str]:
    values = ws.get_all_values()
    if not values:
        headers = ["stock_id", "name", "enabled"]
        ws.append_row(headers)
        return headers
    return [h.strip() for h in values[0]]


def read_latest_signals(limit: int = 50) -> list[dict]:
    if _LOCAL_MODE:
        if not _SIGNALS_CSV.exists():
            return []
        with _SIGNALS_CSV.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        return rows[-limit:][::-1]
    sh = get_gsheet()
    try:
        ws = sh.worksheet("Signals")
    except Exception:
        return []
    rows = ws.get_all_records()
    if not rows:
        return []
    return rows[-limit:][::-1]


def is_local_mode() -> bool:
    return _LOCAL_MODE


def data_dir() -> Path:
    return _DATA_DIR
