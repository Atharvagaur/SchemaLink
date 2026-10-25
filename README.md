# SchemaLink

**Budget-Aware Schema Linking for Text-to-SQL**

A research demonstration tool that benchmarks budget-aware schema-linking strategies for Text-to-SQL. It compares six linking strategies (S0–S6, ABSL) under one frozen SQL generator, measuring execution accuracy versus schema-token budget on Spider and BIRD datasets.

Run a safe 5-question smoke test, view real execution accuracy and metrics, and inspect results — without launching a full benchmark.

---

## Table of Contents

- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration (LLM Settings)](#configuration-llm-settings)
- [Running the Application](#running-the-application)
- [Running the 5-Question Demo](#running-the-5-question-demo)
- [Running Tests](#running-tests)
- [Pages Overview](#pages-overview)
- [Important Notes](#important-notes)

---

## Architecture

| Layer | Technology | Responsibility |
|-------|-----------|----------------|
| Frontend | React 19 + Vite + Tailwind CSS | UI pages, forms, navigation, polling |
| Backend | FastAPI (Python) | REST API, run lifecycle, subprocess management |
| Research package | `schemalink/` (Python) | Schema linking, budget enforcement, LLM generation, execution evaluation, report generation |
| Run storage | Local filesystem (`app_runs/`) | Per-run directories with `run_meta.json`, logs, and results |

The backend launches each experiment as a `subprocess.Popen` running `python -m schemalink.run`. Run metadata and results are written to isolated directories under `app_runs/<run_id>/`. The frontend polls the backend for run status.

---

## Project Structure

```
SchemaLink/
├── backend/
│   ├── app/
│   │   ├── api/routes/      # FastAPI route modules (health, meta, datasets, questions, runs, llm_config)
│   │   ├── core/config.py   # Pydantic settings (loads .env)
│   │   └── main.py          # FastAPI app entrypoint
│   └── tests/test_api.py    # Backend API tests
├── frontend/
│   ├── src/
│   │   ├── pages/           # React pages (Overview, Experiment Runner, Execution Monitor, Results Dashboard, LLM Settings)
│   │   ├── components/Layout.tsx  # Sidebar navigation
│   │   └── lib/api.ts       # Axios API client
│   ├── dist/                # Production build output
│   └── package.json
├── schemalink/
│   ├── run.py               # CLI experiment runner (python -m schemalink.run)
│   ├── config.py            # YAML config loader
│   ├── loaders.py           # Spider/BIRD dataset loaders
│   ├── indexer.py           # Schema element extraction (PK/FK normalization)
│   ├── budget.py            # Schema-token budget enforcer
│   ├── generator.py         # LLM SQL generator
│   ├── evaluator.py         # Read-only SQL execution + EX checking
│   ├── report.py            # Metrics, summary CSV/JSON, Pareto plot, failures.md
│   ├── linkers/             # S0, S1, S2, S3, S6, ABSL linkers
│   └── tests/               # Research package tests (79 tests)
├── spider/                  # Spider dataset (dev, tables, train, database/)
├── bird/                    # BIRD dataset (dev, dev_tables, dev_databases, train/)
├── app_runs/                # Per-run experiment directories (gitignored)
├── results/                 # Parsed benchmark results (gitignored)
├── cache/                   # Index cache (gitignored)
├── config.yaml              # Experiment configuration (datasets, budgets, generator)
├── llm_config.json          # Saved LLM settings (gitignored, contains API key)
├── .env                     # Environment variables (gitignored, contains API key)
├── requirements.txt         # Python dependencies
└── README.md
```

---

## Prerequisites

- Python 3.10+ (with `pip` and `venv`)
- Node.js 18+ and npm
- An LLM provider API key (OpenRouter, NVIDIA NIM, Groq, or custom OpenAI-compatible endpoint)

### Python dependencies

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Node.js dependencies

```powershell
cd frontend
npm install
```

---

## Configuration (LLM Settings)

Before running experiments, configure your LLM provider:

1. Start the backend and frontend (see below).
2. Open `http://localhost:5173/llm-settings`.
3. Enter:
   - **Provider**: `custom`, `openrouter`, `groq`, or `custom`
   - **API key**: your provider API key
   - **Base URL**: e.g. `https://integrate.api.nvidia.com/v1` (NVIDIA NIM) or `https://openrouter.ai/api/v1` (OpenRouter)
   - **Model**: e.g. `openai/gpt-oss-20b`
4. Click **Test Connection** to verify, then **Save Settings**.

Settings are saved locally to `llm_config.json` (gitignored — never commit it). The API key is never exposed in frontend responses, logs, or traces. If no settings are saved, the backend falls back to the `LLM_API_KEY` and `GENERATOR_MODEL_SLUG` environment variables from `.env`.

Example `.env`:
```
GENERATOR_MODEL_SLUG="openai/gpt-oss-20b"
LLM_API_KEY="your-api-key-here"
```

---

## Running the Application

### Backend

From the project root:

```powershell
.venv\Scripts\Activate.ps1   # if not already activated
cd backend
python -m uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000` (docs at `/docs`).

### Frontend

From the `frontend/` directory:

```powershell
cd frontend
npm run dev
```

The app will be available at `http://localhost:5173`.

---

## Running the 5-Question Demo

This runs exactly 5 questions (smoke mode) and completes quickly on Spider.

1. Open `http://localhost:5173`.
2. Go to **LLM Settings** and save your provider configuration (see above).
3. Go to **Experiment Runner** (`/runner`).
4. Select a dataset:
   - **Spider** — fast (~5 seconds for 5 questions)
   - **BIRD** — slower (~5–10 minutes for 5 questions due to larger schemas; this is expected, not a bug)
5. Click **Demo Mode** — this locks exactly 5 questions and enables smoke mode.
6. Select one or more strategies (S0, S1, S2, S3, S6, ABSL are all implemented; S4/S5 are not offered).
7. Click **Launch Experiment**.
8. A real, nonempty **Run ID** is returned. Click the **Execution Monitor** link (or go to `/monitor/<run-id>`).
9. Watch the status (Queued → Running → Completed/Failed). Polling stops automatically when the run reaches a terminal state.
10. When complete, click **View Results** to see:
    - Real execution accuracy (EX) per strategy
    - Strategy comparison table (EX, CI, tokens, BES, violations, escalation rate)
    - AUBC chart and EX vs. budget line chart
    - "5-Question Demo Sample" badge

### Verified smoke test results

| Dataset | Questions | Time | Result |
|---------|-----------|------|--------|
| Spider | 5 | ~5s | EX=0.80 (s0) |
| BIRD | 5 | ~5–10 min | EX=0.00 (s0) |

Both runs use the real pipeline and real SQLite databases — no results are fabricated.

---

## Running Tests

### Backend + research package

```powershell
cd backend
python -m pytest ../backend/tests/test_api.py ../schemalink/tests/ -v
```

Expected: **110 passed**.

### Frontend TypeScript check

```powershell
cd frontend
npx tsc -b
```

### Frontend production build

```powershell
cd frontend
npm run build
```

---

## Pages Overview

| Page | Path | Purpose |
|------|------|---------|
| Overview | `/` | Project description, strategy explanations, environment status |
| Experiment Runner | `/runner` | Configure dataset, strategies, demo mode; launch a run |
| Execution Monitor | `/monitor`, `/monitor/:runId` | View run ID, status, elapsed time; navigate to results |
| Results Dashboard | `/results`, `/results/:runId` | Real EX/AUBC/BES metrics, strategy comparison, plots |
| LLM Settings | `/llm-settings` | Configure and test LLM provider; saved locally |

Removed pages (not needed for the demo): Schema Explorer, Trace Explorer, ABSL Inspector, Failure Analysis. The underlying research functionality (trace generation, ABSL strategy, report generation) is preserved in `schemalink/`.

---

## Important Notes

- **BIRD is slow**: BIRD databases have far more tables/columns than Spider (e.g. `formula_1` has 107 schema elements vs Spider's max of 29). The same number of LLM calls are made, but each call processes a much larger prompt. Expect 5–10 minutes for a 5-question BIRD smoke test. This is expected behavior, not a bug.
- **No results are fabricated**: All SQL generation, execution, and accuracy metrics come from the real `schemalink` pipeline against real SQLite databases.
- **No secrets in git**: `.env` and `llm_config.json` contain API keys and are gitignored. Do not commit them.
- **Five-question limit**: Enforced at the backend API layer. Demo/smoke mode always runs exactly 5 questions (or fewer if the dataset has fewer usable questions).
- **Navigation does not stop experiments**: Runs execute in isolated subprocesses; navigating away and back does not interrupt them.

---

## License

Research demonstration tool. See project documentation for usage terms.
