from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from pathlib import Path
import re
import yaml
import json
import os
import sys
import subprocess
import uuid
import threading
from datetime import datetime
from app.core.config import settings
from app.api.routes.llm_config import get_saved_llm_env, apply_llm_config_to_temp_config

router = APIRouter()

STRATEGIES = ["s0", "s1", "s2", "s3", "s6", "absl"]

# In-memory process tracking: run_id -> subprocess.Popen
_processes: Dict[str, subprocess.Popen] = {}
_processes_lock = threading.Lock()


class RunRequest(BaseModel):
    dataset: str = Field(..., description="spider or bird")
    n_questions: int = Field(..., ge=1, le=150, description="Number of questions")
    strategies: List[str] = Field(..., description="List of strategies")
    seed: Optional[int] = 0
    smoke: Optional[bool] = False
    confirm_cost: Optional[bool] = False


@router.get("/runs")
def list_runs():
    runs_dir = settings.RUNS_DIR
    runs_dir.mkdir(parents=True, exist_ok=True)
    runs = []
    for run_dir in sorted(runs_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if run_dir.is_dir():
            meta_file = run_dir / "run_meta.json"
            if meta_file.exists():
                try:
                    with open(meta_file) as f:
                        meta = json.load(f)
                    runs.append(_enrich_run_status(run_id=run_dir.name, meta=meta, run_dir=run_dir))
                except Exception:
                    pass
    return {"runs": runs}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    run_dir = settings.RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    meta_file = run_dir / "run_meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail="Run metadata not found")
    with open(meta_file) as f:
        meta = json.load(f)
    return _enrich_run_status(run_id=run_id, meta=meta, run_dir=run_dir)


def _enrich_run_status(run_id: str, meta: dict, run_dir: Path) -> dict:
    """Poll the subprocess and update status if terminal."""
    status = meta.get("status", "queued")
    if status in ("queued", "running"):
        with _processes_lock:
            proc = _processes.get(run_id)
        if proc is not None:
            rc = proc.poll()
            if rc is not None:
                # Process has finished
                if rc == 0:
                    status = "completed"
                else:
                    status = "failed"
                meta["status"] = status
                meta["completed_at"] = datetime.utcnow().isoformat() + "Z"
                meta["return_code"] = rc
                # Try to read error from log
                log_file = run_dir / "experiment.log"
                if log_file.exists() and rc != 0:
                    try:
                        tail = log_file.read_text(encoding="utf-8", errors="replace")
                        meta["error"] = tail[-2000:] if len(tail) > 2000 else tail
                    except Exception:
                        pass
                # Persist updated meta
                try:
                    with open(run_dir / "run_meta.json", "w") as f:
                        json.dump(meta, f, indent=2)
                except Exception:
                    pass
                with _processes_lock:
                    _processes.pop(run_id, None)
        else:
            # Process not tracked (backend restarted). Check disk for results.
            results_dir = run_dir / "results" / meta.get("dataset", "")
            summary_file = results_dir / "summary.json"
            traces_file = results_dir / "traces.jsonl"
            if summary_file.exists() or traces_file.exists():
                # Results exist on disk -> run completed
                status = "completed"
                meta["status"] = status
                meta["completed_at"] = meta.get("completed_at") or datetime.utcnow().isoformat() + "Z"
                try:
                    with open(run_dir / "run_meta.json", "w") as f:
                        json.dump(meta, f, indent=2)
                except Exception:
                    pass
            elif meta.get("started_at"):
                # Was running but no results and process is gone -> failed
                status = "failed"
                meta["status"] = status
                meta["completed_at"] = datetime.utcnow().isoformat() + "Z"
                meta["error"] = "Experiment process is no longer running (backend may have restarted)."
                try:
                    with open(run_dir / "run_meta.json", "w") as f:
                        json.dump(meta, f, indent=2)
                except Exception:
                    pass
    meta.pop("pid", None)
    return meta


@router.get("/runs/{run_id}/summary")
def get_run_summary(run_id: str):
    run_dir = settings.RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    meta_file = run_dir / "run_meta.json"
    if meta_file.exists():
        with open(meta_file) as f:
            meta = json.load(f)
    else:
        meta = {}

    dataset = meta.get("dataset")
    if not dataset:
        raise HTTPException(status_code=404, detail="Run dataset not found")

    results_dir = run_dir / "results" / dataset
    summary_json = None
    if results_dir and (results_dir / "summary.json").exists():
        with open(results_dir / "summary.json") as f:
            summary_json = json.load(f)

    # Per-strategy rows from summary.csv
    rows = []
    if results_dir and (results_dir / "summary.csv").exists():
        import csv
        with open(results_dir / "summary.csv", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)

    if summary_json is None and not rows:
        raise HTTPException(status_code=404, detail="Summary not found for this run")

    return {
        "run_id": run_id,
        "summary": summary_json or {},
        "rows": rows,
    }


_SAFE_ID = re.compile(r"[A-Za-z0-9_.-]+")
_SAFE_PNG = re.compile(r"[A-Za-z0-9_.-]+\.png", re.IGNORECASE)


@router.get("/runs/{run_id}/plots/{filename}")
def get_run_plot(run_id: str, filename: str):
    """Serve a PNG plot (e.g. pareto_spider.png) from a run's results dir.

    run_id and filename are validated strictly to prevent path traversal:
    no path separators, no "..", only *.png, and the resolved path must stay
    inside that run's results directory.
    """
    if not run_id or run_id in (".", "..") or not _SAFE_ID.fullmatch(run_id):
        raise HTTPException(status_code=400, detail="Invalid run id")
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
        or not _SAFE_PNG.fullmatch(filename)
    ):
        raise HTTPException(status_code=400, detail="Invalid filename")

    run_dir = settings.RUNS_DIR / run_id
    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Run not found")
    results_dir = run_dir / "results"
    if not results_dir.exists() or not results_dir.is_dir():
        raise HTTPException(status_code=404, detail="Results not found for this run")

    results_dir_resolved = results_dir.resolve()
    target = None
    for candidate in results_dir.rglob(filename):
        if not candidate.is_file():
            continue
        if candidate.name.lower() != filename.lower():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(results_dir_resolved)
        except ValueError:
            continue
        target = resolved
        break
    if target is None:
        raise HTTPException(status_code=404, detail="Plot not found")

    return FileResponse(str(target), media_type="image/png")


