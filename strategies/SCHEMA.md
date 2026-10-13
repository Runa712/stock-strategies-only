# 策略 JSON Schema

每個策略檔案 = 一個 `.json`，檔名 = `{id}.json`。

## 頂層欄位

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `id` | string | ✅ | 策略唯一 ID（建議用 slug 或 UUID）|
| `name` | string | ✅ | 顯示名稱 |
| `description` | string |   | 給人看的描述 |
| `source` | `"default" \| "manual" \| "ai"` |   | 來源；前端用來顯示徽章 |
| `created_at` / `updated_at` | ISO 8601 |   | 時間戳 |
| `params` | object | ✅ | 實際策略參數，扁平 dict（見下） |

## `params` 欄位

### 基本面門檻

- `eps_threshold` (number) — 最近兩年 EPS 最小值需 > 此數
- `roe_threshold` (number) — 最近兩年 ROE 最小值需 > 此數 (%)
- `fundamental_pass_required` (bool) — 是否強制基本面要通過才能 BUY

### 回測 & 訊號

- `backtest_years` (int) — 回測年數
- `hold_days` (int) — 持有日數
- `min_tech_score_for_signal` (int 0-100) — 回測時，技術分達多少算一次訊號

### 風險

- `target_return` (0-1) — 停利百分比
- `stop_loss` (0-1) — 停損百分比

### 評分加權

- `weight_fundamental` / `weight_technical` / `weight_backtest` (0-1) — 三者加總應 = 1
- `min_total_score_for_buy` (0-100) — 總分達多少才考慮 BUY
- `min_tech_score_for_buy` (0-100) — 技術分至少要達多少才考慮 BUY

### 技術訊號開關（影響評分）

- `use_ma_alignment` (bool) — 均線多頭排列
- `use_bollinger_bounce` (bool) — 布林下軌反彈
- `use_kd_golden_cross` (bool) — KD 黃金交叉
- `use_macd_bullish` (bool) — MACD 多頭
- `use_volume_patterns` (bool) — 量價型態加減分

### 大盤濾鏡

- `market_filter_enabled` (bool) — 跌破月線時 BUY 自動降 WATCH
- `market_filter_ma_period` (int) — 月線天數

## 選填：短線規則 `setup` 與紀律 `risk`

沒有這兩個欄位的策略（例如 `default.json`、`conservative.json`）維持原樣。`validate_strategy` 不會替它們補上 `setup` 或 `risk`。

有寫的時候，欄位必須符合下面的定義，多出來的鍵會驗證失敗。檢查實作在 `stock_strategies/short_term_schema.py`，回測在 `stock_strategies/short_term.py`。短線策略仍要帶齊上面的 `params`，讓舊的策略庫畫面讀得到；真正的進出場以 `setup` 為準。

短線策略的 `params` 另有兩條對應規則：`fundamental_pass_required` 必須是 `false`，`hold_days` 必須是 `1`。括號單的 `params.stop_loss`、`params.target_return` 必須與 `setup.exit` 相同。

### `setup`

| 欄位 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `family` | `"short_term"` | ✅ | 目前只有短線這一族 |
| `role` | `"signal" \| "filter" \| "risk"` | ✅ | 進出場、濾網，或紀律 |
| `data_requirement` | `"daily" \| "intraday" \| "intraday_minute" \| "none"` | ✅ | 日 K、盤中、一分 K，或純規則 |
| `side` | `"long" \| "short" \| "both" \| "none"` | ✅ | 方向 |
| `instrument` | `"TAIEX" \| "TPEx" \| "stock" \| "stock_or_futures"` |   | 標的 |
| `signal` | object | ✅ | 見下方 `signal.type` |
| `exit` | object |   | 出場。`mode` 為 `time`、`bracket`、`flip_to_close` 或 `none` |
| `filters` | array |   | 進場前要再過的濾網 |
| `checks` | array |   | 共振三項，只用於 `resonance` |
| `notes` | string[] |   | 給人看的補充 |
| `risk_profile` | string |   | 紀律策略的 id，例如 `short-term-discipline` |
| `execution_note` | string |   | 實際下單工具的說明，不參與計算 |

`exit.mode = "bracket"` 時要有 `stop_loss`、`target_return`（都是大於 0、不超過 0.5 的小數）、`session_exit: "close"`、`same_bar: "stop_first"`。停損和停利同時掛著，先碰到哪個就出哪個。日 K 若同一根同時碰到兩邊，無法知道先後，回測記停損並標成模糊。

`data_requirement` 是 `intraday` 或 `intraday_minute` 時，沒有分 K 就只保留定義，不拿日 K 湊回測。

### `signal.type`

| type | 用途 |
| --- | --- |
| `weekday_hold` | 指定星期收盤進、下一個指定星期收盤出。下一根若不是出場星期就跳過。必須另帶 `close_above_ma` 濾網 |
| `index_ma_trend` | 指數在均線上只做多、均線下只做空。`benchmarks` 同時列大型股 `TAIEX` 與中小型 `TPEx` |
| `prior_day_return` | 前一日收盤漲幅達 `min_return`（含漲停）隔日開盤做多 |
| `breakout_high` | 突破前 `lookback` 日最高價做多 |
| `session_extreme` | 進場在當時已知區間的低檔或高檔附近。需要盤中資料 |
| `session_direction` | 價格低於前收才放空，或高於前收才做多。需要盤中資料 |
| `gap_open` | 開盤相對前收跳空。開低用 `gap_return_at_most` |
| `stop_and_reverse` | 追多碰到停損後，在停損價翻空並持有到收盤。`stop_pcts` 可列多個幅度。需要一分 K |
| `resonance` | 濾網：大盤同向、個股在月線同側、進場價靠近當日 VWAP |
| `discipline` | 不是進出場。規則寫在頂層 `risk` |

共振的 `entry_near_vwap.max_distance` 是可調距離，預設不代表從歷史配出來的最佳值。傳進共振判斷的指數漲跌與月線，必須是進場當下已經知道的數字。

### `filters`

- `close_above_ma`：`ma_period`、`instrument`（`TAIEX` 或 `TPEx`）。收盤要在這條均線之上
- `index_ma_trend`：`ref` 指向月線濾網策略
- `resonance`：`ref` 指向共振策略

### `risk`

只放在紀律策略。其他策略用 `setup.risk_profile` 引用，不把同一份數字複製多份。

| 欄位 | 說明 |
| --- | --- |
| `session_open` | 開盤時間，`HH:MM` |
| `no_entry_first_minutes` | 開盤後幾分鐘內不進場 |
| `max_trades_per_stock_per_day` | 同一檔當天最多幾筆 |
| `no_reversal_after_stop` | 停損後當天不再反手 |
| `reversal_exception_strategy_ids` | 允許反手的策略 id（停損反手測試） |
| `daily_profit_lock_twd` | 當天已實現獲利達到這個金額就停止再開新倉 |
| `daily_giveback_of_peak` | 從當天獲利高點回吐這個比例就停止再開新倉 |
| `consecutive_losses_for_rest` | 連續幾筆虧損後休息 |
| `rest_minutes` | 休息幾分鐘 |
| `max_trades_per_day` | 短線一天最多幾筆 |

`daily_profit_lock_twd` 是紀律門檻，不是任何人的帳戶餘額或歷史損益。
