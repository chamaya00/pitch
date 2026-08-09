"""Schemas for health endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness / readiness payload."""

    status: Literal["ok"] = "ok"
    service: str = Field(description="Human readable service name.")
    version: str = Field(description="Backend version.")
    environment: str = Field(description="Deployment environment.")
