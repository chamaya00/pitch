"""The pitch detector, driven with signals whose true pitch we chose ourselves.

A pure sine is the weakest possible test — every method gets it right. The tests
that matter here use a **harmonic** tone, because that is the signal that breaks
plain autocorrelation by correlating as strongly at twice the true period as at
the period itself. If the octave guard ever regresses, ``test_harmonic_tone_is_
not_detected_an_octave_low`` is what catches it.

Tolerances in cents throughout, never floating-point equality.
"""

import math
import random

import numpy as np
import pytest

from app.services.audio_analysis.detector import (
    DEFAULT_CLARITY_THRESHOLD,
    SILENCE_RMS,
    detect_pitch,
    normalised_square_difference,
    root_mean_square,
)

SAMPLE_RATE = 44100
FRAME = 4096


def sine(frequency: float, *, amplitude: float = 0.5, length: int = FRAME) -> np.ndarray:
    index = np.arange(length)
    return amplitude * np.sin(2 * np.pi * frequency * index / SAMPLE_RATE)


def harmonic(frequency: float, *, length: int = FRAME) -> np.ndarray:
    """A tone with five partials, the way a voice has them."""
    index = np.arange(length)
    phase = 2 * np.pi * frequency * index / SAMPLE_RATE
    gains = [1.0, 0.6, 0.4, 0.25, 0.15]
    signal = sum(gain * np.sin((n + 1) * phase) for n, gain in enumerate(gains))
    return 0.5 * signal / sum(gains)


def noise(*, amplitude: float = 0.4, length: int = FRAME, seed: int = 7) -> np.ndarray:
    generator = random.Random(seed)
    return np.asarray([generator.uniform(-amplitude, amplitude) for _ in range(length)])


def cents_between(measured: float, expected: float) -> float:
    return 1200 * math.log2(measured / expected)


def test_440_hz_is_detected_as_a4() -> None:
    estimate = detect_pitch(sine(440.0), SAMPLE_RATE)
    assert estimate.frequency_hz is not None
    assert abs(cents_between(estimate.frequency_hz, 440.0)) < 5
    assert estimate.clarity >= DEFAULT_CLARITY_THRESHOLD


def test_261_626_hz_is_detected_as_c4() -> None:
    estimate = detect_pitch(sine(261.626), SAMPLE_RATE)
    assert estimate.frequency_hz is not None
    assert abs(cents_between(estimate.frequency_hz, 261.626)) < 5


@pytest.mark.parametrize("frequency", [82.41, 110.0, 146.83, 196.0, 261.626, 440.0, 880.0])
def test_harmonic_tone_is_not_detected_an_octave_low(frequency: float) -> None:
    """The regression this detector exists for.

    A method that takes the tallest autocorrelation peak reports half these
    frequencies. NSDF plus first-acceptable-peak reports them.
    """
    estimate = detect_pitch(harmonic(frequency), SAMPLE_RATE)
    assert estimate.frequency_hz is not None, f"no detection at {frequency} Hz"
    error = cents_between(estimate.frequency_hz, frequency)
    assert abs(error) < 20, f"{frequency} Hz read as {estimate.frequency_hz:.1f} Hz"


@pytest.mark.parametrize("frequency", [70.0, 90.0, 130.81])
def test_low_frequencies_within_the_search_range(frequency: float) -> None:
    """The bottom of the range needs a long frame — two periods of 65 Hz."""
    estimate = detect_pitch(harmonic(frequency, length=8192), SAMPLE_RATE)
    assert estimate.frequency_hz is not None
    assert abs(cents_between(estimate.frequency_hz, frequency)) < 25


@pytest.mark.parametrize("frequency", [700.0, 900.0, 1046.5])
def test_high_frequencies_within_the_search_range(frequency: float) -> None:
    estimate = detect_pitch(sine(frequency), SAMPLE_RATE)
    assert estimate.frequency_hz is not None
    assert abs(cents_between(estimate.frequency_hz, frequency)) < 15


def test_frequencies_outside_the_search_range_are_refused() -> None:
    """40 Hz and 2 kHz are real tones, and neither is a fundamental we report."""
    for frequency in (40.0, 2000.0):
        estimate = detect_pitch(sine(frequency, length=8192), SAMPLE_RATE)
        assert estimate.frequency_hz is None or not (
            frequency * 0.9 < estimate.frequency_hz < frequency * 1.1
        )


