"""Loudness and spectral measurements.

These are checked against signals whose spectrum is known by construction: a
sine's energy sits at one frequency, white noise's is spread evenly, and the two
must produce measurably different flatness. Nothing here asserts a *label* —
there are none to assert, by design.
"""

import math
import random

import numpy as np
import pytest

from app.services.audio_analysis.features import (
    dynamic_range_db,
    frame_features,
    spectral_features,
    zero_crossing_rate,
)

SAMPLE_RATE = 44100
FRAME = 4096


def sine(frequency: float, amplitude: float = 0.5) -> np.ndarray:
    index = np.arange(FRAME)
    return amplitude * np.sin(2 * np.pi * frequency * index / SAMPLE_RATE)


def noise(amplitude: float = 0.4, seed: int = 3) -> np.ndarray:
    generator = random.Random(seed)
    return np.asarray([generator.uniform(-amplitude, amplitude) for _ in range(FRAME)])


def test_rms_and_peak_of_a_known_sine() -> None:
    features = frame_features(sine(440.0, amplitude=0.5), SAMPLE_RATE)
    assert features.rms == pytest.approx(0.5 / math.sqrt(2), abs=0.01)
    assert features.peak == pytest.approx(0.5, abs=0.01)


def test_silence_measures_as_zero_everywhere() -> None:
    features = frame_features(np.zeros(FRAME), SAMPLE_RATE)
    assert features.rms == 0.0
    assert features.peak == 0.0
    assert features.spectral_centroid_hz == 0.0
    assert features.spectral_flatness == 0.0


def test_an_empty_frame_measures_as_zero_rather_than_raising() -> None:
    features = frame_features(np.zeros(0), SAMPLE_RATE)
    assert features.rms == 0.0
    assert features.spectral_rolloff_hz == 0.0


def test_the_centroid_of_a_sine_sits_at_its_frequency() -> None:
    for frequency in (220.0, 440.0, 1000.0):
        features = frame_features(sine(frequency), SAMPLE_RATE)
        assert features.spectral_centroid_hz == pytest.approx(frequency, rel=0.05)


def test_a_higher_tone_has_a_higher_centroid_and_rolloff() -> None:
    low = frame_features(sine(220.0), SAMPLE_RATE)
    high = frame_features(sine(3000.0), SAMPLE_RATE)
    assert high.spectral_centroid_hz > low.spectral_centroid_hz
    assert high.spectral_rolloff_hz > low.spectral_rolloff_hz


def test_noise_is_flatter_than_a_tone() -> None:
    """Flatness near 1 is noise-like, near 0 is tone-like. That is its whole job."""
    tone = frame_features(sine(440.0), SAMPLE_RATE)
    hiss = frame_features(noise(), SAMPLE_RATE)
    assert hiss.spectral_flatness > tone.spectral_flatness
    assert 0.0 <= tone.spectral_flatness <= 1.0
    assert 0.0 <= hiss.spectral_flatness <= 1.0


def test_noise_has_a_wider_bandwidth_than_a_tone() -> None:
    assert (
        frame_features(noise(), SAMPLE_RATE).spectral_bandwidth_hz
        > frame_features(sine(440.0), SAMPLE_RATE).spectral_bandwidth_hz
    )


def test_zero_crossing_rate_counts_sign_changes() -> None:
    assert zero_crossing_rate(np.zeros(0)) == 0.0
    assert zero_crossing_rate(np.asarray([1.0])) == 0.0
    assert zero_crossing_rate(np.asarray([1.0, -1.0, 1.0, -1.0])) == pytest.approx(1.0)
    assert zero_crossing_rate(np.asarray([1.0, 1.0, 1.0])) == 0.0


def test_zero_crossing_rate_rises_with_frequency() -> None:
    low = frame_features(sine(220.0), SAMPLE_RATE).zero_crossing_rate
    high = frame_features(sine(2200.0), SAMPLE_RATE).zero_crossing_rate
    assert high > low
    # Two crossings per period, over the sample rate.
    assert low == pytest.approx(2 * 220.0 / SAMPLE_RATE, rel=0.15)


def test_rolloff_holds_the_stated_share_of_energy() -> None:
    magnitude = np.asarray([1.0, 1.0, 1.0, 1.0, 1.0])
    frequencies = np.asarray([0.0, 100.0, 200.0, 300.0, 400.0])
    _, _, rolloff, _ = spectral_features(magnitude, frequencies)
    # 85% of a flat five-bin spectrum lands in the fifth bin.
    assert rolloff == pytest.approx(400.0)


def test_spectral_features_of_an_empty_spectrum_are_zero() -> None:
    assert spectral_features(np.zeros(8), np.arange(8, dtype=float)) == (0.0, 0.0, 0.0, 0.0)


def test_dynamic_range_needs_something_to_compare() -> None:
    assert dynamic_range_db(np.zeros(0)) is None
    assert dynamic_range_db(np.asarray([0.2])) is None
    assert dynamic_range_db(np.zeros(50)) is None


def test_dynamic_range_grows_with_the_spread_of_levels() -> None:
    steady = np.full(100, 0.2)
    varied = np.concatenate([np.full(50, 0.02), np.full(50, 0.5)])
    steady_range = dynamic_range_db(steady)
    varied_range = dynamic_range_db(varied)
    assert steady_range is not None and varied_range is not None
    assert steady_range == pytest.approx(0.0, abs=0.5)
    assert varied_range > 20.0
