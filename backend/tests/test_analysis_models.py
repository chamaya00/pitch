"""Domain-model invariants for speech analysis.

These tests exist to pin down the two properties the rest of the layer is built
on: a stored record always says where its content came from, and a record can
never describe a state that did not happen (a failure with no reason, a success
with no results).
"""

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.errors import STATUS_BY_CODE, ErrorCode
from app.services.analysis.models import (
    TERMINAL_STATUSES,
    Analysis,
    AnalysisProvenance,
    AnalysisStatus,
    Feedback,
    FillerCategory,
    FillerTermCount,
    FillerWordStats,
    Pause,
    Provenance,
    SpeechMetrics,
    Transcript,
    TranscriptWord,
    new_analysis_id,
)

MOCK = Provenance(provider="mock", is_mock=True)
REAL = Provenance(provider="deepgram", model="nova-3", is_mock=False)


def make_transcript(**overrides: object) -> Transcript:
    payload: dict[str, object] = {"text": "hello there", "provenance": MOCK}
    payload.update(overrides)
    return Transcript.model_validate(payload)


def make_analysis(**overrides: object) -> Analysis:
    payload: dict[str, object] = {
        "analysis_id": new_analysis_id(),
        "recording_id": new_analysis_id(),
    }
    payload.update(overrides)
    return Analysis.model_validate(payload)


# --- Identifiers -----------------------------------------------------------


def test_generated_ids_satisfy_the_models_own_pattern():
    """The generator and the constraint must not drift apart."""
    for _ in range(50):
        make_analysis(analysis_id=new_analysis_id())


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd",
        "z" * 32,  # right length, not hex
        "0123456789abcdef",  # too short
        "0123456789ABCDEF0123456789ABCDEF",  # upper case
        "0123456789abcdef0123456789abcdef\n",
        "",
    ],
)
def test_an_id_that_could_become_a_path_is_refused(bad_id: str):
    """Nothing that fails this pattern can be interpolated into a filename."""
    with pytest.raises(ValidationError):
        make_analysis(analysis_id=bad_id)
    with pytest.raises(ValidationError):
        make_analysis(recording_id=bad_id)


# --- Provenance ------------------------------------------------------------


def test_provenance_requires_an_explicit_mock_flag():
    """There is no default: a provider has to say what it is."""
    with pytest.raises(ValidationError):
        Provenance.model_validate({"provider": "mock"})


def test_provenance_refuses_extra_fields():
    """The one place an adapter might stash a key or a request id."""
    with pytest.raises(ValidationError):
        Provenance.model_validate({"provider": "mock", "is_mock": True, "api_key": "sk-ant-secret"})


@pytest.mark.parametrize(
    "provider",
    [
        "Bearer sk-ant-api03-abc",
        "sk-ant-api03-ABCDEF",  # upper case is not a provider label
        "https://api.deepgram.com/v1/listen?key=abc",
        "mock provider",
        "",
        "-leading-dash",
    ],
)
def test_a_credential_shaped_value_cannot_be_a_provider_label(provider: str):
    with pytest.raises(ValidationError):
        Provenance(provider=provider, is_mock=False)


def test_a_secret_cannot_hide_in_the_model_field():
    with pytest.raises(ValidationError):
        Provenance(provider="deepgram", model="Bearer abc def", is_mock=False)


def test_analysis_provenance_reports_mock_content():
    assert AnalysisProvenance(transcription=REAL, feedback=MOCK).is_mock is True
    assert AnalysisProvenance(transcription=MOCK, feedback=REAL).is_mock is True
    assert AnalysisProvenance(transcription=REAL, feedback=REAL).is_mock is False
    assert AnalysisProvenance().is_mock is False


def test_analysis_provenance_is_derived_from_the_content_it_describes():
    analysis = make_analysis(
        status=AnalysisStatus.COMPLETED,
        transcript=make_transcript(provenance=REAL),
        metrics=SpeechMetrics(word_count=2),
        feedback=Feedback(summary="s", next_action="n", provenance=MOCK),
    )
    assert analysis.provenance.transcription == REAL
    assert analysis.provenance.feedback == MOCK
    assert analysis.provenance.is_mock is True


