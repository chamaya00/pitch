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

from app.schemas.analysis import ProvenanceResponse
from app.services.audio_analysis.models import (
    IN_TUNE_CENTS,
    AnalysisSettings,
    AudioAnalysisSummary,
    AudioFeedback,
    AudioMetrics,
    KeyAnalysis,
    KeyCandidate,
    KeyEstimate,
    KeyMode,
    KeyUnmeasuredReason,
    Loudness,
    NoteSummary,
    PitchClassShare,
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
    def from_domain(cls, analysis: AudioAnalysisSummary) -> "AudioAnalysisResponse":
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
            pitch_point_count=analysis.pitch_point_count,
        )


class AudioFeedbackResponse(BaseModel):
    """A language model's interpretation of the measurements.

    Kept structurally apart from every measured value in this module. The
    numbers above were computed by the analysis pipeline; this is prose about
    them, it carries its own provenance including `is_mock`, and nothing here
    was measured.

    Sections are separate lists so a client can omit what is empty rather than
    render a heading with nothing under it. There is no field for a score, a
    grade or a timbre label — a model cannot return what the schema has nowhere
    to put.
    """

    summary: str
    strengths: list[str] = Field(default_factory=list)
    areas_to_improve: list[str] = Field(default_factory=list)
    pitch_observations: list[str] = Field(default_factory=list)
    range_observations: list[str] = Field(default_factory=list)
    note_observations: list[str] = Field(default_factory=list)
    audio_observations: list[str] = Field(default_factory=list)
    exercises: list[str] = Field(default_factory=list)
    practice_plan: list[str] = Field(default_factory=list)
    provenance: ProvenanceResponse

    @classmethod
    def from_domain(cls, feedback: AudioFeedback) -> "AudioFeedbackResponse":
        return cls(
            summary=feedback.summary,
            strengths=list(feedback.strengths),
            areas_to_improve=list(feedback.areas_to_improve),
            pitch_observations=list(feedback.pitch_observations),
            range_observations=list(feedback.range_observations),
            note_observations=list(feedback.note_observations),
            audio_observations=list(feedback.audio_observations),
            exercises=list(feedback.exercises),
            practice_plan=list(feedback.practice_plan),
            provenance=ProvenanceResponse.from_domain(feedback.provenance),
        )


class AudioFeedbackStateResponse(BaseModel):
    """Where a recording's audio feedback has got to, and the feedback itself.

    `status` is one of `not_requested`, `generating`, `completed` or `failed`.
    A recording with no completed analysis has no record to carry a status, so
    "unavailable" surfaces as a `404` with a specific `error_code` rather than
    as a fifth value here.
    """

    recording_id: str
    audio_analysis_id: str
    status: str
    error_code: str | None = Field(default=None, description="Set only when `status` is `failed`.")
    feedback: AudioFeedbackResponse | None = Field(
        default=None, description="Present once generation has completed."
    )

    @classmethod
    def from_domain(cls, analysis: AudioAnalysisSummary) -> "AudioFeedbackStateResponse":
        return cls(
            recording_id=analysis.recording_id,
            audio_analysis_id=analysis.audio_analysis_id,
            status=analysis.feedback_status.value,
            error_code=(
                analysis.feedback_error_code.value if analysis.feedback_error_code else None
            ),
            feedback=(
                AudioFeedbackResponse.from_domain(analysis.feedback) if analysis.feedback else None
            ),
        )


class NoteSummaryResponse(BaseModel):
    """How much of the pitched time was spent on one musical note."""

    midi_note: int
    note_name: str = Field(description="Scientific pitch notation, sharps only, e.g. `C4`.")
    duration_seconds: float = Field(
        description=(
            "Frames on this note times the analysis hop. Frames overlap, so each "
            "is charged the audio it newly brought rather than its full length."
        )
    )
    percentage_of_voiced_time: float = Field(
        description=(
            "Share of the recording's **pitched** time, not of its duration. "
            "Silence and unvoiced audio are excluded from the denominator, so "
            "these sum to 100 across the breakdown."
        )
    )
    frame_count: int = Field(
        description=(
            "Analysis frames behind this entry. A low count is a short note and "
            "thin evidence; it is reported rather than hidden."
        )
    )
    average_cents: float = Field(
        description="Signed mean deviation from this note. Negative reads flat."
    )
    mean_abs_cents: float = Field(description="Mean distance from this note, ignoring direction.")
    in_tune_ratio: float = Field(
        description=(
            f"Share of this note's frames within {IN_TUNE_CENTS:.0f} cents of it. "
            "A measurement with a stated threshold, not a grade."
        )
    )

    @classmethod
    def from_domain(cls, note: NoteSummary) -> "NoteSummaryResponse":
        return cls(**note.model_dump())


class NoteBreakdownResponse(BaseModel):
    """Where the pitched time went, note by note.

    An **empty `notes` list means no notes were detected**, which is not a
    successful measurement of zero notes. Render it the way every other
    unavailable measurement is rendered, not as a table with no rows.
    """

    recording_id: str
    audio_analysis_id: str
    voiced_seconds: float = Field(
        description="Total pitched time the breakdown divides up, in seconds."
    )
    total_frames: int = Field(description="Voiced frames behind the breakdown.")
    in_tune_cents: float = Field(
        description="The threshold `in_tune_ratio` is measured against, in cents."
    )
    notes: list[NoteSummaryResponse] = Field(
        description="Longest first; where two are equal, the lower note first."
    )

    @classmethod
    def from_domain(
        cls, analysis: AudioAnalysisSummary, notes: tuple[NoteSummary, ...]
    ) -> "NoteBreakdownResponse":
        return cls(
            recording_id=analysis.recording_id,
            audio_analysis_id=analysis.audio_analysis_id,
            voiced_seconds=sum(note.duration_seconds for note in notes),
            total_frames=sum(note.frame_count for note in notes),
            in_tune_cents=IN_TUNE_CENTS,
            notes=[NoteSummaryResponse.from_domain(note) for note in notes],
        )


