from fastapi import APIRouter
import os
import yaml
from pathlib import Path
from app.core.config import settings
from app.api.routes.llm_config import _load_config

router = APIRouter()

STRATEGIES = ["s0", "s1", "s2", "s3", "s6", "absl"]

@router.get("/meta")
def get_meta():
    config_path = settings.PROJECT_ROOT / "config.yaml"
    config = {}
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    
    generator_cfg = config.get("generator", {})
    experiments_cfg = config.get("experiments", {})
    saved = _load_config()
    saved_key = bool(saved.get("api_key"))
    saved_model = saved.get("model")
    env_key = bool(os.environ.get("LLM_API_KEY"))
    env_model = os.environ.get("GENERATOR_MODEL_SLUG")
    
    return {
        "project_name": "SchemaLink",
        "description": "Budget-Aware Schema Linking for Text-to-SQL",
        "strategies": STRATEGIES,
        "strategies_info": {
            "s0": {"name": "Full Schema", "description": "Full-schema baseline, ignores budget", "type": "baseline"},
            "s1": {"name": "BM25", "description": "BM25-based schema ranking", "type": "fixed_budget"},
            "s2": {"name": "Dense", "description": "Dense-embedding-based schema ranking", "type": "fixed_budget"},
            "s3": {"name": "Hybrid (RRF)", "description": "Hybrid reciprocal-rank fusion of BM25 and dense", "type": "fixed_budget"},
            "s6": {"name": "Hybrid + Value", "description": "Hybrid ranking with value-augmented reordering", "type": "fixed_budget"},
            "absl": {"name": "ABSL", "description": "Adaptive budget cascade with tier escalation and self-consistency voting", "type": "adaptive"},
        },
        "budgets": experiments_cfg.get("budgets", {}),
        "model": env_model or saved_model or generator_cfg.get("model"),
        "llm_key_configured": bool(env_key or saved_key),
        "base_url": saved.get("base_url") or generator_cfg.get("base_url"),
        "prices": {
            "input_per_mtok": generator_cfg.get("price_input_per_mtok", 0.0),
            "output_per_mtok": generator_cfg.get("price_output_per_mtok", 0.0),
        },
        "datasets": list((config.get("datasets") or {}).keys()),
    }