def test_provenance_survives_serialisation():
    """A stored record still knows it was mock after a round trip."""
    analysis = make_analysis(
        status=AnalysisStatus.COMPLETED,
        transcript=make_transcript(),
        metrics=SpeechMetrics(word_count=2),
    )
    restored = Analysis.model_validate_json(analysis.model_dump_json())
    assert restored == analysis
    assert restored.provenance.is_mock is True
    assert analysis.model_dump()["provenance"]["is_mock"] is True


def test_no_secret_reaches_a_serialised_analysis():
    """Everything in a dumped record is a label, a number or generated text."""
    analysis = make_analysis(
        status=AnalysisStatus.COMPLETED,
        transcript=make_transcript(provenance=REAL),
        metrics=SpeechMetrics(word_count=2),
    )
    dumped = analysis.model_dump_json()
    for secret in ("sk-ant", "Authorization", "Bearer ", "api_key"):
        assert secret not in dumped


# --- Word timings ----------------------------------------------------------


def test_a_word_carries_both_timings_or_neither():
    TranscriptWord(text="hi")
    TranscriptWord(text="hi", start_seconds=0.0, end_seconds=0.4)
    with pytest.raises(ValidationError):
        TranscriptWord(text="hi", start_seconds=0.0)
    with pytest.raises(ValidationError):
        TranscriptWord(text="hi", end_seconds=0.4)


def test_a_word_cannot_end_before_it_starts():
    with pytest.raises(ValidationError):
        TranscriptWord(text="hi", start_seconds=1.0, end_seconds=0.5)


def test_an_untimed_word_is_not_a_word_at_zero():
    word = TranscriptWord(text="hi")
    assert word.start_seconds is None
    assert word.is_timed is False


def test_word_timings_are_usable_only_when_every_word_has_them():
    timed = TranscriptWord(text="a", start_seconds=0.0, end_seconds=0.2)
    untimed = TranscriptWord(text="b")
    assert make_transcript(words=(timed,)).has_word_timings is True
    assert make_transcript(words=(timed, untimed)).has_word_timings is False
    assert make_transcript(words=()).has_word_timings is False


def test_an_empty_transcript_is_recognisable():
    assert make_transcript(text="   ").is_empty is True
    assert make_transcript(text="hello").is_empty is False


def test_disfluencies_are_assumed_absent_until_a_provider_says_otherwise():
    """The safe default: an unflagged provider yields no filler claims."""
    assert make_transcript().includes_disfluencies is False


# --- Pauses and filler stats ----------------------------------------------


def test_pause_duration_is_derived():
    assert Pause(start_seconds=1.0, end_seconds=1.75).duration_seconds == pytest.approx(0.75)


def test_a_pause_cannot_end_before_it_starts():
    with pytest.raises(ValidationError):
        Pause(start_seconds=2.0, end_seconds=1.0)


def test_filler_total_is_the_sum_of_both_categories():
    stats = FillerWordStats(
        hesitation_count=3,
        discourse_marker_count=2,
        by_term=(
            FillerTermCount(term="um", category=FillerCategory.HESITATION, count=3),
            FillerTermCount(term="like", category=FillerCategory.DISCOURSE_MARKER, count=2),
        ),
    )
    assert stats.total_count == 5


# --- Metrics container -----------------------------------------------------


def test_only_the_word_count_is_mandatory():
    metrics = SpeechMetrics(word_count=0)
    assert metrics.words_per_minute is None
    assert metrics.pause_count is None
    assert metrics.filler_words is None


def test_a_metric_cannot_be_stored_as_a_negative_or_zero_duration():
    with pytest.raises(ValidationError):
        SpeechMetrics(word_count=1, speaking_duration_seconds=0)
    with pytest.raises(ValidationError):
        SpeechMetrics(word_count=1, words_per_minute=-1)


# --- Analysis lifecycle ----------------------------------------------------


def test_every_status_is_representable():
    """Each lifecycle state can exist as a record, with the data it implies."""
    completed_extras: dict[AnalysisStatus, dict[str, object]] = {
        AnalysisStatus.COMPLETED: {
            "transcript": make_transcript(),
            "metrics": SpeechMetrics(word_count=2),
            "completed_at": datetime.now(UTC),
        },
        AnalysisStatus.FAILED: {
            "error_code": ErrorCode.ANALYSIS_PROVIDER_TIMEOUT,
            "completed_at": datetime.now(UTC),
        },
    }
    for status in AnalysisStatus:
        analysis = make_analysis(status=status, **completed_extras.get(status, {}))
        assert analysis.status is status
        assert analysis.is_terminal is (status in TERMINAL_STATUSES)


