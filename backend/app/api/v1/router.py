"""Aggregates all v1 routers."""

from fastapi import APIRouter

from app.api.v1.routes import (
    analysis,
    audio_analysis,
    comparison,
    compatibility,
    config,
    health,
    identity,
    progress,
    recordings,
)

api_router = APIRouter()
api_router.include_router(config.router)
api_router.include_router(health.router)
api_router.include_router(identity.router)
# Both before `recordings`, so the literal `/recordings/compare` and
# `/recordings/progress` are matched before `/recordings/{recording_id}` can
# claim either as an id.
api_router.include_router(comparison.router)
api_router.include_router(progress.router)
api_router.include_router(recordings.router)
api_router.include_router(analysis.router)
api_router.include_router(audio_analysis.router)
# Carries both `/references` and `/recordings/{id}/compatibility`. The
# recording path is a sub-resource of a real id, so it cannot collide with
# the literal segments the two routers above claim.
api_router.include_router(compatibility.router)
