"""Aggregates all v1 routers."""

from fastapi import APIRouter

from app.api.v1.routes import (
    analysis,
    audio_analysis,
    comparison,
    config,
    health,
    recordings,
)

api_router = APIRouter()
api_router.include_router(config.router)
api_router.include_router(health.router)
# Before `recordings`, so the literal `/recordings/compare` is matched
# before `/recordings/{recording_id}` can claim it as an id.
api_router.include_router(comparison.router)
api_router.include_router(recordings.router)
api_router.include_router(analysis.router)
api_router.include_router(audio_analysis.router)
