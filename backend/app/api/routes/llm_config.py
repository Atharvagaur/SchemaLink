"""LLM configuration endpoints: save, test, get, clear provider/API-key/model settings."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter()

CONFIG_PATH = settings.PROJECT_ROOT / "llm_config.json"

PROVIDER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
    },
    "custom": {
        "base_url": "",
    },
}

DEFAULT_MODEL = "openai/gpt-oss-120b"


class LLMConfigRequest(BaseModel):
    provider: str = Field(..., description="openrouter, groq, or custom")
    api_key: str = Field(..., description="LLM API key")
    base_url: str = Field(..., description="Provider base URL")
    model: str = Field(..., description="Model identifier")


class LLMConfigResponse(BaseModel):
    provider: Optional[str]
    model: Optional[str]
    base_url: Optional[str]
    configured: bool
    tested: bool
    test_status: Optional[str]  # "ok" | "failed" | None
    test_error: Optional[str]


class TestConnectionRequest(BaseModel):
    provider: str
    api_key: str
    base_url: str
    model: str


class TestConnectionResponse(BaseModel):
    ok: bool
    status: str
    error: Optional[str] = None


def _load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(data: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def _clear_config() -> None:
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


@router.get("/llm/config", response_model=LLMConfigResponse)
def get_llm_config():
    data = _load_config()
    configured = bool(data.get("api_key")) and bool(data.get("model")) and bool(data.get("base_url"))
    return {
        "provider": data.get("provider"),
        "model": data.get("model"),
        "base_url": data.get("base_url"),
        "configured": configured,
        "tested": bool(data.get("tested")),
        "test_status": data.get("test_status"),
        "test_error": data.get("test_error"),
    }


@router.post("/llm/config")
def save_llm_config(req: LLMConfigRequest):
    if not req.api_key or not req.api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required.")
    if not req.model or not req.model.strip():
        raise HTTPException(status_code=400, detail="Model is required.")
    if not req.base_url or not req.base_url.strip():
        raise HTTPException(status_code=400, detail="Base URL is required.")

    data = {
        "provider": req.provider,
        "api_key": req.api_key.strip(),
        "base_url": req.base_url.strip(),
        "model": req.model.strip(),
        "tested": False,
        "test_status": None,
        "test_error": None,
    }
    _save_config(data)
    return {"ok": True, "message": "LLM configuration saved."}


@router.post("/llm/test", response_model=TestConnectionResponse)
def test_llm_connection(req: TestConnectionRequest):
    """Make a small real request to verify the API key, endpoint, and model.

    The OpenAI client is pointed at the provider's base URL as supplied by the
    frontend (e.g. https://integrate.api.nvidia.com/v1). The client appends
    /chat/completions itself, so the base URL must not already include a
    duplicate /chat/completions segment. A minimal single-token completion is
    used to verify an actual model response without wasting API usage.
    """
    if not req.api_key or not req.base_url or not req.model:
        raise HTTPException(status_code=400, detail="API key, base URL, and model are required.")

    try:
        from openai import OpenAI
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            NotFoundError,
            RateLimitError,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"OpenAI client not available: {e}",
        )

    try:
        # 50s so the backend returns (success or a clear timeout error) before
        # the frontend's 60s per-request timeout expires.
        client = OpenAI(
            base_url=req.base_url,
            api_key=req.api_key,
            timeout=50,
            max_retries=0,
        )
        # Minimal real request: one completion token to validate key + endpoint + model.
        resp = client.chat.completions.create(
            model=req.model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            temperature=0.0,
        )
        # If we get a response with at least one choice, inference works.
        if not resp or not getattr(resp, "choices", None) or len(resp.choices) == 0:
            return TestConnectionResponse(
                ok=False,
                status="failed",
                error="The model returned an empty response. Verify the model identifier is correct.",
            )
        return TestConnectionResponse(
            ok=True,
            status="ok",
            error=None,
        )
    except APITimeoutError:
        return TestConnectionResponse(
            ok=False,
            status="failed",
            error="The connection test did not finish in time. The NVIDIA NIM endpoint may be slow or unreachable.",
        )
    except AuthenticationError:
        return TestConnectionResponse(
            ok=False,
            status="failed",
            error="Invalid API key or authentication failed. Verify your NVIDIA NIM API key.",
        )
    except NotFoundError:
        return TestConnectionResponse(
            ok=False,
            status="failed",
            error="The model is not available or not found. Verify the model identifier (e.g. openai/gpt-oss-20b).",
        )
    except RateLimitError:
        return TestConnectionResponse(
            ok=False,
            status="failed",
            error="Rate limit exceeded. Please try again later.",
        )
    except APIConnectionError:
        return TestConnectionResponse(
            ok=False,
            status="failed",
            error="Could not connect to the LLM endpoint. Check the base URL and your network connection.",
        )
    except APIStatusError as e:
        return TestConnectionResponse(
            ok=False,
            status="failed",
            error=f"The LLM API returned an error (HTTP {getattr(e, 'status_code', 'unknown')}).",
        )
    except Exception as e:
        # Never include the API key or other secrets in the message.
        msg = str(e)
        return TestConnectionResponse(
            ok=False,
            status="failed",
            error=msg if msg else "Connection test failed.",
        )


@router.delete("/llm/config")
def clear_llm_config():
    _clear_config()
    return {"ok": True, "message": "LLM configuration cleared."}


def get_saved_llm_env() -> Dict[str, str]:
    """Return env var overrides (LLM_API_KEY, GENERATOR_MODEL_SLUG) from saved config.

    Used when launching the experiment subprocess so it picks up the
    frontend-configured API key and model instead of requiring shell env vars.
    """
    data = _load_config()
    env: Dict[str, str] = {}
    if data.get("api_key"):
        env["LLM_API_KEY"] = data["api_key"]
    if data.get("model"):
        env["GENERATOR_MODEL_SLUG"] = data["model"]
    return env


def apply_llm_config_to_temp_config(config: Dict[str, Any]) -> None:
    """Override generator.base_url and generator.model in a temp config dict
    from the saved LLM configuration, if present."""
    data = _load_config()
    gen = config.setdefault("generator", {})
    if data.get("base_url"):
        gen["base_url"] = data["base_url"]
    if data.get("model"):
        gen["model"] = data["model"]
