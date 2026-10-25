# SchemaLink Web Interface — Documentation

SchemaLink is a research system for budget-aware schema linking in Text-to-SQL.
This document describes how to install, start, and use the web interface that
wraps the existing `schemalink` research pipeline.

## Architecture

- **Backend**: FastAPI (Python) at `backend/app/main.py`
  - Wraps `python -m schemalink.run` as an isolated subprocess
  - Each experiment writes to a unique directory under `app_runs/<run_id>/`
  - Never modifies the original `config.yaml`
  - LLM API keys are read server-side only, never exposed to the browser
- **Frontend**: React + TypeScript + Vite + Tailwind at `frontend/`
  - 8 pages: Overview, Experiment Runner, Execution Monitor, Results Dashboard,
    Trace Explorer, Schema Explorer, ABSL Inspector, Failure Analysis
  - Charts via Recharts; icons via Lucide React

## Prerequisites

- Python 3.10 or 3.11
- Node.js 18+ and npm
- The existing `schemalink` package and its dependencies (`requirements.txt`)
- Spider and/or BIRD dataset files (see `README.md` for download links)

## Installation

### Backend dependencies

```powershell
pip install -r requirements.txt
pip install fastapi uvicorn pydantic pydantic-settings aiofiles python-multipart httpx pytest
```

### Frontend dependencies

```powershell
cd frontend
npm install
```

## Environment Variables

Create a `.env` file in the project root (or export these variables):

| Variable | Description | Required |
|----------|-------------|----------|
| `LLM_API_KEY` | OpenRouter / LLM API key | Yes, for experiments |
| `GENERATOR_MODEL_SLUG` | Model slug (overrides config.yaml) | Optional |

**Never commit `.env` or share your API key.** The key is read only on the
server; the frontend only sees a boolean "configured" status.

## Starting the Application

### Terminal 1 — Backend (FastAPI)

```powershell
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API is available at `http://localhost:8000`. Interactive docs at
`http://localhost:8000/docs`.

### Terminal 2 — Frontend (Vite dev server)

```powershell
cd frontend
npm run dev
```

The frontend is available at `http://localhost:5173` by default.

### Terminal 3 — Run both concurrently (optional)

Use two terminals as above, or a process manager.

## Running a Safe Smoke Experiment

1. Set `LLM_API_KEY` and optionally `GENERATOR_MODEL_SLUG`.
2. Open the frontend at `http://localhost:5173`.
3. Navigate to **Experiment Runner**.
4. Select dataset `spider` (or `bird` if available).
5. Leave **Smoke test** enabled (5 questions, 2 budgets).
6. Select strategies (e.g., `s0`, `s1`, `s3`).
7. Click **Launch Experiment**.
8. The run is dispatched to an isolated subprocess. Open **Execution Monitor**
   to track status.
9. When complete, open **Results Dashboard** to view metrics and charts.

> **Warning**: Non-smoke runs make external LLM API calls and can incur costs.
> Always review the confirmation dialog before launching large runs.

## Where Outputs Are Stored

- Run metadata and logs: `app_runs/<run_id>/`
- Experiment results (traces, summary, plots): `app_runs/<run_id>/results/<dataset>/`
- Original results (untouched): `results/<dataset>/`
- Index cache: `cache/`
- Exemplars: `exemplars.json`

Each run is fully isolated. Previous results are never overwritten.

## Pages Overview

| Page | Path | Works Without LLM Key? | Description |
|------|------|----------------------|-------------|
| Overview | `/` | Yes | Research explanation, pipeline, strategy descriptions, environment status |
| Experiment Runner | `/runner` | No (for launching) | Configure and launch benchmark experiments |
| Execution Monitor | `/monitor/:runId` | Yes | Track run status, view logs |
| Results Dashboard | `/results/:runId` | Yes (if results exist) | Metrics, charts, strategy comparison |
| Trace Explorer | `/traces/:runId` | Yes (if traces exist) | Searchable trace table, SQL diff view |
| Schema Explorer | `/schema-explorer` | Yes | Read-only database schema browser |
| ABSL Inspector | `/absl/:runId` | Yes (if ABSL traces exist) | ABSL cascade tier-by-tier analysis |
| Failure Analysis | `/failures/:runId` | Yes (if failures exist) | Error taxonomy and failure reports |

## Metric Definitions

All metrics are computed by the existing `schemalink.report` module:

- **EX (Execution Accuracy)**: Exact match of predicted vs gold result sets
- **Bootstrap CI**: 95% confidence interval from 1000 resamples
- **Avg Schema Tokens**: Tokens of serialised schema text only (tiktoken o200k_base)
- **Avg Prompt/Completion Tokens**: API-returned usage per query
- **Latency p50/p95**: Query execution latency percentiles
- **Schema Recall/Precision**: Selected columns vs gold columns from SQL
- **Budget Violation Rate**: Count of queries exceeding budget without violation flag
- **ABSL Escalation Rate**: Fraction of questions that escalated tiers
- **BES (Budget Efficiency Score)**: EX / (prompt tokens per 1000)
- **AUBC**: Area under EX vs log2(budget) curve, normalised to [0,1]
  - Computed only for S1, S2, S3, S6
- **Cost per 1k Queries**: USD estimate using configured prices (0 when unset)

## Strategies

| ID | Name | Budget Behavior |
|----|------|----------------|
| S0 | Full Schema | Budget = ∞ (baseline, no truncation) |
| S1 | BM25 | Rank, truncate to B |
| S2 | Dense Embedding | Rank, truncate to B |
| S3 | Hybrid RRF | Rank, truncate to B |
| S6 | Hybrid + Value | Rank + value reorder, truncate to B |
| ABSL | Adaptive Cascade | Tier budgets, self-consistency, escalate |

S4 and S5 are not implemented and are not available.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health and credential status |
| GET | `/api/meta` | Project capabilities, strategies, budgets, model |
| GET | `/api/datasets` | Dataset availability |
| GET | `/api/questions` | Preview benchmark questions |
| GET | `/api/schema` | Read-only schema metadata |
| POST | `/api/runs` | Launch an experiment |
| GET | `/api/runs` | List all runs |
| GET | `/api/runs/{id}` | Run status and metadata |
| GET | `/api/runs/{id}/summary` | Summary metrics and per-strategy rows |
| GET | `/api/runs/{id}/traces` | Paginated traces |
| GET | `/api/runs/{id}/traces/{trace_id}` | Individual trace detail |
| GET | `/api/runs/{id}/failures` | Failure analysis |

## Test Commands

```powershell
# Existing schemalink tests (73 tests)
python -m pytest schemalink/tests/ -q

# New backend API tests (24 tests)
python -m pytest backend/tests/ -q

# Frontend type check and production build
cd frontend
npx tsc --noEmit
npm run build
```

## Limitations

- The existing CLI does not expose per-question progress, so percentage
  completion and remaining time are not displayed.
- Real benchmark runs require `LLM_API_KEY` and a model slug; they make
  external LLM calls that can incur API costs.
- S4 and S5 strategies are not implemented.
- Dense embeddings require `sentence-transformers` and a model download;
  the system degrades gracefully to lexical-only if unavailable.
- No fabricated results are ever shown. If no experiment has been run,
  dashboards display honest empty states.

## Security

- API keys are read only from environment variables on the server
- The frontend never receives or displays the key value
- CORS is restricted to localhost development origins
- SQLite access is read-only (mode=ro URI)
- No arbitrary SQL execution or database write interface
- Each experiment runs in an isolated subprocess with a unique output directory