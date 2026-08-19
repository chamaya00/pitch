"""The analyzer, end to end, against real files on disk.

Every fixture here is written as a genuine WAV and decoded by the real decoder,
so these exercise the whole path — header, streaming, framing, detection,
aggregation — rather than a mock of it.

The important cases are the ones where the honest answer is "no":

* silence and noise must fail with ``INSUFFICIENT_PITCH_SIGNAL``, not report a
  range of zero;
* a corrupt or truncated file must fail with a code, not raise;
* a very short file must say it is short.
"""

import contextlib
import math
from pathlib import Path

import pytest

from app.core.errors import ErrorCode
from app.services.audio_analysis.analyzer import (
    IN_TUNE_CENTS,
    MIN_RANGE_FRAMES,
    SignalAudioAnalyzer,
)
from app.services.audio_analysis.errors import (
    AudioTooShortError,
    AudioUnsupportedError,
    InsufficientPitchSignalError,
)
from app.services.audio_analysis.pitch import note_name_for_midi
from tests.fixtures import (
    harmonic_samples,
    noise_samples,
    silence_samples,
    write_signal_wav,
    write_wav,
)

SAMPLE_RATE = 22050


@pytest.fixture
def analyzer() -> SignalAudioAnalyzer:
    return SignalAudioAnalyzer()


