import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Clock, RefreshCw } from 'lucide-react';
import { runsAPI } from '@/lib/api';

const STATUS_COLORS: Record<string, string> = {
  queued: 'bg-blue-50 text-blue-700 border-blue-200',
  running: 'bg-amber-50 text-amber-700 border-amber-200',
  completed: 'bg-green-50 text-green-700 border-green-200',
  failed: 'bg-red-50 text-red-700 border-red-200',
};

export default function ExecutionMonitor() {
  const { runId } = useParams();
  const navigate = useNavigate();
const [run, setRun] = useState<any>(null);
const [loading, setLoading] = useState(true);
const [polling, setPolling] = useState(true);
const [error, setError] = useState<string | null>(null);

  const fetchRun = async () => {
    if (!runId) {
      setLoading(false);
      setRun(null);
      setError('No run ID provided. Open a run from the Experiment Runner to view its progress.');
      return;
    }
  try {
    const res = await runsAPI.getRun(runId);
    setRun(res.data);
    setError(null);
  } catch (e: any) {
    setRun(null);
    // A 404 is handled by the "Run not found." empty state below, so only set
    // an error message for non-404 failures (network, server, etc.).
    if (e.response?.status !== 404) {
      setError(e.response?.data?.detail || e.message || 'Failed to load run.');
    }
  } finally {
    setLoading(false);
  }
  };

  useEffect(() => {
    fetchRun();

    const interval = setInterval(() => {
      if (!polling) return;
      if (run?.status === 'completed' || run?.status === 'failed') {
        setPolling(false);
        return;
      }
      fetchRun();
    }, 3000);

    return () => clearInterval(interval);
  }, [runId, polling, run]);

  const statusColor = run?.status ? STATUS_COLORS[run.status] : 'bg-gray-50 text-gray-700 border-gray-200';
  const isTerminal = run?.status === 'completed' || run?.status === 'failed';

  const elapsed = run?.started_at && run?.completed_at
    ? Math.round((new Date(run.completed_at).getTime() - new Date(run.started_at).getTime()) / 1000)
    : run?.started_at
      ? Math.round((Date.now() - new Date(run.started_at).getTime()) / 1000)
      : null;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Execution Monitor</h1>
        {runId ? (
          <p className="text-gray-600 mt-1">
            Run ID: <span className="font-mono text-sm">{runId}</span>
          </p>
        ) : (
          <p className="text-gray-600 mt-1">
            Run ID: <span className="font-mono text-sm text-red-600">not provided</span>
          </p>
        )}
      </div>

      {error ? (
        <div className="p-4 rounded-lg bg-red-50 border border-red-200 text-red-700">
          {error}
        </div>
      ) : loading ? (
        <div className="flex h-64 items-center justify-center">
          <p className="text-gray-500">Loading run…</p>
        </div>
      ) : !run ? (
        runId ? (
          <div className="p-6 rounded-lg bg-red-50 border border-red-200 text-red-700">
            Run not found.
          </div>
        ) : null
      ) : (
        <>
          {/* Status card */}
          <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
            <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6">
              <div>
                <div className="flex items-center gap-3 mb-4">
                  <span className={`px-3 py-1 rounded-full text-xs font-medium border ${statusColor}`}>
                    {run.status.charAt(0).toUpperCase() + run.status.slice(1)}
                  </span>
                  {isTerminal && (
                    <span className="text-xs text-gray-500">
                      {run.completed_at ? `Completed at ${new Date(run.completed_at).toLocaleString()}` : ''}
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  <div>
                    <div className="text-gray-500 text-xs uppercase tracking-wider">Dataset</div>
                    <div className="font-medium text-gray-900">{run.dataset}</div>
                  </div>
                  <div>
                    <div className="text-gray-500 text-xs uppercase tracking-wider">Questions</div>
                    <div className="font-medium text-gray-900">{run.n_questions}</div>
                  </div>
                  <div>
                    <div className="text-gray-500 text-xs uppercase tracking-wider">Strategies</div>
                    <div className="font-medium text-gray-900">{run.strategies.join(', ')}</div>
                  </div>
                  <div>
                    <div className="text-gray-500 text-xs uppercase tracking-wider">Mode</div>
                    <div className="font-medium text-gray-900">{run.smoke ? 'Smoke' : 'Full'}</div>
                  </div>
                </div>
              </div>
              <div className="w-full lg:w-auto">
                <div className="flex items-center gap-3 mb-2">
                  <Clock className="w-4 h-4 text-gray-500" />
                  <span className="text-gray-600">
                    {elapsed !== null ? `Elapsed: ${elapsed}s` : 'Not started'}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {run.started_at && (
                    <span className="text-xs text-gray-500">
                      Started: {new Date(run.started_at).toLocaleString()}
                    </span>
                  )}
                  {run.completed_at && (
                    <span className="ml-3 text-xs text-gray-500">
                      Finished: {new Date(run.completed_at).toLocaleString()}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Progress note */}
          <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
            <h3 className="font-semibold text-gray-900 mb-3">Execution Details</h3>
            <p className="text-gray-600 text-sm mb-4">
              The existing CLI does not expose per-question progress, so
              percentage completion and remaining time cannot be displayed.
              Polling status every 3 seconds. Polling stops automatically when
              the run reaches a terminal state.
            </p>
            {run.error && (
              <div className="p-4 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
                <div className="font-medium mb-1">Error:</div>
                <div className="font-mono text-xs break-all">{run.error}</div>
              </div>
            )}
            {isTerminal && run.status === 'completed' && (
              <div className="flex gap-3">
                <button
                  onClick={() => navigate(`/results/${runId}`)}
                  className="px-5 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700"
                >
                  View Results
                </button>
                <button
                  onClick={() => navigate(`/traces/${runId}`)}
                  className="px-5 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50"
                >
                  View Traces
                </button>
              </div>
            )}
            {!isTerminal && (
              <button
                onClick={fetchRun}
                className="px-5 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 flex items-center gap-2"
              >
                <RefreshCw className="w-4 h-4" /> Refresh
              </button>
            )}
          </div>

          {/* Logs */}
          <div className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm">
            <h3 className="font-semibold text-gray-900 mb-3">Process Logs</h3>
            <p className="text-gray-600 text-sm mb-4">
              Logs are captured from the subprocess. Real-time log streaming is
              not available; logs are read after the process completes or on
              refresh.
            </p>
            <div className="bg-gray-900 rounded-lg p-4 overflow-x-auto font-mono text-xs text-gray-300 max-h-96 overflow-y-auto">
              {run.status !== 'queued' && run.status !== 'running'
                ? 'Logs will appear here after the experiment completes.'
                : 'Experiment is running. Refresh to see updated logs.'}
            </div>
          </div>

          {/* What happens next */}
          <div className="bg-blue-50 border border-blue-200 rounded-xl p-6">
            <h3 className="font-semibold text-blue-900 mb-3">What happens next</h3>
            <ul className="text-sm text-blue-800 space-y-2 list-disc pl-5">
              <li>The experiment runs in an isolated subprocess using <code className="font-mono">python -m schemalink.run</code>.</li>
              <li>Each run writes to its own directory under <code className="font-mono">app_runs/&lt;run_id&gt;/</code>.</li>
              <li>When complete, results are parsed from <code className="font-mono">traces.jsonl</code> and <code className="font-mono">summary.json</code>.</li>
              <li>No results are shown until the process finishes successfully.</li>
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
