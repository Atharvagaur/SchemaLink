from fastapi import APIRouter, HTTPException, Query
import yaml
import json
from pathlib import Path
from app.core.config import settings

router = APIRouter()

@router.get("/questions")
def get_questions(dataset: str = Query(...), limit: int = Query(10, ge=1, le=100)):
    if dataset not in ["spider", "bird"]:
        raise HTTPException(status_code=400, detail="dataset must be spider or bird")
    
    config_path = settings.PROJECT_ROOT / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    
    ds_cfg = config["datasets"][dataset]
    root = settings.PROJECT_ROOT if ds_cfg.get("root", ".") == "." else settings.PROJECT_ROOT / ds_cfg["root"]
    dev_path = root / ds_cfg["dev"]
    
    if not dev_path.exists():
        raise HTTPException(status_code=404, detail=f"Dataset dev file not found: {dev_path}")
    
    with open(dev_path, encoding='utf-8') as f:
        data = json.load(f)
    
    questions = []
    for i, item in enumerate(data[:limit]):
        questions.append({
            "index": i,
            "question_id": item.get("question_id") or item.get("id") or i,
            "question": item.get("question", ""),
            "evidence": item.get("evidence", ""),
            "db_id": item.get("db_id") or item.get("database_id", ""),
        })
    
    return {"dataset": dataset, "total": len(data), "questions": questions}
