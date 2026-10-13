"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, Strategy, RunResult } from "@/lib/api";
import { ActionBadge, SourceBadge } from "@/components/ActionBadge";

export default function StrategyDetail() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getStrategy(id).then(setStrategy).catch((e) => setError(e.message));
  }, [id]);

  async function doRun() {
    setRunning(true);
    setError(null);
    try {
      const r = await api.run(id);
      setRun(r);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  if (!strategy && !error) return <div className="card text-muted">載入中…</div>;
  if (error) return <div className="card border-err/40 text-err">錯誤：{error}</div>;
  if (!strategy) return null;

  return (
    <div className="space-y-6">
      <Link href="/strategies" className="text-sm text-muted hover:text-text">← 回策略庫</Link>

      <div className="card">
        <div className="flex items-center gap-2 mb-2">
          <h1 className="text-2xl font-semibold">{strategy.name}</h1>
          <SourceBadge source={strategy.source} />
        </div>
        <div className="text-xs text-muted font-mono mb-3">{strategy.id}</div>
        {strategy.description && <p className="text-sm text-muted leading-relaxed">{strategy.description}</p>}
        <button onClick={doRun} disabled={running} className="btn-primary mt-5">
          {running ? "執行中…" : "▶ 用此策略跑一次 watchlist"}
        </button>
      </div>

      {strategy.setup?.family === "short_term" && <SetupCard strategy={strategy} />}

      <div className="card">
        <h2 className="font-medium mb-4">策略參數</h2>
        {strategy.setup?.family === "short_term" && (
          <p className="text-sm text-muted mb-4">
            下面是舊版評分器的扁平參數。短線進出看上面的規則；用這份策略跑 watchlist 不會當成基本面買進。
          </p>
        )}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
          {Object.entries(strategy.params).map(([k, v]) => (
            <div key={k}>
              <div className="text-xs text-muted">{k}</div>
              <div className="font-mono">{renderVal(v)}</div>
            </div>
          ))}
        </div>
      </div>

      {run && (
        <div className="card">
          <h2 className="font-medium mb-3">執行結果</h2>
          <div className="text-sm text-muted mb-3">{run.market.note}</div>
          <div className="flex gap-2 mb-4">
            <span className="badge-buy">BUY {run.summary.buy}</span>
            <span className="badge-watch">WATCH {run.summary.watch}</span>
            <span className="badge-skip">SKIP {run.summary.skip}</span>
            {run.summary.error > 0 && <span className="badge-err">ERR {run.summary.error}</span>}
          </div>
          <div className="overflow-x-auto -mx-5">
            <table className="w-full text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="text-left px-5 py-2">代號 / 名稱</th>
                  <th className="text-left px-2 py-2">動作</th>
                  <th className="text-right px-2 py-2">總分</th>
                  <th className="text-right px-2 py-2">技術</th>
                  <th className="text-right px-2 py-2">回測勝率</th>
                  <th className="text-right px-5 py-2">參考價</th>
                </tr>
              </thead>
              <tbody>
                {run.results.map((r) => (
                  <tr key={r.stock_id} className="border-b border-line/50 hover:bg-panel2/50">
                    <td className="px-5 py-2">
                      <div className="font-mono">{r.stock_id}</div>
                      <div className="text-xs text-muted">{r.name}</div>
                    </td>
                    <td className="px-2 py-2"><ActionBadge action={r.action} /></td>
                    <td className="px-2 py-2 text-right font-mono">{r.signal_score ?? "—"}</td>
                    <td className="px-2 py-2 text-right font-mono">{r.components?.tech_score ?? "—"}</td>
                    <td className="px-2 py-2 text-right font-mono">
                      {r.components?.backtest_winrate != null
                        ? `${(r.components.backtest_winrate * 100).toFixed(0)}%`
                        : "—"}
                    </td>
                    <td className="px-5 py-2 text-right font-mono">{r.entry_price ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function SetupCard({ strategy }: { strategy: Strategy }) {
  const setup = strategy.setup || {};
  const signal = setup.signal || {};
  const exit = setup.exit || {};
  const intraday = setup.data_requirement === "intraday" || setup.data_requirement === "intraday_minute";
  return (
    <div className="card space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="font-medium">短線規則</h2>
        <span className="badge-short">{roleLabel(setup.role)}</span>
      </div>
      {intraday && (
        <p className="text-sm text-muted">
          這套要有盤中資料才回測得了。沒有分 K 時只保留規則，不會用日 K 湊結果。
        </p>
      )}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
        <Item label="方向" value={sideLabel(setup.side)} />
        <Item label="資料" value={dataLabel(setup.data_requirement)} />
        <Item label="訊號" value={signal.type} />
        {exit.mode === "bracket" && <Item label="停損" value={pct(exit.stop_loss)} />}
        {exit.mode === "bracket" && <Item label="停利" value={pct(exit.target_return)} />}
        {exit.mode === "bracket" && <Item label="同一根 K" value="先記停損，並標成模糊" />}
        {Array.isArray(signal.stop_pcts) && (
          <Item label="反手停損" value={signal.stop_pcts.map((v: number) => pct(v)).join("、")} />
        )}
        {setup.risk_profile && <Item label="紀律" value={setup.risk_profile} />}
      </div>
      {Array.isArray(setup.notes) && setup.notes.length > 0 && (
        <ul className="text-sm text-muted list-disc pl-5 space-y-1">
          {setup.notes.map((note: string) => <li key={note}>{note}</li>)}
        </ul>
      )}
      {setup.execution_note && <p className="text-sm text-muted">{setup.execution_note}</p>}
      {Array.isArray(setup.checks) && (
        <ul className="text-sm space-y-2">
          {setup.checks.map((check: any) => (
            <li key={check.id}>
              <div className="font-mono text-xs text-muted">{check.id}</div>
              <div>{check.description}</div>
            </li>
          ))}
        </ul>
      )}
      {strategy.risk && <RiskList risk={strategy.risk} />}
    </div>
  );
}

function RiskList({ risk }: { risk: Record<string, any> }) {
  const rows: [string, string][] = [
    ["開盤", String(risk.session_open ?? "—")],
    ["開盤後不進場", `${risk.no_entry_first_minutes} 分鐘`],
    ["同一檔上限", `一天 ${risk.max_trades_per_stock_per_day} 筆`],
    ["全日上限", `${risk.max_trades_per_day} 筆`],
    ["停損後反手", risk.no_reversal_after_stop ? "不允許" : "允許"],
    ["反手例外", (risk.reversal_exception_strategy_ids || []).join("、") || "—"],
    ["獲利鎖", `${Number(risk.daily_profit_lock_twd).toLocaleString("zh-TW")} 元`],
    ["高點回吐", `${Math.round(Number(risk.daily_giveback_of_peak) * 100)}% 就停`],
    ["連虧休息", `${risk.consecutive_losses_for_rest} 筆後休 ${risk.rest_minutes} 分鐘`],
  ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm pt-2 border-t border-line">
      {rows.map(([label, value]) => <Item key={label} label={label} value={value} />)}
    </div>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className="font-mono">{value}</div>
    </div>
  );
}
function roleLabel(role?: string) {
  if (role === "signal") return "進出場";
  if (role === "filter") return "濾網";
  if (role === "risk") return "紀律";
  return role || "—";
}
function sideLabel(side?: string) {
  if (side === "long") return "做多";
  if (side === "short") return "放空";
  if (side === "both") return "多空";
  if (side === "none") return "無";
  return "—";
}
function dataLabel(req?: string) {
  if (req === "daily") return "日 K";
  if (req === "intraday") return "盤中分 K";
  if (req === "intraday_minute") return "一分 K";
  if (req === "none") return "不需要價量";
  return "—";
}
function pct(v?: number) {
  if (typeof v !== "number") return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function renderVal(v: any) {
  if (typeof v === "boolean") return v ? "✓" : "✗";
  if (typeof v === "number") {
    if (v > 0 && v < 1) return v.toFixed(3);
    return String(v);
  }
  return String(v);
}
