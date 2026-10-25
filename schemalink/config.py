"""Configuration management."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """Load and validate configuration from YAML."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            "Copy or create config.yaml first."
        )
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    gen = cfg.setdefault("generator", {})

    gen.setdefault("base_url", "https://openrouter.ai/api/v1")
    gen.setdefault("max_tokens", 512)
    gen.setdefault("temperature", 0.0)
    gen.setdefault("timeout", 60)
    gen.setdefault("workers", 4)

    # model must be set explicitly (never a placeholder)
    model = gen.get("model", "")
    if not model or model == "GENERATOR_MODEL_SLUG" or "${" in str(model):
        env_model = os.environ.get("GENERATOR_MODEL_SLUG", "")
        if env_model:
            gen["model"] = env_model
        else:
            raise ValueError(
                "generator.model is not set. Set it in config.yaml or export "
                "GENERATOR_MODEL_SLUG=<your-model-slug> (e.g. from OpenRouter)."
            )

    # api key from env only, never hardcoded
    gen["api_key"] = os.environ.get("LLM_API_KEY", "")
    if not gen["api_key"]:
        raise ValueError(
            "LLM_API_KEY environment variable not set. "
            "export LLM_API_KEY=<your-key> before running."
        )

    # dataset paths
    ds = cfg.setdefault("datasets", {})
    spider = ds.setdefault("spider", {})
    spider.setdefault("dev", "spider/dev.json")
    spider.setdefault("tables", "spider/tables.json")
    spider.setdefault("train", "spider/train_spider.json")
    spider.setdefault("db_root", "spider/database")

    bird = ds.setdefault("bird", {})
    bird.setdefault("dev", "bird/dev.json")
    bird.setdefault("tables", "bird/dev_tables.json")
    bird.setdefault("train", "bird/train/train.json")
    bird.setdefault("db_root", "bird/dev_databases")

    # budgets
    exp = cfg.setdefault("experiments", {})
    exp.setdefault("budgets", {
        "spider": [250, 500, 1000, 2000, 4000],
        "bird": [1000, 2000, 4000, 8000, 16000],
    })
    exp.setdefault("n_questions", 150)
    exp.setdefault("seed", 0)
    exp.setdefault("cache_dir", "cache")
    exp.setdefault("results_dir", "results")

    return cfg


class Config:
    """Dict wrapper with dotted access for convenience."""

    def __init__(self, d: Dict[str, Any]):
        self._d = d

    def __getitem__(self, k: str) -> Any:
        parts = k.split(".")
        cur = self._d
        for p in parts:
            cur = cur[p]
        return cur

    def get(self, k: str, default: Any = None) -> Any:
        try:
            return self[k]
        except (KeyError, TypeError):
            return default

    @property
    def raw(self) -> Dict[str, Any]:
        return self._d
