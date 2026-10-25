import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { metaAPI, datasetsAPI, healthAPI, llmConfigAPI, type MetaResponse, type HealthResponse, type DatasetInfo, type LLMConfig } from '@/lib/api';
import { AlertCircle } from 'lucide-react';

const STRATEGIES_INFO = [
  {
    id: 's0',
    name: 'S0 — Full Schema Baseline',
    type: 'baseline',
    description: 'Returns the entire database schema without any truncation. Budget is effectively infinite. Serves as the reference point showing maximum possible execution accuracy with no schema budget constraint.',
  },
  {
    id: 's1',
    name: 'S1 — BM25 Ranking',
    type: 'fixed_budget',
    description: 'Lexical keyword-based ranking of schema elements using BM25. Elements most relevant to the question text are selected first, then truncated to fit the schema-token budget B.',
  },
  {
    id: 's2',
    name: 'S2 — Dense Embedding Ranking',
    type: 'fixed_budget',
    description: 'Dense vector similarity ranking using sentence-transformer embeddings (BAAI/bge-small-en-v1.5). Schema elements are ranked by cosine similarity to the question+evidence text, then truncated to budget B.',
  },
  {
    id: 's3',
    name: 'S3 — Hybrid RRF',
    type: 'fixed_budget',
    description: 'Reciprocal-rank fusion of S1 (BM25) and S2 (dense) rankings. Combines lexical and semantic signals into a single ranked list, then truncated to budget B.',
  },
  {
    id: 's6',
    name: 'S6 — Hybrid + Value Reordering',
    type: 'fixed_budget',
    description: 'S3 ranking augmented with value-index reordering. Columns containing values mentioned in the question (literals, numerals, capitalized tokens) are promoted to the front, then truncated to budget B.',
  },
  {
    id: 'absl',
    name: 'ABSL — Adaptive Budget Cascade',
    type: 'adaptive',
    description: 'Adaptive budgeted schema linking. Starts at a predicted tier and cascades upward through tier budgets (Spider: 500/1500/4000; BIRD: 1000/4000/16000). At each tier, generates n=3 candidates and uses self-consistency voting by execution-result equivalence. Escalates on failure (exec error, 0 rows, or agreement < 0.67).',
  },
];

