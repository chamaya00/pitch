"""API representation of a recording comparison.

Built explicitly from the domain, like every other schema here, so a field added
to the domain cannot reach a response by accident. What that filter removes in
this case is worth naming: **no owner id, no analysis id, no storage path, no
provider name, no settings block.** A comparison response says what was measured
and how the two differ; nothing about where the bytes live or who produced them.

The three distinctions the domain exists to protect survive into the wire format:

* ``null`` means the recording did not support the measurement. It is never a
  zero, and ``availability`` states which side is missing so a client cannot
  reconstruct a delta from it.
* Every delta carries a ``unit`` and a ``direction``. ``percentage_points`` is
  its own unit because a ratio moving 0.50 → 0.56 is six percentage points, not
  six percent.
* ``direction: "neutral"`` means the difference has no better end. Three of the
  seven metrics are neutral, including the detected range.

There is no overall figure in this response, and no field that could hold one.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.audio_analysis.models import NoteSummary
from app.services.comparison.models import (
    ComparedMetric,
    ComparedNote,
    ComparisonSide,
    RecordingComparison,
)


class ComparisonSideResponse(BaseModel):
    """One recording's identity, and whether it can take part."""

    recording_id: str
    status: str = Field(
        description=(
            "`ready`, or why not: `not_found`, `analysis_missing`, "
            "`analysis_in_progress`, `analysis_failed`, "
            "`insufficient_pitch_signal`. A recording belonging to somebody "
            "else reports `not_found`, identically to one that does not exist."
        )
    )
    original_filename: str | None = Field(
        default=None, description="Display name. `null` when the recording is not this owner's."
    )
    created_at: datetime | None = None
    duration_seconds: float | None = None
    audio_format: str | None = None
    error_code: str | None = Field(
        default=None, description="Documented code for a failed analysis. Never a decoder message."
    )
    lowest_note: str | None = Field(
        default=None,
        description=(
            "Lowest note detected **in this recording**, never a physiological "
            "limit. `null` when no pitch was held long enough to establish a "
            "range — which is not a range of zero."
        ),
    )
    highest_note: str | None = None

    @classmethod
    def from_domain(cls, side: ComparisonSide) -> "ComparisonSideResponse":
        return cls(
            recording_id=side.recording_id,
            status=side.status.value,
            original_filename=side.original_filename,
            created_at=side.created_at,
            duration_seconds=side.duration_seconds,
            audio_format=side.audio_format,
            error_code=side.error_code.value if side.error_code else None,
            lowest_note=side.lowest_note,
            highest_note=side.highest_note,
        )


class ComparedMetricResponse(BaseModel):
    """One measurement from each recording, and the difference if there is one."""

    key: str
    label: str
    unit: str = Field(
        description="`percentage_points`, `cents`, `semitones` or `seconds`. Never `percent`."
    )
    direction: str = Field(
        description=(
            "What a change means, if anything: `higher_is_nearer_the_note`, "
            "`lower_is_nearer_the_note`, `lower_is_steadier`, or `neutral`. "
            "`neutral` metrics — recording length, pitched time, voiced share "
            "and detected range — have no better end and must not be labelled "
            "as an improvement in either direction."
        )
    )
    left: float | None
    right: float | None
    delta: float | None = Field(
        description=(
            "`right - left`, in `unit`. `null` whenever either side was not "
            "measured — a missing measurement never becomes a zero."
        )
    )
    availability: str = Field(description="`both`, `left_only`, `right_only` or `neither`.")

    @classmethod
    def from_domain(cls, metric: ComparedMetric) -> "ComparedMetricResponse":
        return cls(
            key=metric.key,
            label=metric.label,
            unit=metric.unit.value,
            direction=metric.direction.value,
            left=metric.left,
            right=metric.right,
            delta=metric.delta,
            availability=metric.availability.value,
        )


class ComparedNoteEntry(BaseModel):
    """One recording's figures for one note."""

    duration_seconds: float
    percentage_of_voiced_time: float
    frame_count: int
    mean_abs_cents: float
    in_tune_ratio: float

    @classmethod
    def from_domain(cls, note: NoteSummary) -> "ComparedNoteEntry":
        return cls(
            duration_seconds=note.duration_seconds,
            percentage_of_voiced_time=note.percentage_of_voiced_time,
            frame_count=note.frame_count,
            mean_abs_cents=note.mean_abs_cents,
            in_tune_ratio=note.in_tune_ratio,
        )


class ComparedNoteResponse(BaseModel):
    """One musical note as it appeared in each recording.

    A note absent from one recording is `null` there, **not a zero-duration
    entry**: it was not sung, which is not the same as having been sung for no
    time. Note presence is not scored — singing more distinct notes is not
    better, and there is no field here that says otherwise.
    """

    midi_note: int
    note_name: str
    presence: str = Field(description="`both`, `left_only` or `right_only`.")
    left: ComparedNoteEntry | None
    right: ComparedNoteEntry | None
    share_delta_points: float | None = Field(
        description="Difference in share of pitched time, in percentage points."
    )
    duration_delta_seconds: float | None
    mean_abs_cents_delta: float | None = Field(
        description="Difference in mean distance from the note, in cents. Lower is nearer."
    )

    @classmethod
    def from_domain(cls, note: ComparedNote) -> "ComparedNoteResponse":
        return cls(
            midi_note=note.midi_note,
            note_name=note.note_name,
            presence=note.presence.value,
            left=None if note.left is None else ComparedNoteEntry.from_domain(note.left),
            right=None if note.right is None else ComparedNoteEntry.from_domain(note.right),
            share_delta_points=note.share_delta_points,
            duration_delta_seconds=note.duration_delta_seconds,
            mean_abs_cents_delta=note.mean_abs_cents_delta,
        )


class RecordingComparisonResponse(BaseModel):
    """Two recordings' measurements, side by side.

    **A comparison of measurements, not a verdict.** There is no overall score,
    no ranking and no statement about which recording is better — the response
    has nowhere to put one.
    """

    left: ComparisonSideResponse
    right: ComparisonSideResponse
    comparable: bool = Field(
        description=(
            "`true` only when both recordings have a completed audio analysis. "
            "When `false`, `metrics` and `notes` are empty and each side's "
            "`status` says why."
        )
    )
    metrics: list[ComparedMetricResponse]
    notes: list[ComparedNoteResponse] = Field(
        description="Ordered by pitch, ascending, so a row means the same note in both columns."
    )
    caveats: list[str] = Field(
        description=(
            "Measurable reasons the two recordings may not be telling the same "
            "story: `different_duration`, `different_voiced_time`, "
            "`little_pitched_signal`, `different_sample_rate`, "
            "`different_audio_format`, `different_analysis_settings`, "
            "`clipping`, `no_detected_range`. This list is never exhaustive — "
            "microphone, room, effort and physical condition are not measured "
            "by this system and are not claimed here."
        )
    )

    @classmethod
    def from_domain(cls, comparison: RecordingComparison) -> "RecordingComparisonResponse":
        return cls(
            left=ComparisonSideResponse.from_domain(comparison.left),
            right=ComparisonSideResponse.from_domain(comparison.right),
            comparable=comparison.comparable,
            metrics=[ComparedMetricResponse.from_domain(m) for m in comparison.metrics],
            notes=[ComparedNoteResponse.from_domain(n) for n in comparison.notes],
            caveats=[caveat.value for caveat in comparison.caveats],
        )
