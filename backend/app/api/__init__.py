from fastapi import APIRouter
from app.api.routes import health, meta, datasets, questions, runs, llm_config

router = APIRouter()
api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(meta.router, tags=["meta"])
api_router.include_router(datasets.router, tags=["datasets"])
api_router.include_router(questions.router, tags=["questions"])
api_router.include_router(runs.router, tags=["runs"])
api_router.include_router(llm_config.router, tags=["llm_config"])