def test_silence_produces_no_pitch() -> None:
    estimate = detect_pitch(np.zeros(FRAME), SAMPLE_RATE)
    assert estimate.frequency_hz is None
    assert estimate.clarity == 0.0
    assert estimate.rms == 0.0


def test_a_signal_below_the_silence_gate_produces_no_pitch() -> None:
    estimate = detect_pitch(sine(440.0, amplitude=SILENCE_RMS / 4), SAMPLE_RATE)
    assert estimate.frequency_hz is None


def test_white_noise_does_not_reach_the_clarity_threshold() -> None:
    estimate = detect_pitch(noise(), SAMPLE_RATE)
    assert estimate.frequency_hz is None or estimate.clarity < DEFAULT_CLARITY_THRESHOLD


def test_a_frame_too_short_to_hold_a_period_returns_nothing() -> None:
    estimate = detect_pitch(sine(440.0, length=64), SAMPLE_RATE)
    assert estimate.frequency_hz is None


def test_an_empty_frame_returns_nothing_rather_than_raising() -> None:
    estimate = detect_pitch(np.zeros(0), SAMPLE_RATE)
    assert estimate.frequency_hz is None
    assert estimate.rms == 0.0


@pytest.mark.parametrize("rate", [0, -44100])
def test_an_impossible_sample_rate_returns_nothing(rate: int) -> None:
    estimate = detect_pitch(sine(440.0), rate)
    assert estimate.frequency_hz is None


def test_non_finite_samples_are_refused_rather_than_propagated() -> None:
    """A NaN reaching the aggregates would poison every mean downstream."""
    frame = sine(440.0)
    frame[100] = np.nan
    estimate = detect_pitch(frame, SAMPLE_RATE)
    assert estimate.frequency_hz is None


def test_detection_survives_a_dc_offset() -> None:
    """A constant offset flattens every correlation peak if it is not removed."""
    estimate = detect_pitch(sine(220.0) + 0.4, SAMPLE_RATE)
    assert estimate.frequency_hz is not None
    assert abs(cents_between(estimate.frequency_hz, 220.0)) < 10


def test_detection_survives_moderate_noise() -> None:
    """12 dB SNR is a realistic room, and the threshold is set to survive it."""
    signal = harmonic(196.0) + noise(amplitude=0.05)
    estimate = detect_pitch(signal, SAMPLE_RATE)
    assert estimate.frequency_hz is not None
    assert abs(cents_between(estimate.frequency_hz, 196.0)) < 25


def test_clipped_audio_is_still_detected() -> None:
    """Clipping adds harmonics. It must not move the fundamental."""
    clipped = np.clip(harmonic(220.0) * 6.0, -1.0, 1.0)
    estimate = detect_pitch(clipped, SAMPLE_RATE)
    assert estimate.frequency_hz is not None
    assert abs(cents_between(estimate.frequency_hz, 220.0)) < 20


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 48000])
def test_detection_works_at_other_sample_rates(rate: int) -> None:
    index = np.arange(4096)
    frame = 0.5 * np.sin(2 * np.pi * 220.0 * index / rate)
    estimate = detect_pitch(frame, rate)
    assert estimate.frequency_hz is not None, f"no detection at {rate} Hz"
    assert abs(cents_between(estimate.frequency_hz, 220.0)) < 15


def test_root_mean_square_measures_level() -> None:
    assert root_mean_square(np.zeros(0)) == 0.0
    assert root_mean_square(np.zeros(128)) == 0.0
    # A sine of amplitude a has RMS a/sqrt(2).
    assert root_mean_square(sine(440.0, amplitude=0.5)) == pytest.approx(
        0.5 / math.sqrt(2), abs=0.01
    )


def test_nsdf_peaks_at_the_period_and_is_bounded() -> None:
    frame = sine(441.0)  # exactly 100 samples per period at 44.1 kHz
    nsdf = normalised_square_difference(frame)
    assert nsdf.size == frame.size
    assert nsdf.max() <= 1.0 + 1e-9
    assert nsdf.min() >= -1.0 - 1e-9
    assert nsdf[100] == pytest.approx(1.0, abs=0.01)


def test_nsdf_of_a_degenerate_frame_is_empty_rather_than_an_error() -> None:
    assert normalised_square_difference(np.zeros(0)).size == 0
    assert normalised_square_difference(np.zeros(1)).size == 0
