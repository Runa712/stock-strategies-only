"use client";
import { useEffect, useState } from "react";
import { api, Strategy } from "@/lib/api";

type Props = {
  initial?: Partial<Strategy>;
  onSaved?: (s: Strategy) => void;
  saveLabel?: string;
};

export default function StrategyForm({ initial, onSaved, saveLabel = "儲存到策略庫" }: Props) {
  const [defaults, setDefaults] = useState<Record<string, any> | null>(null);
  const [name, setName] = useState(initial?.name || "");
  const [description, setDescription] = useState(initial?.description || "");
  const [params, setParams] = useState<Record<string, any>>(initial?.params || {});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getDefaults().then((d) => {
      setDefaults(d.params);
      if (!initial?.params) setParams({ ...d.params });
    });
  }, []);

  useEffect(() => {
    if (initial?.params) setParams(initial.params);
    if (initial?.name) setName(initial.name);
    if (initial?.description) setDescription(initial.description);
  }, [initial?.id]);

  if (!defaults) return <div className="card text-muted">載入預設參數中…</div>;

  function setP(k: string, v: any) {
    setParams((p) => ({ ...p, [k]: v }));
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const s = await api.saveStrategy({
        id: initial?.id,
        name,
        description,
        source: initial?.source || "manual",
        params,
        ...(initial?.setup ? { setup: initial.setup } : {}),
        ...(initial?.risk ? { risk: initial.risk } : {}),
      });
      onSaved?.(s);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  const weightSum = (params.weight_fundamental || 0) + (params.weight_technical || 0) + (params.weight_backtest || 0);
  const weightOk = Math.abs(weightSum - 1) < 0.01;

  return (
    <div className="space-y-6">
      <div className="card space-y-4">
        <div>
          <label className="label">策略名稱 *</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="例：動能短打 V1" />
        </div>
        <div>
          <label className="label">說明</label>
          <textarea className="input min-h-[72px]" value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder="一兩句話描述這個策略的目標與適用情境" />
        </div>
      </div>

      <Section title="基本面門檻">
        <NumField label="EPS 最小門檻" value={params.eps_threshold} step={0.5} onChange={(v) => setP("eps_threshold", v)} />
        <NumField label="ROE 最小門檻 (%)" value={params.roe_threshold} step={1} onChange={(v) => setP("roe_threshold", v)} />
        <BoolField label="強制基本面要過才能 BUY" value={params.fundamental_pass_required}
          onChange={(v) => setP("fundamental_pass_required", v)} />
      </Section>

      <Section title="回測與訊號">
        <NumField label="回測年數" value={params.backtest_years} step={1} onChange={(v) => setP("backtest_years", v)} />
        <NumField label="持有日數" value={params.hold_days} step={1} onChange={(v) => setP("hold_days", v)} />
        <NumField label="回測訊號門檻 (技術分)" value={params.min_tech_score_for_signal} step={5}
          onChange={(v) => setP("min_tech_score_for_signal", v)} />
      </Section>

      <Section title="風險">
        <NumField label="停利 %" value={(params.target_return ?? 0) * 100} step={0.5}
          onChange={(v) => setP("target_return", v / 100)} />
        <NumField label="停損 %" value={(params.stop_loss ?? 0) * 100} step={0.5}
          onChange={(v) => setP("stop_loss", v / 100)} />
      </Section>

      <Section title={`評分加權（總和：${weightSum.toFixed(2)}${weightOk ? " ✓" : " ⚠ 應為 1.00"}）`}>
        <NumField label="基本面權重" value={params.weight_fundamental} step={0.05}
          onChange={(v) => setP("weight_fundamental", v)} />
        <NumField label="技術面權重" value={params.weight_technical} step={0.05}
          onChange={(v) => setP("weight_technical", v)} />
        <NumField label="回測權重" value={params.weight_backtest} step={0.05}
          onChange={(v) => setP("weight_backtest", v)} />
        <NumField label="總分 BUY 門檻" value={params.min_total_score_for_buy} step={5}
          onChange={(v) => setP("min_total_score_for_buy", v)} />
        <NumField label="技術分 BUY 門檻" value={params.min_tech_score_for_buy} step={5}
          onChange={(v) => setP("min_tech_score_for_buy", v)} />
      </Section>

      <Section title="技術訊號開關">
        <BoolField label="均線多頭排列" value={params.use_ma_alignment} onChange={(v) => setP("use_ma_alignment", v)} />
        <BoolField label="布林下軌反彈" value={params.use_bollinger_bounce} onChange={(v) => setP("use_bollinger_bounce", v)} />
        <BoolField label="KD 黃金交叉" value={params.use_kd_golden_cross} onChange={(v) => setP("use_kd_golden_cross", v)} />
        <BoolField label="MACD 多頭" value={params.use_macd_bullish} onChange={(v) => setP("use_macd_bullish", v)} />
        <BoolField label="量價型態加減分" value={params.use_volume_patterns} onChange={(v) => setP("use_volume_patterns", v)} />
      </Section>

      <Section title="大盤濾鏡">
        <BoolField label="跌破均線時 BUY 降為 WATCH" value={params.market_filter_enabled}
          onChange={(v) => setP("market_filter_enabled", v)} />
        <NumField label="濾鏡均線天數" value={params.market_filter_ma_period} step={5}
          onChange={(v) => setP("market_filter_ma_period", v)} />
      </Section>

      {error && <div className="card border-err/40 text-err text-sm">儲存失敗：{error}</div>}

      <div className="flex gap-2">
        <button onClick={save} disabled={saving || !name.trim()} className="btn-primary">
          {saving ? "儲存中…" : saveLabel}
        </button>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card">
      <h2 className="font-medium mb-4">{title}</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">{children}</div>
    </div>
  );
}

function NumField({ label, value, step, onChange }:
  { label: string; value: number; step: number; onChange: (v: number) => void }) {
  return (
    <div>
      <label className="label">{label}</label>
      <input type="number" step={step} className="input" value={value ?? ""}
        onChange={(e) => onChange(parseFloat(e.target.value))} />
    </div>
  );
}
function BoolField({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center justify-between gap-3 cursor-pointer bg-panel2 border border-line rounded-lg px-3 py-2 text-sm">
      <span>{label}</span>
      <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} className="h-4 w-4" />
    </label>
  );
}
