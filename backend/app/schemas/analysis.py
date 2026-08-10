"""API representation of an analysis.

Built explicitly from the domain models rather than returning them directly, so
a field added to the domain cannot appear in a response by accident. The
mapping is one-way and deliberate: every field below was chosen to be public.

The shape preserves the distinctions the domain layer exists to protect:

* **Missing is not zero.** An optional field that is ``null`` means the
  recording did not support measuring it. Clients must render it as "not
  measured", never as a zero.
* **Measured and generated are separate.** ``metrics`` is arithmetic over the
  transcript; ``feedback`` is a language model's prose about those numbers.
  They are different objects, each carrying its own provenance, and nothing
  merges them.
* **Provenance is always present.** ``is_mock`` tells a client whether it is
  looking at demo data.

There is deliberately no overall score, grade or percentage, and nothing about
pronunciation: no validated measurement of either exists behind this API.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.analysis.models import (
    Analysis,
    AnalysisProvenance,
    Feedback,
    FillerWordStats,
    Provenance,
    SpeechMetrics,
    Transcript,
)


class ProvenanceResponse(BaseModel):
    """Where one piece of generated content came from."""

    provider: str = Field(description="Short provider label, e.g. `deepgram` or `mock`.")
    model: str | None = Field(default=None, description="Provider-side model identifier.")
    is_mock: bool = Field(
        description=(
            "`true` when this content came from a development stand-in. Content "
            "marked `true` is demo data and must never be presented as real analysis."
        )
    )

    @classmethod
    def from_domain(cls, provenance: Provenance) -> "ProvenanceResponse":
        return cls(
            provider=provenance.provider,
            model=provenance.model,
            is_mock=provenance.is_mock,
        )


class AnalysisProvenanceResponse(BaseModel):
    """Origins of everything in this analysis."""

    transcription: ProvenanceResponse | None = Field(
        default=None, description="Who produced the transcript. `null` before one exists."
    )
    feedback: ProvenanceResponse | None = Field(
        default=None, description="Who wrote the feedback. `null` when there is none."
    )
    is_mock: bool = Field(description="`true` if any content here came from a mock provider.")

    @classmethod
    def from_domain(cls, provenance: AnalysisProvenance) -> "AnalysisProvenanceResponse":
        return cls(
            transcription=(
                ProvenanceResponse.from_domain(provenance.transcription)
                if provenance.transcription
                else None
            ),
            feedback=(
                ProvenanceResponse.from_domain(provenance.feedback) if provenance.feedback else None
            ),
            is_mock=provenance.is_mock,
        )


class TranscriptWordResponse(BaseModel):
    """One transcribed word, with timings when the provider supplied them."""

    text: str = Field(description="The word as the provider rendered it, punctuation included.")
    start_seconds: float | None = Field(
        default=None,
        description=(
            "Offset of the word's start. `null` means the provider returned no "
            "timing for it — not that the word began at zero."
        ),
    )
    end_seconds: float | None = Field(default=None, description="Offset of the word's end.")


class TranscriptResponse(BaseModel):
    """What the speech-to-text provider returned."""

    text: str = Field(description="The full transcript.")
    words: list[TranscriptWordResponse] = Field(
        default_factory=list,
        description="Per-word tokens. Empty when the provider returned text only.",
    )
    language: str | None = Field(default=None, description="Language tag, when reported.")
    audio_duration_seconds: float | None = Field(
        default=None, description="Duration the provider observed."
    )
    includes_disfluencies: bool = Field(
        description=(
            "Whether this transcript is verbatim. When `false`, filler-word "
            "statistics are omitted from the metrics because they are not "
            "measurable — not because none were said."
        )
    )

    @classmethod
    def from_domain(cls, transcript: Transcript) -> "TranscriptResponse":
        return cls(
            text=transcript.text,
            words=[
                TranscriptWordResponse(
                    text=word.text,
                    start_seconds=word.start_seconds,
                    end_seconds=word.end_seconds,
                )
                for word in transcript.words
            ],
            language=transcript.language,
            audio_duration_seconds=transcript.audio_duration_seconds,
            includes_disfluencies=transcript.includes_disfluencies,
        )


class FillerTermCountResponse(BaseModel):
    """How often one filler term appeared."""

    term: str
    category: str = Field(
        description=(
            "`hesitation` for sounds with no other meaning (`um`, `uh`), or "
            "`discourse_marker` for ordinary words matched lexically (`like`, "
            "`you know`) — those include legitimate, non-filler uses and must "
            "be labelled as a word count wherever they are shown."
        )
    )
    count: int


class FillerWordStatsResponse(BaseModel):
    """Filler counts, in two separately reportable categories."""

    hesitation_count: int = Field(description="Unambiguous hesitation sounds.")
    discourse_marker_count: int = Field(
        description="Lexical matches on context-dependent words. Not a hesitation count."
    )
    total_count: int
    by_term: list[FillerTermCountResponse] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, stats: FillerWordStats) -> "FillerWordStatsResponse":
        return cls(
            hesitation_count=stats.hesitation_count,
            discourse_marker_count=stats.discourse_marker_count,
            total_count=stats.total_count,
            by_term=[
                FillerTermCountResponse(
                    term=item.term, category=item.category.value, count=item.count
                )
                for item in stats.by_term
            ],
        )


class SpeechMetricsResponse(BaseModel):
    """Deterministic measurements taken from the transcript.

    Every field except ``word_count`` is optional, and ``null`` always means
    "this recording did not support measuring it". Nothing here was estimated.
    """

    word_count: int = Field(description="Spoken words. Always available.")

    duration_source: str | None = Field(
        default=None,
        description=(
            "Which clock the rate metrics used: `word_timings` (first word to "
            "last, excluding leading and trailing silence) or "
            "`recording_duration` (the file's own length, which includes it). "
            "The two give different rates from the same words."
        ),
    )
    speaking_duration_seconds: float | None = Field(
        default=None, description="The span the rate metrics were computed over."
    )
    words_per_minute: float | None = Field(
        default=None, description="Speaking rate across the whole span, pauses included."
    )
    articulation_rate_wpm: float | None = Field(
        default=None,
        description="Speaking rate with detected pauses removed. Requires word timings.",
    )

    pause_threshold_seconds: float | None = Field(
        default=None,
        description="Gap length counted as a pause. A pause count means nothing without it.",
    )
    pause_count: int | None = Field(
        default=None,
        description=(
            "Pauses detected. `null` means there were no word timings to detect "
            "them from; `0` means the timings were read and no gap was long enough."
        ),
    )
    total_pause_seconds: float | None = Field(default=None)
    longest_pause_seconds: float | None = Field(
        default=None, description="`null` when no pause was found — the maximum of nothing."
    )
    mean_pause_seconds: float | None = Field(
        default=None, description="`null` when no pause was found."
    )

    filler_words: FillerWordStatsResponse | None = Field(
        default=None,
        description=(
            "`null` when the transcript is not verbatim, so fillers could not be "
            "counted. It never means none were said."
        ),
    )

    @classmethod
    def from_domain(cls, metrics: SpeechMetrics) -> "SpeechMetricsResponse":
        return cls(
            word_count=metrics.word_count,
            duration_source=metrics.duration_source.value if metrics.duration_source else None,
            speaking_duration_seconds=metrics.speaking_duration_seconds,
            words_per_minute=metrics.words_per_minute,
            articulation_rate_wpm=metrics.articulation_rate_wpm,
            pause_threshold_seconds=metrics.pause_threshold_seconds,
            pause_count=metrics.pause_count,
            total_pause_seconds=metrics.total_pause_seconds,
            longest_pause_seconds=metrics.longest_pause_seconds,
            mean_pause_seconds=metrics.mean_pause_seconds,
            filler_words=(
                FillerWordStatsResponse.from_domain(metrics.filler_words)
                if metrics.filler_words
                else None
            ),
        )


class FeedbackResponse(BaseModel):
    """Language-model prose about the measurements. Not a measurement itself."""

    summary: str
    strengths: list[str] = Field(default_factory=list)
    areas_to_improve: list[str] = Field(default_factory=list)
    next_action: str = Field(description="The single thing to try next.")

    @classmethod
    def from_domain(cls, feedback: Feedback) -> "FeedbackResponse":
        return cls(
            summary=feedback.summary,
            strengths=list(feedback.strengths),
            areas_to_improve=list(feedback.areas_to_improve),
            next_action=feedback.next_action,
        )


class AnalysisResponse(BaseModel):
    """One analysis, in whatever state it has reached.

    ``status`` decides which of the optional fields are populated:

    | status | transcript | metrics | feedback | error_code |
    | --- | --- | --- | --- | --- |
    | `pending`, `transcribing` | `null` | `null` | `null` | `null` |
    | `analyzing` | present | present | `null` | `null` |
    | `completed` | present | present | present *or* `null` | `null` |
    | `failed` | usually `null` | usually `null` | `null` | present |

    A completed analysis with `feedback: null` is a normal outcome, not an
    error: the measurements are produced without a language model, so a
    feedback outage degrades an analysis to numbers rather than losing it.
    """

    analysis_id: str = Field(
        description="Server-generated identifier for this analysis.",
        examples=["3fa85f6457174562b3fc2c963f66afa6"],
    )
    recording_id: str = Field(description="The recording this analysis belongs to.")
    status: str = Field(
        description="`pending`, `transcribing`, `analyzing`, `completed` or `failed`.",
        examples=["completed"],
    )

    created_at: datetime = Field(description="When the analysis was queued (UTC).")
    started_at: datetime | None = Field(
        default=None, description="When work actually began. `null` while queued."
    )
    completed_at: datetime | None = Field(
        default=None, description="When it finished, successfully or not."
    )

    error_code: str | None = Field(
        default=None,
        description=(
            "Set only when `status` is `failed`. The analysis failed; the "
            "request that returned this did not."
        ),
        examples=["ANALYSIS_PROVIDER_TIMEOUT"],
    )

    transcript: TranscriptResponse | None = Field(default=None)
    metrics: SpeechMetricsResponse | None = Field(
        default=None, description="Measured deterministically. Nothing here was estimated."
    )
    feedback: FeedbackResponse | None = Field(
        default=None,
        description="Generated by a language model from the metrics. `null` when unavailable.",
    )
    provenance: AnalysisProvenanceResponse = Field(
        description="Which providers produced the content above, and whether any was a mock."
    )

    @classmethod
    def from_analysis(cls, analysis: Analysis) -> "AnalysisResponse":
        return cls(
            analysis_id=analysis.analysis_id,
            recording_id=analysis.recording_id,
            status=analysis.status.value,
            created_at=analysis.created_at,
            started_at=analysis.started_at,
            completed_at=analysis.completed_at,
            error_code=analysis.error_code.value if analysis.error_code else None,
            transcript=(
                TranscriptResponse.from_domain(analysis.transcript) if analysis.transcript else None
            ),
            metrics=(
                SpeechMetricsResponse.from_domain(analysis.metrics) if analysis.metrics else None
            ),
            feedback=(
                FeedbackResponse.from_domain(analysis.feedback) if analysis.feedback else None
            ),
            provenance=AnalysisProvenanceResponse.from_domain(analysis.provenance),
        )
