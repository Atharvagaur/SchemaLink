import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Play, AlertTriangle, Server, CheckCircle2 } from 'lucide-react';
import { metaAPI, datasetsAPI, runsAPI } from '@/lib/api';

const STRATEGIES = [
  { id: 's0', label: 'S0 — Full Schema (baseline, budget=∞)', type: 'baseline' as const },
  { id: 's1', label: 'S1 — BM25 ranking', type: 'fixed_budget' as const },
  { id: 's2', label: 'S2 — Dense embedding ranking', type: 'fixed_budget' as const },
  { id: 's3', label: 'S3 — Hybrid RRF', type: 'fixed_budget' as const },
  { id: 's6', label: 'S6 — Hybrid + value reordering', type: 'fixed_budget' as const },
  { id: 'absl', label: 'ABSL — Adaptive budget cascade', type: 'adaptive' as const },
];

const BUDGETS: Record<string, number[]> = {
  spider: [250, 500, 1000, 2000, 4000],
  bird: [1000, 2000, 4000, 8000, 16000],
};

export default function ExperimentRunner() {
  const navigate = useNavigate();
  const [meta, setMeta] = useState<any>(null);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({
    dataset: 'spider' as string,
    n_questions: 5,
    strategies: ['s0', 's1', 's2', 's3', 's6', 'absl'] as string[],
    smoke: true,
    demoMode: true,
    confirm_cost: false,
  });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [confirmShow, setConfirmShow] = useState(false);
  const [launchedRun, setLaunchedRun] = useState<string | null>(null);

  const abslTierBudgets: Record<string, number[]> = {
    spider: [500, 1500, 4000],
    bird: [1000, 4000, 16000],
  };

  useEffect(() => {
    Promise.all([
      metaAPI.getMeta().then((r) => setMeta(r.data)).catch(() => setSubmitError('Failed to load config')),
      datasetsAPI.getDatasets().then((r) => setDatasets(r.data.datasets)).catch(() => {}),
    ]).finally(() => setLoading(false));
  }, []);

  const handleStrategyChange = (id: string, checked: boolean) => {
    setForm(prev => ({
      ...prev,
      strategies: checked
        ? [...prev.strategies, id]
        : prev.strategies.filter(s => s !== id),
    }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);

    const { dataset, n_questions, strategies, smoke, demoMode } = form;

    if (!dataset || !strategies.length) {
      setSubmitError('Please select a dataset and at least one strategy.');
      return;
    }

    // Demo mode: exactly 5 questions, smoke mode
    if (demoMode) {
      setForm(prev => ({ ...prev, n_questions: 5, smoke: true }));
      launchRun();
      return;
    }

    // Cost warning: non-smoke runs > 5 questions need confirmation
    if (!smoke && n_questions > 5 && !form.confirm_cost) {
      setConfirmShow(true);
      return;
    }

    launchRun();
  };

  const launchRun = async () => {
    setSubmitting(true);
    try {
      const res = await runsAPI.createRun({
        dataset: form.dataset,
        n_questions: form.n_questions,
        strategies: form.strategies,
        seed: 0,
        smoke: form.smoke,
        confirm_cost: form.confirm_cost,
      });
      setLaunchedRun(res.data.run_id);
    } catch (e: any) {
      setSubmitError(e.response?.data?.detail || e.message || 'Failed to launch experiment');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-gray-500">Loading configuration…</p>
      </div>
    );
  }

  if (submitError) {
    return (
      <div className="p-6 rounded-lg bg-red-50 border border-red-200 text-red-700">
        <AlertTriangle className="w-5 h-5 mr-2" /> {submitError}
      </div>
    );
  }

  const dsMeta = meta?.datasets?.includes(form.dataset) ? true : false;
  const isSmoke = form.smoke;
  const isLargeRun = !isSmoke && form.n_questions > 5;
  const budgetList = BUDGETS[form.dataset] || [];
  const abslTiers = abslTierBudgets[form.dataset] || [];

  const prices = meta?.prices || { input_per_mtok: 0, output_per_mtok: 0 };
  const hasPrices = prices.input_per_mtok > 0 || prices.output_per_mtok > 0;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Experiment Runner</h1>
        <p className="text-gray-600 mt-1">
          Configure and launch a benchmark experiment using the existing
          schemalink pipeline.
        </p>
      </div>

      {/* Config form */}
      <form onSubmit={handleSubmit} className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Dataset */}
          <div className="space-y-2">
            <label className="font-medium text-gray-700">Dataset</label>
            <select
              value={form.dataset}
              onChange={e => setForm({ ...form, dataset: e.target.value, strategies: form.strategies.filter(s => s !== 'absl') })}
              className="w-full p-2 border border-gray-300 rounded bg-gray-50"
            >
              {datasets.map(d => (
                <option key={d.name} value={d.name} disabled={!d.available}>
                  {d.name.toUpperCase()} {d.available ? '' : ' — not available'}
                </option>
              ))}
            </select>
            {!dsMeta && (
              <p className="text-xs text-red-600">Dataset files are missing. Download the dataset before running.</p>
            )}
          </div>

          {/* Demo Mode */}
          <div className="space-y-2">
            <label className="font-medium text-gray-700">Run Mode</label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setForm({ ...form, demoMode: true, smoke: true, n_questions: 5 })}
                className={`flex flex-col items-center gap-2 p-4 rounded border ${
                  form.demoMode
                    ? 'bg-primary-50 border-primary-200 text-primary-700'
                    : 'bg-gray-50 border-gray-200 text-gray-600 hover:border-gray-300'
                }`}
              >
                <Play className="w-5 h-5" />
                <span className="text-sm font-medium">Demo Mode</span>
                <span className="text-xs text-gray-500">5 questions</span>
              </button>
              <button
                type="button"
                onClick={() => setForm({ ...form, demoMode: false, smoke: false })}
                className={`flex flex-col items-center gap-2 p-4 rounded border ${
                  !form.demoMode && !form.smoke
                    ? 'bg-primary-50 border-primary-200 text-primary-700'
                    : 'bg-gray-50 border-gray-200 text-gray-600 hover:border-gray-300'
                }`}
              >
                <Server className="w-5 h-5" />
                <span className="text-sm font-medium">Custom Run</span>
                <span className="text-xs text-gray-500">Your question count</span>
              </button>
            </div>
            <p className="text-xs text-gray-500">
              Demo mode runs exactly 5 questions using the smoke configuration
              (first 2 budgets only).
            </p>
          </div>

          {/* Question count (custom mode only) */}
          {!form.demoMode && (
            <div className="space-y-2">
              <label className="flex items-center justify-between font-medium text-gray-700">
                Number of questions
                <span className="text-xs text-gray-500">{form.n_questions}</span>
              </label>
              <input
                type="number"
                value={form.n_questions}
                onChange={e => setForm({ ...form, n_questions: parseInt(e.target.value) || 1, demoMode: false })}
                min="1" max="150"
                className="w-full p-2 border border-gray-300 rounded bg-gray-50"
              />
              <p className="text-xs text-gray-500">
                Safe default: 5 (smoke). {isLargeRun && 'Larger runs make external LLM calls and may incur API costs.'}
              </p>
            </div>
          )}

          {/* Smoke toggle (custom mode only) */}
          {!form.demoMode && (
            <div className="space-y-2">
              <label className="font-medium text-gray-700">Smoke Mode</label>
              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  checked={form.smoke}
                  onChange={e => setForm({ ...form, smoke: e.target.checked, demoMode: !e.target.checked })}
                  className="rounded border-gray-300 text-primary-600"
                />
                <span className="text-sm text-gray-700">
                  {form.smoke ? 'Smoke test (5 questions, 2 budgets)' : 'Full run'}
                </span>
              </div>
              {form.smoke && (
                <p className="text-xs text-gray-500">Uses smoke_n=5 and first 2 budgets only.</p>
              )}
            </div>
          )}
        </div>

        {/* Strategies */}
        <div className="space-y-2">
          <label className="font-medium text-gray-700">Strategies</label>
          <p className="text-xs text-gray-500 mb-3">S4 and S5 are not implemented and are not offered.</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {STRATEGIES.map(s => {
              const checked = form.strategies.includes(s.id);
              const disabled = s.id === 'absl' && !budgetList.includes(abslTiers[0]); // absl needs tiers
              return (
                <div key={s.id} className="flex items-center gap-3 p-3 rounded border hover:border-primary-300 transition-colors cursor-pointer">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => handleStrategyChange(s.id, !checked)}
                    disabled={disabled}
                    className="rounded border-gray-300 text-primary-600"
                  />
                  <div className="flex-1">
                    <div className="font-medium text-gray-900">{s.label}</div>
                    <span className={`text-xs uppercase tracking-wider px-2 py-0.5 rounded ${
                      s.type === 'baseline' ? 'bg-amber-100 text-amber-700' :
                      s.type === 'adaptive' ? 'bg-blue-100 text-blue-700' :
                      'bg-gray-100 text-gray-600'
                    }`}>
                      {s.type}
                    </span>
                  </div>
                  {disabled && (
                    <span className="text-xs text-red-500">requires ABSL tiers</span>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Budgets info */}
        <div className="space-y-2">
          <label className="font-medium text-gray-700">Configured schema-token budgets</label>
          <div className="flex flex-wrap gap-2">
            {budgetList.map(b => (
              <span key={b} className="px-3 py-1.5 rounded bg-gray-100 text-gray-700 text-sm font-mono border border-gray-200">
                B={b}
              </span>
            ))}
          </div>
          {form.strategies.includes('absl') && (
            <div className="mt-2 p-3 rounded bg-blue-50 border border-blue-200">
              <div className="text-xs font-medium text-blue-800 mb-1">ABSL tier budgets ({form.dataset.toUpperCase()}):</div>
              <div className="text-xs text-blue-700 font-mono">{abslTiers.join(' → ')}</div>
            </div>
          )}
        </div>

        {/* Model & cost */}
        <div className="space-y-2 p-4 rounded bg-gray-50 border border-gray-200">
          <div className="flex items-center gap-2 mb-2">
            <Server className="w-4 h-4 text-gray-500" />
            <span className="font-medium text-gray-700">Model configuration</span>
          </div>
          <div className="text-sm text-gray-600 space-y-1">
            <div>Model: <span className="font-mono text-xs">{meta?.model || 'Not configured'}</span></div>
            <div>Prices (USD per 1M tokens): input {prices.input_per_mtok}, output {prices.output_per_mtok}</div>
            {!hasPrices && (
              <div className="text-xs text-amber-600">⚠ Prices are unset — cost_per_1k_queries will be reported as 0. This does not mean the experiment is free.</div>
            )}
          </div>
        </div>

        {/* External API warning */}
        <div className="p-4 rounded-lg bg-amber-50 border border-amber-200">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-600 mt-0.5" />
            <div>
              <div className="font-medium text-amber-800">External LLM calls will be made</div>
              <p className="text-sm text-amber-700 mt-1">
                Every experiment makes calls to an external LLM provider
                (OpenRouter by default). These calls can incur API costs.
                Credentials are read server-side only and never exposed to the browser.
              </p>
            </div>
          </div>
        </div>

        {/* Confirmation for large runs */}
        {confirmShow && (
          <div className="p-4 rounded-lg bg-amber-100 border border-amber-200">
            <div className="font-medium text-amber-900 mb-2">Confirm large run</div>
            <p className="text-sm text-amber-800 mb-3">
              You are about to launch a {form.n_questions}-question run on
              {form.dataset.toUpperCase()} without smoke mode. This will make
              external LLM calls and may incur API costs.
            </p>
            <div className="flex items-center gap-3 mb-3">
              <input
                type="checkbox"
                checked={form.confirm_cost}
                onChange={e => setForm({ ...form, confirm_cost: e.target.checked })}
                className="rounded border-amber-300 text-amber-600"
              />
              <span className="text-sm text-amber-800">I confirm I understand this run may incur API costs.</span>
            </div>
            <button
              type="button"
              onClick={launchRun}
              disabled={submitting}
              className="px-4 py-2 bg-amber-600 text-white rounded hover:bg-amber-700 disabled:opacity-50"
            >
              {submitting ? 'Launching…' : 'Confirm and Launch'}
            </button>
          </div>
        )}

        {/* Action buttons */}
        <div className="flex gap-3">
          <button
            type="submit"
            disabled={submitting || !dsMeta || !form.strategies.length}
            className="px-6 py-3 bg-primary-600 text-white rounded-lg font-medium hover:bg-primary-700 disabled:opacity-50 flex items-center gap-2"
          >
            {submitting ? (
              <>
                <Server className="w-4 h-4 animate-spin" />
                Launching…
              </>
            ) : (
              <>
                <Play className="w-4 h-4" />
                Launch Experiment
              </>
            )}
          </button>
          {!isSmoke && form.n_questions <= 5 && (
            <button
              type="button"
              onClick={() => setForm({ ...form, smoke: true })}
              className="px-6 py-3 border border-gray-300 text-gray-700 rounded-lg font-medium hover:bg-gray-50"
            >
              Switch to smoke mode (5 questions)
            </button>
          )}
        </div>
      </form>

      {/* Launched run */}
      {launchedRun && (
        <div className="bg-green-50 border border-green-200 rounded-xl p-6">
          <div className="flex items-center gap-3 mb-2">
            <CheckCircle2 className="w-5 h-5 text-green-600" />
            <span className="font-medium text-gray-900">Experiment launched</span>
          </div>
          <p className="text-sm text-gray-700 mb-3">
            Run ID: <span className="font-mono">{launchedRun}</span>. View progress in the
            <span className="font-medium text-primary-600 cursor-pointer" onClick={() => navigate(`/monitor/${launchedRun}`)}>Execution Monitor</span>.
          </p>
          <p className="text-xs text-gray-500">
            Results will be written to an isolated run directory. Previous
            results are not affected.
          </p>
        </div>
      )}
    </div>
  );
}
