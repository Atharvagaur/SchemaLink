from fastapi import APIRouter, HTTPException, Query
import yaml
import json
from pathlib import Path
from app.core.config import settings

router = APIRouter()

@router.get("/datasets")
def get_datasets():
    config_path = settings.PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        raise HTTPException(status_code=500, detail="config.yaml not found")
    
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    
    datasets = config.get("datasets", {})
    result = []
    
    for name, ds_cfg in datasets.items():
        root = Path(ds_cfg.get("root", ".")) if ds_cfg.get("root", ".") != "." else settings.PROJECT_ROOT
        if ds_cfg.get("root", ".") == ".":
            root = settings.PROJECT_ROOT
        else:
            root = settings.PROJECT_ROOT / ds_cfg["root"]
        
        dev_path = root / ds_cfg["dev"]
        tables_path = root / ds_cfg["tables"]
        db_root = root / ds_cfg["db_root"]
        
        result.append({
            "name": name,
            "dev_exists": dev_path.exists(),
            "tables_exists": tables_path.exists(),
            "db_root_exists": db_root.exists(),
            "dev_path": str(dev_path),
            "db_root": str(db_root),
            "available": dev_path.exists() and tables_path.exists() and db_root.exists(),
        })
    
    return {"datasets": result}


@router.get("/databases")
def get_databases(dataset: str = Query(...)):
    """Return distinct database IDs available for a dataset."""
    if dataset not in ["spider", "bird"]:
        raise HTTPException(status_code=400, detail="dataset must be spider or bird")
    
    config_path = settings.PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        raise HTTPException(status_code=500, detail="config.yaml not found")
    
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    
    ds_cfg = config.get("datasets", {}).get(dataset)
    if not ds_cfg:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset} not configured")
    
    root = settings.PROJECT_ROOT if ds_cfg.get("root", ".") == "." else settings.PROJECT_ROOT / ds_cfg["root"]
    dev_path = root / ds_cfg["dev"]
    
    if not dev_path.exists():
        raise HTTPException(status_code=404, detail=f"Dataset dev file not found: {dev_path}")
    
    with open(dev_path, encoding='utf-8') as f:
        data = json.load(f)
    
    db_ids = sorted({item.get("db_id") or item.get("database_id") for item in data if item.get("db_id") or item.get("database_id")})
    
    return {"dataset": dataset, "databases": db_ids}