export default function Overview() {
  const navigate = useNavigate();
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [llmConfig, setLlmConfig] = useState<LLMConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      metaAPI.getMeta().then((r) => setMeta(r.data)).catch(() => setError('Failed to load meta')),
      datasetsAPI.getDatasets().then((r) => setDatasets(r.data.datasets)).catch(() => {}),
      healthAPI.getHealth().then((r) => setHealth(r.data)).catch(() => {}),
      llmConfigAPI.getConfig().then((r) => setLlmConfig(r.data)).catch(() => {}),
    ]).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-4 border-primary-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-500">Loading environment…</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 text-red-600 bg-red-50 border border-red-200 rounded">
        Failed to load environment: {error}
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">SchemaLink</h1>
          <h2 className="mt-2 text-lg text-gray-600">Budget-Aware Schema Linking for Text-to-SQL</h2>
          <p className="mt-4 text-gray-700 max-w-2xl">
            How can schema-linking strategies reduce the schema context supplied
            to an LLM while retaining execution accuracy? SchemaLink benchmarks
            six linking strategies (S0, S1, S2, S3, S6, ABSL) under one frozen
            SQL generator, measuring execution accuracy versus schema-token
            budget.
          </p>
        </div>
        <div className="lg:w-80">
          <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
            <h3 className="font-semibold text-gray-900 mb-4">Environment Status</h3>
            <div className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600">Datasets available</span>
                <span className="font-medium">
                  {datasets.filter(d => d.available).length}/{datasets.length}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600">LLM model</span>
                <span className="font-medium truncate max-w-xs">
                  {health?.llm_model || meta?.model || 'Not configured'}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600">Credentials configured</span>
                <span className={`font-medium ${health?.llm_key_configured || meta?.llm_key_configured ? 'text-green-600' : 'text-amber-600'}`}>
                  {health?.llm_key_configured || meta?.llm_key_configured ? 'Yes' : 'No'}
                </span>
              </div>
              {llmConfig && (
                <div className="flex items-center justify-between text-sm">
                  <span className="text-gray-600">LLM connection</span>
                  <span className={`font-medium ${
                    llmConfig.test_status === 'ok'
                      ? 'text-green-600'
                      : llmConfig.test_status === 'failed'
                        ? 'text-red-600'
                        : 'text-amber-600'
                  }`}>
                    {llmConfig.test_status === 'ok' ? 'Tested OK' :
                     llmConfig.test_status === 'failed' ? 'Test failed' :
                     llmConfig.configured ? 'Saved, not tested' : 'Not configured'}
                  </span>
                </div>
              )}
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600">API base URL</span>
                <span className="font-medium text-xs truncate">
                  {llmConfig?.base_url || meta?.base_url || health?.project_root}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {llmConfig && !llmConfig.configured && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-5">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-amber-600 mt-0.5" />
            <div>
              <div className="font-medium text-amber-900">LLM not configured</div>
              <p className="text-sm text-amber-800 mt-1">
                No LLM API key or model has been set. Experiments require an
                LLM provider. Configure it in{' '}
                <button
                  onClick={() => navigate('/llm-settings')}
                  className="text-amber-700 underline hover:text-amber-900"
                >
                  LLM Settings
                </button>
                , or set the LLM_API_KEY and GENERATOR_MODEL_SLUG environment
                variables.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Pipeline */}
      <section>
        <h3 className="text-xl font-bold text-gray-900 mb-2">Research Pipeline</h3>
        <p className="text-gray-600 mb-6">
          Each benchmark question flows through the same fixed pipeline; only
          the schema-linking step changes per strategy.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { n: '1', title: 'Load question & schema', desc: 'Read a Spider/BIRD dev question and its database schema metadata.' },
            { n: '2', title: 'Index schema elements', desc: 'Extract tables, columns, types, PK/FK relationships and sample values into a per-database index.' },
            { n: '3', title: 'Rank by linker', desc: 'Apply the selected strategy (S0–S6, ABSL) to rank schema elements by relevance.' },
            { n: '4', title: 'Enforce token budget', desc: 'Truncate the ranked schema to fit the configured schema-token budget B (S0 ignores budget).' },
            { n: '5', title: 'Generate SQL', desc: 'The frozen prompt + LLM generator produces a SQL query from the selected schema.' },
            { n: '6', title: 'Execute read-only', desc: 'Run predicted SQL on a read-only copy of the SQLite database (30s timeout).' },
            { n: '7', title: 'Evaluate accuracy', desc: 'Compare predicted vs gold result sets (execution accuracy, multiset match).' },
            { n: '8', title: 'Aggregate results', desc: 'Compute EX, CI, tokens, latency, BES, AUBC, recall/precision per strategy and budget.' },
          ].map((step) => (
            <div key={step.n} className="bg-white p-5 rounded-xl border border-gray-200">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-8 h-8 rounded-full bg-primary-100 text-primary-600 flex items-center justify-center font-bold text-sm">
                  {step.n}
                </div>
                <h4 className="font-semibold text-gray-900">{step.title}</h4>
              </div>
              <p className="text-sm text-gray-600 leading-relaxed">{step.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Strategies */}
      <section>
        <h3 className="text-xl font-bold text-gray-900 mb-4">Implemented Strategies</h3>
        <p className="text-gray-600 mb-6">
          Six linkers are implemented. S4 and S5 from the paper's ablation
          ladder are <strong>not</strong> implemented and are not available.
        </p>
        <div className="space-y-3">
          {STRATEGIES_INFO.map(s => (
            <div key={s.id} className={`p-5 rounded-xl border shadow-sm ${
              s.type === 'baseline' ? 'bg-amber-50 border-amber-200' :
              s.type === 'adaptive' ? 'bg-blue-50 border-blue-200' :
              'bg-white border-gray-200'
            }`}>
              <div className="flex items-start gap-3">
                <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white font-bold text-xs shrink-0 mt-0.5 ${
                  s.type === 'baseline' ? 'bg-amber-500' :
                  s.type === 'adaptive' ? 'bg-blue-600' :
                  'bg-gray-500'
                }`}>
                  {s.id.toUpperCase()}
                </div>
                <div className="flex-1">
                  <div className="flex items-baseline gap-2 mb-1">
                    <h4 className="font-bold text-gray-900">{s.name}</h4>
<span className="text-xs font-medium px-2 py-0.5 rounded bg-gray-100 text-gray-600">
  {s.type}
</span>
                  </div>
                  <p className="text-sm text-gray-700">{s.description}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-6 p-5 rounded-lg bg-blue-50 border border-blue-200">
          <h4 className="font-bold text-blue-900 mb-2">ABSL Cascade</h4>
          <p className="text-sm text-blue-800 mb-3">
            ABSL uses an adaptive three-tier cascade. At each tier it generates
            n=3 SQL candidates at temperature 0.5 and votes by execution-result
equivalence. Escalation triggers when: majority SQL fails to execute,
              majority returns 0 rows, or agreement &lt; 0.67. The failure reason is
            appended to the next tier's prompt.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
            {[['Spider', '500 → 1500 → 4000'], ['BIRD', '1000 → 4000 → 16000']].map(([ds, tiers]) => (
              <div key={ds} className="bg-white/50 p-3 rounded border border-blue-200/50">
                <div className="font-bold text-blue-800">{ds}</div>
                <div className="text-blue-700">Tier budgets: {tiers}</div>
              </div>
            ))}
            <div className="bg-white/50 p-3 rounded border border-blue-200/50">
              <div className="font-bold text-blue-800">Voting</div>
              <div className="text-blue-700">Result-equivalence</div>
            </div>
          </div>
        </div>
      </section>

      {/* Dataset cards */}
      <section>
        <h3 className="text-xl font-bold text-gray-900 mb-4">Datasets</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {datasets.map(ds => (
            <div key={ds.name} className="p-5 rounded-xl border shadow-sm hover:shadow-md transition-shadow cursor-pointer" onClick={() => navigate('/runner')}>
              <div className="flex items-center justify-between mb-3">
                <span className="font-bold text-gray-900 capitalize">{ds.name}</span>
                <span className={`px-2 py-1 rounded text-xs font-medium ${
                  ds.available ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                }`}>
                  {ds.available ? 'Available' : 'Missing'}
                </span>
              </div>
              <div className="space-y-2 text-sm text-gray-600">
                <div className="flex justify-between">
                  <span>Dev file:</span> <span className="font-mono text-xs">{ds.dev_exists ? '✓' : '✗'}</span>
                </div>
                <div className="flex justify-between">
                  <span>Tables:</span> <span className="font-mono text-xs">{ds.tables_exists ? '✓' : '✗'}</span>
                </div>
                <div className="flex justify-between">
                  <span>DB root:</span> <span className="font-mono text-xs">{ds.db_root_exists ? '✓' : '✗'}</span>
                </div>
              </div>
              {!ds.available && (
                <p className="mt-3 text-xs text-red-600">Download dataset to run experiments.</p>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Call to action */}
      <section className="p-6 rounded-xl border border-gray-200 bg-white">
        <h3 className="font-bold text-gray-900 mb-3">Ready to run an experiment?</h3>
        <p className="text-gray-600 mb-4">
          Start with a safe 5-question smoke test. Real benchmark runs make
          external LLM calls and can incur API costs.
        </p>
        <button
          onClick={() => navigate('/runner')}
          className="px-6 py-3 bg-primary-600 text-white rounded-lg font-medium hover:bg-primary-700 transition"
        >
          Go to Experiment Runner
        </button>
      </section>
    </div>
  );
}