def tone_file(path: Path, frequency: float, *, seconds: float = 2.0) -> Path:
    return write_signal_wav(
        path,
        harmonic_samples(frequency, seconds=seconds, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )


def phrase_file(path: Path, *frequencies: float, seconds_per_note: float = 0.6) -> Path:
    """Several notes in a row, for properties that a single held note cannot show."""
    samples: list[float] = []
    for frequency in frequencies:
        samples += harmonic_samples(frequency, seconds=seconds_per_note, sample_rate=SAMPLE_RATE)
    return write_signal_wav(path, samples, sample_rate=SAMPLE_RATE)


def cents_between(measured: float, expected: float) -> float:
    return 1200 * math.log2(measured / expected)


# --- The happy path --------------------------------------------------------


def test_a_held_a4_is_measured_as_a4(analyzer: SignalAudioAnalyzer, tmp_path: Path) -> None:
    result = analyzer.analyze(tone_file(tmp_path / "a4.wav", 440.0))

    assert result.metrics.pitch is not None
    assert result.metrics.pitch.lowest_note == "A4"
    assert result.metrics.pitch.highest_note == "A4"
    assert result.metrics.pitch.semitone_span == 0
    assert result.metrics.stability.voiced_ratio > 0.9
    assert all(point.note_name == "A4" for point in result.pitch_points)


def test_every_point_the_analyzer_writes_is_named_by_its_own_number(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    """The property the two aggregations rely on, asserted where the names are made.

    Since Step 10.15 the note breakdown derives a note's name from the stored
    ``midi_note`` rather than reading the ``note_name`` beside it — the name is a
    fact about the number, and reading it would mean sending 12 931 strings the
    fold can compute. That is only sound while the writer agrees, which it does
    by construction: it names each point with the same function from the same
    integer. This is the test that would fail if that ever stopped being true,
    and it runs against real decoded audio rather than a fixture.
    """
    #  C4       E4       G4       B4       C5
    result = analyzer.analyze(
        phrase_file(tmp_path / "phrase.wav", 261.626, 329.628, 391.995, 493.883, 523.251)
    )

    assert result.pitch_points
    for point in result.pitch_points:
        assert point.note_name == note_name_for_midi(point.midi_note)


def test_a_held_c4_is_measured_as_c4(analyzer: SignalAudioAnalyzer, tmp_path: Path) -> None:
    result = analyzer.analyze(tone_file(tmp_path / "c4.wav", 261.626))
    assert result.metrics.pitch is not None
    assert result.metrics.pitch.lowest_note == "C4"
    for point in result.pitch_points:
        assert abs(cents_between(point.frequency_hz, 261.626)) < 20


def test_the_timeline_is_ordered_and_covers_the_recording(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    result = analyzer.analyze(tone_file(tmp_path / "tone.wav", 220.0, seconds=3.0))
    times = [point.timestamp_seconds for point in result.pitch_points]

    assert times == sorted(times)
    assert times[0] < 0.2
    assert times[-1] > 2.5
    assert result.metrics.duration_seconds == pytest.approx(3.0, abs=0.05)


def test_every_timeline_point_carries_a_measured_confidence(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    result = analyzer.analyze(tone_file(tmp_path / "tone.wav", 330.0))
    threshold = result.metrics.settings.clarity_threshold
    assert all(point.confidence >= threshold for point in result.pitch_points)
    assert all(0.0 <= point.confidence <= 1.0 for point in result.pitch_points)


def test_the_settings_used_are_recorded_on_the_result(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    """Without these the numbers are not interpretable later."""
    result = analyzer.analyze(tone_file(tmp_path / "tone.wav", 440.0))
    settings = result.metrics.settings
    assert settings.sample_rate_hz == SAMPLE_RATE
    assert settings.frame_length_samples > settings.hop_length_samples
    assert 0 < settings.clarity_threshold <= 1


def test_a_two_note_recording_reports_the_span_between_them(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    samples = harmonic_samples(220.0, seconds=1.5, sample_rate=SAMPLE_RATE) + harmonic_samples(
        440.0, seconds=1.5, sample_rate=SAMPLE_RATE
    )
    path = write_signal_wav(tmp_path / "two.wav", samples, sample_rate=SAMPLE_RATE)

    result = analyzer.analyze(path)
    assert result.metrics.pitch is not None
    assert result.metrics.pitch.lowest_note == "A3"
    assert result.metrics.pitch.highest_note == "A4"
    assert result.metrics.pitch.semitone_span == 12


def test_a_momentary_glitch_does_not_widen_the_reported_range(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    """One frame of something else must not become the bottom of a range.

    A pitch has to be *held* to count, so a two-frame excursion is measured in
    the timeline but excluded from the range. Without that rule a single
    transient decides how wide someone's voice is reported to be.
    """
    glitch_seconds = 0.02  # shorter than MIN_RANGE_FRAMES hops
    samples = (
        harmonic_samples(440.0, seconds=1.0, sample_rate=SAMPLE_RATE)
        + harmonic_samples(110.0, seconds=glitch_seconds, sample_rate=SAMPLE_RATE)
        + harmonic_samples(440.0, seconds=1.0, sample_rate=SAMPLE_RATE)
    )
    path = write_signal_wav(tmp_path / "glitch.wav", samples, sample_rate=SAMPLE_RATE)

    result = analyzer.analyze(path)
    assert result.metrics.pitch is not None
    assert result.metrics.pitch.lowest_note == "A4"
    assert result.metrics.pitch.semitone_span == 0
    assert MIN_RANGE_FRAMES > 1  # the rule this test depends on


def test_a_steady_tone_is_in_tune_with_itself(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    result = analyzer.analyze(tone_file(tmp_path / "tone.wav", 440.0))
    stability = result.metrics.stability

    assert stability.in_tune_ratio == pytest.approx(1.0)
    assert stability.mean_abs_cents_deviation is not None
    assert stability.mean_abs_cents_deviation < IN_TUNE_CENTS
    assert stability.cents_std is not None
    assert stability.cents_std < 10
    assert stability.unstable_sections == ()


def test_stability_counts_add_up(analyzer: SignalAudioAnalyzer, tmp_path: Path) -> None:
    result = analyzer.analyze(tone_file(tmp_path / "tone.wav", 440.0))
    stability = result.metrics.stability

    assert stability.voiced_frames <= stability.total_frames
    assert stability.voiced_ratio == pytest.approx(
        stability.voiced_frames / stability.total_frames, abs=1e-9
    )
    # Every timeline point is a voiced frame that survived outlier rejection.
    assert len(result.pitch_points) == stability.voiced_frames


def test_half_silence_halves_the_voiced_ratio(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    samples = harmonic_samples(440.0, seconds=2.0, sample_rate=SAMPLE_RATE) + silence_samples(
        seconds=2.0, sample_rate=SAMPLE_RATE
    )
    path = write_signal_wav(tmp_path / "half.wav", samples, sample_rate=SAMPLE_RATE)

    stability = analyzer.analyze(path).metrics.stability
    assert 0.35 < stability.voiced_ratio < 0.65


def test_unvoiced_frames_are_omitted_from_the_timeline_not_nulled(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    """The documented choice. A gap in the timeline is silence, and means it."""
    samples = (
        harmonic_samples(440.0, seconds=1.0, sample_rate=SAMPLE_RATE)
        + silence_samples(seconds=1.0, sample_rate=SAMPLE_RATE)
        + harmonic_samples(440.0, seconds=1.0, sample_rate=SAMPLE_RATE)
    )
    path = write_signal_wav(tmp_path / "gap.wav", samples, sample_rate=SAMPLE_RATE)

    result = analyzer.analyze(path)
    times = [point.timestamp_seconds for point in result.pitch_points]
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert max(gaps) > 0.5, "the silent second should appear as a gap"
    assert all(point.frequency_hz > 0 for point in result.pitch_points)


# --- Loudness and spectrum -------------------------------------------------


def test_loudness_is_measured_not_normalised(analyzer: SignalAudioAnalyzer, tmp_path: Path) -> None:
    quiet = write_signal_wav(
        tmp_path / "quiet.wav",
        harmonic_samples(440.0, seconds=1.5, sample_rate=SAMPLE_RATE, amplitude=0.05),
        sample_rate=SAMPLE_RATE,
    )
    loud = write_signal_wav(
        tmp_path / "loud.wav",
        harmonic_samples(440.0, seconds=1.5, sample_rate=SAMPLE_RATE, amplitude=0.8),
        sample_rate=SAMPLE_RATE,
    )

    assert (
        analyzer.analyze(quiet).metrics.loudness.rms < analyzer.analyze(loud).metrics.loudness.rms
    )


def test_clipping_is_reported_as_a_measurement(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    """A ratio, not a verdict. The file is analysed either way."""
    clipped = [
        max(-1.0, min(1.0, value * 8))
        for value in harmonic_samples(220.0, seconds=1.5, sample_rate=SAMPLE_RATE)
    ]
    path = write_signal_wav(tmp_path / "clipped.wav", clipped, sample_rate=SAMPLE_RATE)

    result = analyzer.analyze(path)
    assert result.metrics.loudness.clipped_sample_ratio > 0.0
    assert result.metrics.loudness.peak == pytest.approx(1.0, abs=0.01)
    assert result.metrics.pitch is not None  # still measurable


def test_spectral_features_are_present_for_audible_material(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    result = analyzer.analyze(tone_file(tmp_path / "tone.wav", 440.0))
    spectral = result.metrics.spectral
    assert spectral is not None
    assert spectral.centroid_hz > 0
    assert 0.0 <= spectral.flatness <= 1.0
    assert 0.0 <= spectral.zero_crossing_rate <= 1.0


# --- Honest failures -------------------------------------------------------


def test_silence_reports_insufficient_pitch_rather_than_an_empty_range(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    path = write_signal_wav(
        tmp_path / "silence.wav",
        silence_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    with pytest.raises(InsufficientPitchSignalError) as caught:
        analyzer.analyze(path)
    assert caught.value.error_code is ErrorCode.INSUFFICIENT_PITCH_SIGNAL


def test_white_noise_reports_insufficient_pitch(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    path = write_signal_wav(
        tmp_path / "noise.wav",
        noise_samples(seconds=2.0, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    with pytest.raises(InsufficientPitchSignalError):
        analyzer.analyze(path)


def test_a_very_short_recording_says_it_is_short(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    path = write_signal_wav(
        tmp_path / "blip.wav",
        harmonic_samples(440.0, seconds=0.05, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    with pytest.raises(AudioTooShortError) as caught:
        analyzer.analyze(path)
    assert caught.value.error_code is ErrorCode.AUDIO_TOO_SHORT


def test_a_recording_shorter_than_one_frame_says_it_is_short(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    path = write_signal_wav(
        tmp_path / "tiny.wav",
        harmonic_samples(440.0, seconds=0.3, sample_rate=SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
    )
    # 0.3 s clears the duration floor but is only ~3 frames. Either outcome is
    # acceptable; raising something unhandled is not, and that is what this pins.
    with contextlib.suppress(AudioTooShortError, InsufficientPitchSignalError):
        analyzer.analyze(path)


def test_a_corrupt_file_fails_with_a_code_rather_than_raising(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"RIFF" + b"\x00" * 200)
    with pytest.raises(AudioUnsupportedError) as caught:
        analyzer.analyze(path)
    assert caught.value.error_code is ErrorCode.AUDIO_UNSUPPORTED


def test_a_missing_file_fails_with_a_code_rather_than_raising(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    with pytest.raises(AudioUnsupportedError):
        analyzer.analyze(tmp_path / "nothing-here.wav")


def test_a_failure_message_never_names_a_path(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    """A decoder's own message can carry the server's filesystem layout."""
    secret = tmp_path / "very-secret-directory" / "corrupt.wav"
    secret.parent.mkdir()
    secret.write_bytes(b"RIFF" + b"\x00" * 200)

    with pytest.raises(AudioUnsupportedError) as caught:
        analyzer.analyze(secret)

    error = caught.value
    assert "secret" not in str(error)
    assert "secret" not in error.to_api_error().message
    assert "secret" not in "".join(error.log_context().values())


# --- Other formats and shapes ----------------------------------------------


def test_a_stereo_recording_is_analysed_as_one_voice(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    path = write_wav(
        tmp_path / "stereo.wav", seconds=2.0, sample_rate=16000, channels=2, frequency=440.0
    )
    result = analyzer.analyze(path)
    assert result.metrics.pitch is not None
    assert result.metrics.pitch.lowest_note == "A4"


@pytest.mark.parametrize("rate", [8000, 16000, 44100])
def test_analysis_works_at_other_sample_rates(
    analyzer: SignalAudioAnalyzer, tmp_path: Path, rate: int
) -> None:
    path = write_signal_wav(
        tmp_path / f"tone-{rate}.wav",
        harmonic_samples(220.0, seconds=1.5, sample_rate=rate),
        sample_rate=rate,
    )
    result = analyzer.analyze(path)
    assert result.metrics.pitch is not None
    assert result.metrics.pitch.lowest_note == "A3"
    assert result.metrics.settings.sample_rate_hz == rate


def test_time_resolution_is_the_same_whatever_the_sample_rate(
    analyzer: SignalAudioAnalyzer, tmp_path: Path
) -> None:
    """Frame and hop are defined in seconds, so results stay comparable."""
    counts = []
    for rate in (16000, 44100):
        path = write_signal_wav(
            tmp_path / f"same-{rate}.wav",
            harmonic_samples(330.0, seconds=2.0, sample_rate=rate),
            sample_rate=rate,
        )
        counts.append(analyzer.analyze(path).metrics.stability.total_frames)
    assert abs(counts[0] - counts[1]) <= 2
