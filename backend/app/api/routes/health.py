from fastapi import APIRouter
from app.core.config import settings
import os
import sys
from app.api.routes.llm_config import _load_config

router = APIRouter()

@router.get("/health")
def health_check():
    data = _load_config()
    saved_key = bool(data.get("api_key"))
    saved_model = bool(data.get("model"))
    env_key = bool(os.environ.get("LLM_API_KEY"))
    env_model = bool(os.environ.get("GENERATOR_MODEL_SLUG"))
    return {
        "status": "ok",
        "python_version": sys.version,
        "project_root": str(settings.PROJECT_ROOT),
        "runs_dir": str(settings.RUNS_DIR),
        "llm_key_configured": bool(env_key or saved_key),
        "llm_model": os.environ.get("GENERATOR_MODEL_SLUG") or data.get("model") or None,
    }
