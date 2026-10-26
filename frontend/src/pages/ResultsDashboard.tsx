import { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, ReferenceLine,
} from 'recharts';
import { FileText, RefreshCw } from 'lucide-react';import { runsAPI } from '@/lib/api';

const STRATEGY_LABELS: Record<string, string> = {
  s0: 'S0 (Full Schema)',
  s1: 'S1 (BM25)',
  s2: 'S2 (Dense)',
  s3: 'S3 (Hybrid RRF)',
  s6: 'S6 (Hybrid+Value)',
  absl: 'ABSL',
  absl_cap: 'ABSL (cap)',
};

export default function ResultsDashboard() {
  const { runId } = useParams();
  const [data, setData] = useState<any>(null);
  const [runMeta, setRunMeta] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (!runId) {
      setLoading(false);
      setData(null);
      setRunMeta(null);
      return;
    }
    try {
      const [s, r] = await Promise.all([
        runsAPI.getSummary(runId),
        runsAPI.getRun(runId),
      ]);
      setData(s.data);
      setRunMeta(r.data);
    } catch (e: any) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-gray-500">Loading results…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 rounded-lg bg-red-50 border border-red-200 text-red-700">
        {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="p-6 text-center">
        <FileText className="w-12 h-12 mx-auto text-gray-400 mb-3" />
        <h3 className="text-lg font-medium text-gray-900">No results available</h3>
        <p className="text-gray-500 mt-1">This run has no summary data yet.</p>
      </div>
    );
  }

  const rows: any[] = data.rows || [];
  const summary = data.summary || {};
  const fixedBudgetRows = rows.filter((r: any) => r.strategy !== 's0' && r.strategy !== 'absl' && r.budget != null);
  const s0Row = rows.find((r: any) => r.strategy === 's0');
  const abslRow = rows.find((r: any) => r.strategy === 'absl');

  // One line per strategy (s1, s2, s3, s6, absl_cap). Shared x-axis is the
  // numeric budget, sorted ascending; each strategy contributes its EX at the
  // budgets where it has a row.
  const CHART_STRATEGIES = ['s1', 's2', 's3', 's6', 'absl_cap'];
  const CHART_COLORS: Record<string, string> = {
    s1: '#3b82f6',
    s2: '#ef4444',
    s3: '#10b981',
    s6: '#f59e0b',
    absl_cap: '#6366f1',
  };
  const byBudget: Record<number, Record<string, number | null>> = {};
  fixedBudgetRows.forEach((r: any) => {
    const b = Number(r.budget);
    if (!byBudget[b]) {
      byBudget[b] = { budget: b } as Record<string, number | null>;
      CHART_STRATEGIES.forEach((s) => {
        byBudget[b][s] = null;
      });
    }
    byBudget[b][r.strategy] = parseFloat(r.ex);
  });
  const chartData = Object.values(byBudget).sort(
    (a: any, b: any) => a.budget - b.budget
  );
  const nQuestions = runMeta?.n_questions || (rows.length > 0 ? Math.max(...rows.map((r: any) => parseInt(r.n) || 0)) : 0);
  const isDemoSample = nQuestions <= 5;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Results Dashboard</h1>
        <p className="text-gray-600 mt-1">
          Run ID: <span className="font-mono text-sm">{runId}</span> ·
          {nQuestions} question{nQuestions !== 1 ? 's' : ''} evaluated ·
          <span className={`font-medium text-xs px-2 py-0.5 rounded ${
            isDemoSample ? 'bg-amber-100 text-amber-700' : 'bg-blue-100 text-blue-700'
          }`}>
            {isDemoSample ? '5-Question Demo Sample' : 'Full Benchmark Run'}
          </span>
        </p>
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-red-50 border border-red-200 text-red-700">
          {error}
        </div>
      )}

      {/* Key metrics */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <MetricCard
          title="Total traces"
          value={summary.n_traces ?? 'N/A'}
          subtitle="Individual (question, strategy, budget) records"
        />
        <MetricCard
          title="S0 EX (baseline)"
          value={s0Row ? (parseFloat(s0Row.ex) * 100).toFixed(1) + '%' : 'N/A'}
          subtitle="Full-schema execution accuracy"
        />
        <MetricCard
          title="ABSL EX"
          value={abslRow ? (parseFloat(abslRow.ex) * 100).toFixed(1) + '%' : 'N/A'}
          subtitle="Adaptive cascade execution accuracy"
        />
      </div>

      {/* AUBC */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm">
          <h3 className="font-semibold text-gray-900 mb-4">AUBC by Strategy</h3>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={Object.entries(summary.aubc || {}).map(([k, v]) => ({
                  name: STRATEGY_LABELS[k] || k.toUpperCase(),
                  value: v,
                }))}
              >
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                <YAxis label={{ value: 'AUBC (0–1)', angle: -90, position: 'insideLeft' }} />
                <Tooltip formatter={((value: number | undefined) => value != null ? value.toFixed(3) : '') as any} />
                <Bar dataKey="value" fill="#3b82f6" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="text-xs text-gray-500 mt-3">
            AUBC (Area Under the Budget Curve) is computed only for fixed-budget
            strategies S1, S2, S3, S6. Normalised to [0, 1]. Higher is better.
          </p>
        </div>

        <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm">
          <h3 className="font-semibold text-gray-900 mb-4">EX vs Schema-Token Budget</h3>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="budget"
                  type="number"
                  tick={{ fontSize: 10 }}
                  label={{ value: 'Schema-token budget (B)', position: 'bottom', offset: 0 }}
                />
                <YAxis
                  label={{ value: 'EX', angle: -90, position: 'insideLeft' }}
                  domain={[0, 1]}
                  tick={{ fontSize: 10 }}
                />
                <Tooltip />
                <Legend verticalAlign="top" align="right" />
                {CHART_STRATEGIES.map((strategy) => (
                  <Line
                    key={strategy}
                    type="linear"
                    dataKey={strategy}
                    stroke={CHART_COLORS[strategy]}
                    strokeWidth={2}
                    dot={{
                      fill: CHART_COLORS[strategy],
                      stroke: CHART_COLORS[strategy],
                      strokeWidth: 2,
                      radius: 5,
                    }}
                    name={STRATEGY_LABELS[strategy] || strategy.toUpperCase()}
                  />
                ))}
                {s0Row && (
                  <ReferenceLine
                    y={parseFloat(s0Row.ex)}
                    stroke="#94a3b8"
                    strokeDasharray="4 4"
                    strokeWidth={2}
                    label={{
                      value: `S0 (full schema, EX = ${(parseFloat(s0Row.ex) * 100).toFixed(1)}%)`,
                      position: 'inside',
                      fill: '#94a3b8',
                      fontSize: 11,
                    }}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
          <p className="text-xs text-gray-500 mt-3">
            S0 (full schema, budget=∞) is shown separately as the baseline —
            it is not a finite-budget strategy.
          </p>
        </div>
      </div>

      {/* Strategy comparison table */}
      <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm">
        <h3 className="font-semibold text-gray-900 mb-4">Strategy Comparison</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left pb-2 text-gray-600 font-medium">Strategy</th>
                <th className="text-left pb-2 text-gray-600 font-medium">Budget</th>
                <th className="text-right pb-2 text-gray-600 font-medium">n</th>
                <th className="text-right pb-2 text-gray-600 font-medium">EX</th>
                <th className="text-right pb-2 text-gray-600 font-medium">CI 95%</th>
                <th className="text-right pb-2 text-gray-600 font-medium">Avg Schema Tokens</th>
                <th className="text-right pb-2 text-gray-600 font-medium">Avg Prompt Tokens</th>
                <th className="text-right pb-2 text-gray-600 font-medium">BES</th>
                <th className="text-right pb-2 text-gray-600 font-medium">Violations</th>
                <th className="text-right pb-2 text-gray-600 font-medium">Escalation Rate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: any) => (
                <tr key={`${r.strategy}-${r.budget}`} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="py-2 text-gray-900 font-medium">{STRATEGY_LABELS[r.strategy] || r.strategy.toUpperCase()}</td>
                  <td className="py-2 font-mono text-xs">
                    {r.budget == null || r.budget === '' ? '∞' : r.budget}
                  </td>
                  <td className="py-2 text-right text-gray-700">{r.n}</td>
                  <td className="py-2 text-right text-gray-700">
                    {(parseFloat(r.ex || 0) * 100).toFixed(1)}%
                  </td>
                  <td className="py-2 text-right text-gray-700">
                    {r.ex_ci_lo != null && r.ex_ci_hi != null
                      ? `[${(parseFloat(r.ex_ci_lo) * 100).toFixed(1)}%, ${(parseFloat(r.ex_ci_hi) * 100).toFixed(1)}%]`
                      : 'N/A'}
                  </td>
                  <td className="py-2 text-right text-gray-700">{r.avg_schema_tokens ?? 'N/A'}</td>
                  <td className="py-2 text-right text-gray-700">{r.avg_prompt_tokens ?? 'N/A'}</td>
                  <td className="py-2 text-right text-gray-700">{r.bes ?? 'N/A'}</td>
                  <td className="py-2 text-right text-gray-700">{r.budget_violations ?? 'N/A'}</td>
                  <td className="py-2 text-right text-gray-700">
                    {r.escalation_rate != null ? (parseFloat(r.escalation_rate) * 100).toFixed(1) + '%' : 'N/A'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-gray-500 mt-3">
          BES = Budget Efficiency Score = EX / (avg prompt tokens / 1000).
          AUBC values are in the chart above. Cost per 1k queries:
          ${summary.cost_per_1k_queries?.toFixed(6) || 'N/A'} (only meaningful when prices are configured).
        </p>
      </div>

      {/* ABSL analysis */}
      {abslRow && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-5">
          <h3 className="font-semibold text-blue-900 mb-3">ABSL Analysis</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
            <div>
              <div className="text-blue-600 text-xs uppercase tracking-wider">EX</div>
              <div className="font-medium text-blue-900">
                {(parseFloat(abslRow.ex) * 100).toFixed(1)}%
              </div>
            </div>
            <div>
              <div className="text-blue-600 text-xs uppercase tracking-wider">Escalation rate</div>
              <div className="font-medium text-blue-900">
                {(parseFloat(abslRow.escalation_rate || 0) * 100).toFixed(1)}%
              </div>
            </div>
            <div>
              <div className="text-blue-600 text-xs uppercase tracking-wider">Avg schema tokens</div>
              <div className="font-medium text-blue-900">{abslRow.avg_schema_tokens ?? 'N/A'}</div>
            </div>
          </div>
        </div>
      )}

      {/* Plots */}
      <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm">
        <h3 className="font-semibold text-gray-900 mb-4">Generated Plots</h3>
        <div className="space-y-2">
              {(summary.plots || []).map((p: string, i: number) => {
                const filename = String(p).replace(/.*[\\/]/, '') || String(p);
                const url = runsAPI.getPlotUrl(runId!, filename);
                return (
                  <div key={i} className="flex items-center gap-3 p-3 rounded border border-gray-200">
                    <div className="w-8 h-8 rounded flex items-center justify-center bg-gray-50">
                      <img
                        src={url}
                        alt={filename}
                        className="w-7 h-7 object-contain rounded"
                      />
                    </div>
                    <span className="text-sm text-gray-700 font-mono truncate flex-1" title={filename}>
                      {filename}
                    </span>
                  </div>
                );
              })}
          {!(summary.plots || []).length && (
            <p className="text-gray-500 text-sm">No plots generated.</p>
          )}
        </div>
      </div>

      <div className="flex gap-3">
        <Link to={`/traces/${runId}`}>
          <button className="px-5 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 flex items-center gap-2">
            <RefreshCw className="w-4 h-4" /> View Traces
          </button>
        </Link>
      </div>
    </div>
  );
}

function MetricCard({ title, value, subtitle }: { title: string; value: string | number; subtitle: string }) {
  return (
    <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm">
      <div className="text-gray-500 text-xs uppercase tracking-wider mb-1">{title}</div>
      <div className="text-2xl font-bold text-gray-900">{value}</div>
      <div className="text-xs text-gray-500 mt-1">{subtitle}</div>
    </div>
  );
}
