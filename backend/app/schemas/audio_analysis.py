"""API representation of a deterministic audio analysis.

Built explicitly from the domain models, like ``schemas/analysis.py``, so a
field added to the domain cannot appear in a response by accident.

The shape keeps the distinctions the domain exists to protect:

* **Missing is not zero.** ``null`` means the signal did not support the
  measurement. A recording with no reliable pitch has no range — not a range of
  zero semitones.
* **Nothing here is a verdict.** No score, no grade, no timbre label, and no
  combined figure with the speech analysis. Every field is a measurement with a
  stated definition and a unit in its name.
* **The range is what this recording contained.** Never a physiological limit.

The pitch timeline is deliberately *not* part of the summary response: it can
run to tens of thousands of points, and a client rendering a range and a
consistency figure should not have to download all of them. It has its own path.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.audio_analysis.models import (
    AnalysisSettings,
    AudioAnalysis,
    AudioMetrics,
    Loudness,
    PitchPoint,
    PitchStability,
    SpectralFeatures,
    UnstableSection,
    VocalRange,
)


class AnalysisSettingsResponse(BaseModel):
    """The parameters this result was measured with.

    Published because the numbers are only interpretable against them: a range
    measured with a 0.80 clarity threshold is not the same measurement as one
    taken at 0.90.
    """

    sample_rate_hz: int = Field(description="Rate the analysis ran at. The file's own rate.")
    frame_length_samples: int = Field(description="Analysis window length, in samples.")
    hop_length_samples: int = Field(description="Distance between successive frames.")
    min_frequency_hz: float = Field(description="Lowest frequency searched for.")
    max_frequency_hz: float = Field(description="Highest frequency searched for.")
    clarity_threshold: float = Field(
        description=(
            "Measured detector clarity, 0-1, below which a frame is unvoiced and carries no pitch."
        )
    )
    silence_rms: float = Field(description="RMS below which a frame is treated as silent.")

    @classmethod
    def from_domain(cls, settings: AnalysisSettings) -> "AnalysisSettingsResponse":
        return cls(**settings.model_dump())


class VocalRangeResponse(BaseModel):
    """The pitch range **detected in this recording**.

    Not a physiological range and not a voice type. It is bounded by what was
    performed, by the microphone, by the room, and by the detector's own search
    limits. Only pitches held for at least ~116 ms contribute.
    """

    lowest_frequency_hz: float
    highest_frequency_hz: float
    lowest_note: str = Field(description="Scientific pitch notation, sharps only, e.g. `G2`.")
    highest_note: str
    semitone_span: int = Field(description="Whole semitones between the extremes.")

    @classmethod
    def from_domain(cls, value: VocalRange) -> "VocalRangeResponse":
        return cls(**value.model_dump())


class UnstableSectionResponse(BaseModel):
    """A stretch where the detected pitch moved more than the threshold allows.

    Descriptive, not a fault: vibrato, a slide and a laugh all land here.
    """

    start_seconds: float
    end_seconds: float
    cents_std: float

    @classmethod
    def from_domain(cls, value: UnstableSection) -> "UnstableSectionResponse":
        return cls(**value.model_dump())


class PitchStabilityResponse(BaseModel):
    """How steady the detected pitch was. **Not a singing-ability score.**"""

    voiced_ratio: float = Field(description="Voiced frames over total frames, 0-1.")
    total_frames: int
    voiced_frames: int
    mean_cents_deviation: float | None = Field(
        default=None,
        description="Signed mean distance from the nearest semitone. Negative reads flat.",
    )
    mean_abs_cents_deviation: float | None = Field(
        default=None, description="Mean distance from the nearest semitone, ignoring direction."
    )
    cents_std: float | None = Field(
        default=None, description="Standard deviation of the signed deviation."
    )
    semitone_variance: float | None = Field(
        default=None, description="Variance of the pitch itself, in semitones squared."
    )
    in_tune_ratio: float | None = Field(
        default=None,
        description=(
            "Share of voiced frames within 25 cents of a semitone. Report it with "
            "that definition attached; it is a measurement, not a grade."
        ),
    )
    unstable_sections: list[UnstableSectionResponse] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, value: PitchStability) -> "PitchStabilityResponse":
        data = value.model_dump(exclude={"unstable_sections"})
        return cls(
            **data,
            unstable_sections=[
                UnstableSectionResponse.from_domain(section) for section in value.unstable_sections
            ],
        )


class LoudnessResponse(BaseModel):
    """Amplitude measurements. **These are not LUFS.**

    No loudness weighting, no gating, no reference level. Nothing derived from
    them may be described as broadcast- or mastering-grade loudness.
    """

    rms: float = Field(description="Root-mean-square amplitude, 0-1.")
    peak: float = Field(description="Largest absolute sample, 0-1.")
    dynamic_range_db: float | None = Field(
        default=None,
        description=(
            "95th minus 5th percentile of per-frame RMS, in dB. An estimate of how "
            "much the level moves — not a loudness range (LRA)."
        ),
    )
    crest_factor_db: float | None = Field(default=None, description="Peak over RMS, in dB.")
    clipped_sample_ratio: float = Field(
        description="Share of samples at or beyond full scale. A measurement, not a judgement."
    )

    @classmethod
    def from_domain(cls, value: Loudness) -> "LoudnessResponse":
        return cls(**value.model_dump())


class SpectralFeaturesResponse(BaseModel):
    """Averaged spectral shape over the audible frames.

    Raw measurable characteristics. **No timbre label is derived from these** —
    "bright", "dark", "breathy", "nasal" are classifications, and no validated
    classifier exists in this system.
    """

    centroid_hz: float
    bandwidth_hz: float
    rolloff_hz: float = Field(description="Frequency below which 85% of the energy lies.")
    zero_crossing_rate: float
    flatness: float = Field(description="Geometric over arithmetic mean: 1 is noise, 0 a tone.")

    @classmethod
    def from_domain(cls, value: SpectralFeatures) -> "SpectralFeaturesResponse":
        return cls(**value.model_dump())


class AudioSummaryResponse(BaseModel):
    """Everything measured from the signal."""

    duration_seconds: float
    settings: AnalysisSettingsResponse
    range: VocalRangeResponse | None = Field(
        default=None,
        description="`null` when no pitch was held long enough to define a range.",
    )
    stability: PitchStabilityResponse
    loudness: LoudnessResponse
    spectral: SpectralFeaturesResponse | None = Field(
        default=None, description="`null` when no frame rose above the silence threshold."
    )

    @classmethod
    def from_domain(cls, metrics: AudioMetrics) -> "AudioSummaryResponse":
        return cls(
            duration_seconds=metrics.duration_seconds,
            settings=AnalysisSettingsResponse.from_domain(metrics.settings),
            range=(VocalRangeResponse.from_domain(metrics.pitch) if metrics.pitch else None),
            stability=PitchStabilityResponse.from_domain(metrics.stability),
            loudness=LoudnessResponse.from_domain(metrics.loudness),
            spectral=(
                SpectralFeaturesResponse.from_domain(metrics.spectral) if metrics.spectral else None
            ),
        )


class AudioAnalysisResponse(BaseModel):
    """One audio analysis, at whatever stage it has reached."""

    audio_analysis_id: str
    recording_id: str
    status: str = Field(description="`pending`, `analyzing`, `completed` or `failed`.")
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = Field(default=None, description="Set only when `status` is `failed`.")
    summary: AudioSummaryResponse | None = Field(
        default=None, description="Present once the analysis has completed."
    )
    pitch_point_count: int = Field(
        description=(
            "Voiced frames in the timeline. Fetch them from "
            "`/audio-analysis/pitch`; they are not included here."
        )
    )

    @classmethod
    def from_domain(cls, analysis: AudioAnalysis) -> "AudioAnalysisResponse":
        return cls(
            audio_analysis_id=analysis.audio_analysis_id,
            recording_id=analysis.recording_id,
            status=analysis.status.value,
            created_at=analysis.created_at,
            started_at=analysis.started_at,
            completed_at=analysis.completed_at,
            error_code=analysis.error_code.value if analysis.error_code else None,
            summary=(
                AudioSummaryResponse.from_domain(analysis.metrics) if analysis.metrics else None
            ),
            pitch_point_count=len(analysis.pitch_points),
        )


class PitchPointResponse(BaseModel):
    """One voiced frame."""

    timestamp_seconds: float
    frequency_hz: float
    midi_note: int
    note_name: str
    cents: float = Field(description="Signed distance to `midi_note`, within ±50.")
    confidence: float = Field(description="Measured detector clarity, 0-1.")

    @classmethod
    def from_domain(cls, point: PitchPoint) -> "PitchPointResponse":
        return cls(**point.model_dump())


class PitchTimelineResponse(BaseModel):
    """The pitch timeline.

    **Only voiced frames appear.** Unvoiced audio — silence, noise, consonants,
    anything below the clarity threshold — is omitted rather than sent as null
    points, so every point here was measured. A gap between consecutive
    timestamps is therefore meaningful: draw it as a gap, never interpolate
    across it.
    """

    recording_id: str
    audio_analysis_id: str
    total_points: int = Field(description="Voiced frames the analysis produced.")
    returned_points: int = Field(description="Points in this response after decimation.")
    decimation: int = Field(
        description=(
            "1 when every point is included; `n` when every `n`-th point was taken "
            "to satisfy `max_points`."
        )
    )
    points: list[PitchPointResponse]

    @classmethod
    def from_domain(
        cls, analysis: AudioAnalysis, *, points: list[PitchPoint], decimation: int
    ) -> "PitchTimelineResponse":
        return cls(
            recording_id=analysis.recording_id,
            audio_analysis_id=analysis.audio_analysis_id,
            total_points=len(analysis.pitch_points),
            returned_points=len(points),
            decimation=decimation,
            points=[PitchPointResponse.from_domain(point) for point in points],
        )
