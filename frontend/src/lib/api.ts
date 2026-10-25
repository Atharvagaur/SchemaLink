import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  // Fail fast so loading states resolve to an error instead of hanging forever
  // when the backend is unreachable.
  timeout: 10000,
});

export interface HealthResponse {
  status: string;
  python_version: string;
  project_root: string;
  runs_dir: string;
  llm_key_configured: boolean;
  llm_model: string | null;
}

export interface MetaResponse {
  project_name: string;
  description: string;
  strategies: string[];
  strategies_info: Record<string, any>;
  budgets: Record<string, number[]>;
  model: string | null;
  llm_key_configured: boolean;
  base_url: string;
  prices: { input_per_mtok: number; output_per_mtok: number };
  datasets: string[];
}

export interface DatasetInfo {
  name: string;
  dev_exists: boolean;
  tables_exists: boolean;
  db_root_exists: boolean;
  available: boolean;
  dev_path?: string;
  db_root?: string;
}

export interface DatasetsResponse {
  datasets: DatasetInfo[];
}

export interface QuestionPreview {
  index: number;
  question_id: string | number;
  question: string;
  evidence: string;
  db_id: string;
}

export interface QuestionsResponse {
  dataset: string;
  total: number;
  questions: QuestionPreview[];
}

export interface RunRequest {
  dataset: string;
  n_questions: number;
  strategies: string[];
  seed?: number;
  smoke?: boolean;
  confirm_cost?: boolean;
}

export interface RunMeta {
  run_id: string;
  dataset: string;
  n_questions: number;
  strategies: string[];
  status: 'queued' | 'running' | 'completed' | 'failed';
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  model: string | null;
  error?: string;
  smoke?: boolean;
  seed?: number;
}

export interface RunSummary {
  dataset: string;
  strategy: string;
  budget: number | null;
  n: number;
  ex: number;
  ex_ci_lo: number;
  ex_ci_hi: number;
  avg_schema_tokens: number;
  avg_prompt_tokens: number;
  avg_completion_tokens: number;
  cost_per_1k_queries: number;
  bes: number;
  p50_latency_s: number;
  p95_latency_s: number;
  escalation_rate: number;
  budget_violations: number;
  avg_schema_recall: number | string;
  avg_schema_precision: number | string;
}

export interface RunSummaryResponse {
  run_id: string;
  summary: {
    aubc: Record<string, number>;
    error_taxonomy: Record<string, Record<string, number>>;
    plots: string[];
    n_traces: number;
    prices_usd_per_mtok: { input_per_mtok: number; output_per_mtok: number };
    cost_per_1k_queries: number;
  };
  rows: RunSummary[];
}

export interface LLMConfig {
  provider: string | null;
  model: string | null;
  base_url: string | null;
  configured: boolean;
  tested: boolean;
  test_status: string | null;
  test_error: string | null;
}

export interface LLMConfigRequest {
  provider: string;
  api_key: string;
  base_url: string;
  model: string;
}

export interface TestConnectionRequest {
  provider: string;
  api_key: string;
  base_url: string;
  model: string;
}

export interface TestConnectionResponse {
  ok: boolean;
  status: string;
  error?: string;
}

export const llmConfigAPI = {
  getConfig: () => api.get<LLMConfig>('/api/llm/config'),
  saveConfig: (data: LLMConfigRequest) => api.post<{ ok: boolean; message: string }>('/api/llm/config', data),
  // The connection test calls out to an external LLM provider and can take
  // longer than the default Axios timeout. Give it its own 60s timeout so a
  // slow (but valid) provider is not mistaken for a broken connection, while
  // ordinary dashboard requests keep the 10s fail-fast timeout.
  testConnection: (data: TestConnectionRequest) =>
    api.post<TestConnectionResponse>('/api/llm/test', data, { timeout: 60000 }),
  clearConfig: () => api.delete<{ ok: boolean; message: string }>('/api/llm/config'),
};

export const healthAPI = {
  getHealth: () => api.get<HealthResponse>('/api/health'),
};

export const metaAPI = {
  getMeta: () => api.get<MetaResponse>('/api/meta'),
};

export const datasetsAPI = {
  getDatasets: () => api.get<DatasetsResponse>('/api/datasets'),
  getDatabases: (dataset: string) => api.get<{ dataset: string; databases: string[] }>('/api/databases', { params: { dataset } }),
};

export const questionsAPI = {
  getQuestions: (dataset: string, limit: number) =>
    api.get<QuestionsResponse>('/api/questions', { params: { dataset, limit } }),
};

export const runsAPI = {
  createRun: (data: RunRequest) =>
    api.post<{ run_id: string; status: string; message: string }>('/api/runs', data),
  listRuns: () => api.get<{ runs: RunMeta[] }>('/api/runs'),
  getRun: (runId: string) => api.get<RunMeta>(`/api/runs/${runId}`),
  getSummary: (runId: string) => api.get<RunSummaryResponse>(`/api/runs/${runId}/summary`),
};
