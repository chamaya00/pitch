"""Aggregates all v1 routers."""

from fastapi import APIRouter

from app.api.v1.routes import analysis, config, health, recordings

api_router = APIRouter()
api_router.include_router(config.router)
api_router.include_router(health.router)
api_router.include_router(recordings.router)
api_router.include_router(analysis.router)
