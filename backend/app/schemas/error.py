"""The error envelope returned by every handled failure.

Kept in ``schemas`` because it is part of the public API contract: the frontend
parses this exact shape in ``frontend/lib/api.ts``.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """A failure the API handled deliberately."""

    status: Literal["failed"] = "failed"
    error_code: str = Field(
        description="Stable machine-readable code. Clients branch on this, not on the message."
    )
    message: str = Field(description="Human-readable explanation, safe to show to a user.")