def test_a_new_analysis_starts_pending_with_nothing_attached():
    analysis = make_analysis()
    assert analysis.status is AnalysisStatus.PENDING
    assert analysis.started_at is None
    assert analysis.completed_at is None
    assert analysis.transcript is None
    assert analysis.metrics is None
    assert analysis.feedback is None
    assert analysis.error_code is None
    assert analysis.provenance.is_mock is False


def test_a_failure_must_say_why():
    with pytest.raises(ValidationError, match="must record an error_code"):
        make_analysis(status=AnalysisStatus.FAILED)


def test_a_non_failure_cannot_carry_an_error_code():
    """Guards against a stale code surviving a retry."""
    with pytest.raises(ValidationError, match="only valid on a failed analysis"):
        make_analysis(status=AnalysisStatus.TRANSCRIBING, error_code=ErrorCode.AI_UNAVAILABLE)


def test_completion_requires_the_deterministic_results():
    with pytest.raises(ValidationError, match="must have a transcript"):
        make_analysis(status=AnalysisStatus.COMPLETED)
    with pytest.raises(ValidationError, match="must have metrics"):
        make_analysis(status=AnalysisStatus.COMPLETED, transcript=make_transcript())


def test_completion_does_not_require_feedback():
    """A feedback outage degrades an analysis to numbers, it does not lose it."""
    analysis = make_analysis(
        status=AnalysisStatus.COMPLETED,
        transcript=make_transcript(),
        metrics=SpeechMetrics(word_count=2),
    )
    assert analysis.feedback is None
    assert analysis.provenance.feedback is None


def test_feedback_cannot_be_attached_without_its_metrics():
    """Prose is never shown without the numbers it claims to describe."""
    with pytest.raises(ValidationError, match="without the metrics it describes"):
        make_analysis(
            status=AnalysisStatus.ANALYZING,
            feedback=Feedback(summary="s", next_action="n", provenance=MOCK),
        )


def test_completed_at_is_only_set_once_the_work_is_over():
    with pytest.raises(ValidationError, match="only valid once the analysis has finished"):
        make_analysis(status=AnalysisStatus.TRANSCRIBING, completed_at=datetime.now(UTC))


def test_an_analysis_is_immutable():
    analysis = make_analysis()
    with pytest.raises(ValidationError):
        analysis.status = AnalysisStatus.COMPLETED  # type: ignore[misc]


# --- Error vocabulary ------------------------------------------------------


ANALYSIS_ERROR_CODES = [
    ErrorCode.ANALYSIS_NOT_CONFIGURED,
    ErrorCode.ANALYSIS_PROVIDER_UNAVAILABLE,
    ErrorCode.ANALYSIS_PROVIDER_TIMEOUT,
    ErrorCode.ANALYSIS_RATE_LIMITED,
    ErrorCode.ANALYSIS_PROVIDER_ERROR,
    ErrorCode.TRANSCRIPT_EMPTY,
]


@pytest.mark.parametrize("code", ANALYSIS_ERROR_CODES)
def test_each_analysis_code_can_be_recorded_on_a_failed_analysis(code: ErrorCode):
    analysis = make_analysis(status=AnalysisStatus.FAILED, error_code=code)
    assert analysis.error_code is code
    assert code.value in analysis.model_dump_json()


@pytest.mark.parametrize("code", ANALYSIS_ERROR_CODES)
def test_each_analysis_code_has_a_sensible_http_status(code: ErrorCode):
    status = STATUS_BY_CODE[code]
    assert 400 <= status < 600
    # None of these are the caller sending something invalid; they are our
    # infrastructure, the provider's, or the recording's content.
    assert status != 400


def test_analysis_codes_are_screaming_snake_case():
    for code in ANALYSIS_ERROR_CODES:
        assert re.fullmatch(r"[A-Z][A-Z_]+", code.value)