class PitchClassShareResponse(BaseModel):
    """How much of the pitched time went to one pitch class."""

    pitch_class: int = Field(description="0-11, where 0 is C.")
    name: str = Field(description="Pitch class without an octave, sharps only, e.g. `C#`.")
    percentage_of_voiced_time: float = Field(
        description=(
            "Share of the recording's **pitched** time. Silence and unvoiced "
            "audio are excluded, so the twelve values sum to 100."
        )
    )

    @classmethod
    def from_domain(cls, share: PitchClassShare) -> "PitchClassShareResponse":
        return cls(**share.model_dump())


class KeyCandidateResponse(BaseModel):
    """One key, and how far it stood clear of the next one."""

    tonic: str = Field(description="Pitch class of the tonic, sharps only, e.g. `F#`.")
    mode: KeyMode
    confidence: float = Field(
        description=(
            "Margin between this candidate's correlation and the next candidate's. "
            "**Not a correlation and not a probability**: random pitch classes "
            "correlate +0.49 with a real key profile and one held note +0.48, so a "
            "correlation would read as certainty for a hum. The margin is what "
            "separates evidence from arithmetic."
        )
    )

    @classmethod
    def from_domain(cls, candidate: KeyCandidate) -> "KeyCandidateResponse":
        return cls(**candidate.model_dump())


class KeyEstimateResponse(BaseModel):
    """The key a recording's pitch classes best fit, with its runner-up.

    **An estimate of what was sung, not a statement that it was right.** There is
    no reference melody in this product, so this cannot say whether the key was
    the intended one - only which key the notes that were sung best fit.
    """

    tonic: str = Field(description="Pitch class of the tonic, sharps only, e.g. `F#`.")
    mode: KeyMode
    confidence: float = Field(description="See `alternative.confidence` for the definition.")
    alternative: KeyCandidateResponse | None = Field(
        default=None,
        description=(
            "The candidate that came second, so a close call is visible rather "
            "than hidden behind one confident-looking label."
        ),
    )

    @classmethod
    def from_domain(cls, estimate: KeyEstimate) -> "KeyEstimateResponse":
        return cls(
            tonic=estimate.tonic,
            mode=estimate.mode,
            confidence=estimate.confidence,
            alternative=(
                KeyCandidateResponse.from_domain(estimate.alternative)
                if estimate.alternative is not None
                else None
            ),
        )


class MusicalKeyResponse(BaseModel):
    """The key implied by what was sung, or a stated reason there is none.

    **`key: null` is a measurement outcome, not an error.** It means the analysis
    ran, the timeline is there, and the notes in it do not establish a key -
    which a hum, an arpeggio or a chromatic wander all produce. Render it as
    "not measured", the way every other unavailable measurement is rendered.
    `unmeasured_reason` says which evidence was missing.

    The evidence travels with the verdict either way. `pitch_classes` is always
    twelve entries when there was anything pitched to fold, **including the
    unused classes at zero**, because a key is decided as much by which classes
    are absent as by which are present.

    A recording with no *completed* analysis is a different thing entirely and
    answers `404`, not a null key.
    """

    recording_id: str
    audio_analysis_id: str
    key: KeyEstimateResponse | None = Field(
        default=None, description="`null` when the evidence did not establish one."
    )
    unmeasured_reason: KeyUnmeasuredReason | None = Field(
        default=None,
        description=(
            "Present exactly when `key` is `null`. **Not an error code**: it never "
            "appears in the error envelope and is never an HTTP status."
        ),
    )
    pitch_classes: list[PitchClassShareResponse] = Field(
        description="Twelve entries in pitch-class order, including unused ones at zero."
    )
    distinct_pitch_classes: int = Field(
        description="Pitch classes carrying more than a negligible share of the pitched time."
    )
    voiced_seconds: float = Field(description="Pitched time the profile was folded from.")
    method: str = Field(
        description=(
            "Which published key-profile set produced this. The numbers are only "
            "interpretable against it, as `settings` is for the summary."
        )
    )

    @classmethod
    def from_domain(cls, analysis: AudioAnalysisSummary, key: KeyAnalysis) -> "MusicalKeyResponse":
        return cls(
            recording_id=analysis.recording_id,
            audio_analysis_id=analysis.audio_analysis_id,
            key=(KeyEstimateResponse.from_domain(key.key) if key.key is not None else None),
            unmeasured_reason=key.unmeasured_reason,
            pitch_classes=[
                PitchClassShareResponse.from_domain(share) for share in key.pitch_classes
            ],
            distinct_pitch_classes=key.distinct_pitch_classes,
            voiced_seconds=key.voiced_seconds,
            method=key.method,
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
        cls, analysis: AudioAnalysisSummary, *, points: list[PitchPoint], decimation: int
    ) -> "PitchTimelineResponse":
        """Build the response from a record and the points it is returning.

        Takes a summary rather than an :class:`AudioAnalysis`: the points come
        in beside it, sampled from the stored timeline, and ``total_points``
        comes from the count the record carries — never from ``len(points)``,
        which is the size of the sample and not of the measurement.
        """
        return cls(
            recording_id=analysis.recording_id,
            audio_analysis_id=analysis.audio_analysis_id,
            total_points=analysis.pitch_point_count,
            returned_points=len(points),
            decimation=decimation,
            points=[PitchPointResponse.from_domain(point) for point in points],
        )