@router.post("/runs")
def create_run(req: RunRequest):
    # Validate dataset
    if req.dataset not in ["spider", "bird"]:
        raise HTTPException(status_code=400, detail="dataset must be spider or bird")

    # Validate strategies
    for s in req.strategies:
        if s not in STRATEGIES:
            raise HTTPException(status_code=400, detail=f"Unsupported strategy: {s}. Choose from {STRATEGIES}")

    # Validate question count
    if req.n_questions < 1:
        raise HTTPException(status_code=400, detail="n_questions must be >= 1")
    if req.n_questions > 150:
        raise HTTPException(status_code=400, detail="n_questions cannot exceed available questions")

    # Check for API key: env var first, then saved frontend config
    saved_env = get_saved_llm_env()
    api_key = os.environ.get("LLM_API_KEY") or saved_env.get("LLM_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="LLM_API_KEY not configured. Set it in LLM Settings or as an environment variable.")

    # Check model: env var first, then saved config, then config.yaml
    model = os.environ.get("GENERATOR_MODEL_SLUG") or saved_env.get("GENERATOR_MODEL_SLUG") or get_model_from_config()
    if not model:
        raise HTTPException(status_code=400, detail="Model not configured. Set it in LLM Settings, GENERATOR_MODEL_SLUG, or generator.model in config.yaml.")

    # Cost confirmation for non-smoke runs > 5 questions
    if not req.smoke and not req.confirm_cost and req.n_questions > 5:
        raise HTTPException(status_code=400, detail="Must confirm cost for non-smoke runs with more than 5 questions.")

    # Enforce demo/smoke limit: smoke runs are capped at smoke_n questions
    # (default 5) so a demo always stays a small classroom sample.
    if req.smoke:
        try:
            config_path = settings.PROJECT_ROOT / "config.yaml"
            with open(config_path) as f:
                _cfg = yaml.safe_load(f) or {}
            effective_n = int((_cfg.get("experiments") or {}).get("smoke_n", 5))
            if effective_n < 1:
                effective_n = 5
        except Exception:
            effective_n = 5
        req.n_questions = effective_n

    # Create run directory
    run_id = str(uuid.uuid4())
    run_dir = settings.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "results").mkdir(exist_ok=True)
    (run_dir / "config").mkdir(exist_ok=True)

    # Prepare metadata
    meta = {
        "run_id": run_id,
        "dataset": req.dataset,
        "n_questions": req.n_questions,
        "strategies": req.strategies,
        "seed": req.seed,
        "smoke": req.smoke,
        "status": "queued",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "started_at": None,
        "completed_at": None,
        "model": model,
    }

    with open(run_dir / "run_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    # Launch experiment in subprocess
    try:
        launch_experiment(run_id, req, run_dir, meta, saved_env)
    except Exception as e:
        meta["status"] = "failed"
        meta["error"] = str(e)
        with open(run_dir / "run_meta.json", "w") as f:
            json.dump(meta, f, indent=2)
        raise HTTPException(status_code=500, detail=f"Failed to launch experiment: {str(e)}")

    return {"run_id": run_id, "status": "queued", "message": "Experiment launched"}


def get_model_from_config():
    try:
        config_path = settings.PROJECT_ROOT / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        model = config.get("generator", {}).get("model")
        if model and model != "GENERATOR_MODEL_SLUG":
            return model
    except Exception:
        pass
    return None


def launch_experiment(run_id: str, req: RunRequest, run_dir: Path, meta: dict, saved_env: Dict[str, str] = None):
    # Create a temporary config (do not modify original config.yaml)
    config_path = settings.PROJECT_ROOT / "config.yaml"
    with open(config_path) as f:
        base_config = yaml.safe_load(f) or {}

    config = json.loads(json.dumps(base_config))  # deep copy
    config["experiments"]["seed"] = req.seed or 0
    config["experiments"]["results_dir"] = str(run_dir / "results")
    apply_llm_config_to_temp_config(config)

    temp_config_path = run_dir / "config" / "config.yaml"
    with open(temp_config_path, "w") as f:
        yaml.safe_dump(config, f)

    # Build command - safe argument list, no shell concatenation
    cmd = [
        sys.executable,
        "-m", "schemalink.run",
        "--config", str(temp_config_path),
        "--dataset", req.dataset,
        "--n", str(req.n_questions),
        "--strategies", ",".join(req.strategies),
    ]
    if req.smoke:
        cmd.append("--smoke")

    # Update meta to running
    meta["status"] = "running"
    meta["started_at"] = datetime.utcnow().isoformat() + "Z"
    with open(run_dir / "run_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    # Launch subprocess
    log_file = run_dir / "experiment.log"
    with open(log_file, "w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            cmd,
            cwd=str(settings.PROJECT_ROOT),
            stdout=logf,
            stderr=logf,
            env={**os.environ.copy(), **(saved_env or {})},
        )

    with _processes_lock:
        _processes[run_id] = proc

    meta["pid"] = proc.pid
    with open(run_dir / "run_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
