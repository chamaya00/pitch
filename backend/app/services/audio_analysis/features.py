"""Per-frame loudness and spectral measurements.

Everything here is a **measurable characteristic of the signal**, reported as
what it is. Nothing in this module — or anywhere downstream of it — turns a
spectral number into a word like "bright", "dark", "breathy", "nasal",
"healthy" or "damaged". Those are classifications, they need a validated
classifier trained on labelled data, and no such thing exists in this project.
A spectral centroid is a spectral centroid.

Loudness here is **RMS and peak amplitude**, not LUFS. There is no
loudness-weighting filter, no gating and no reference level, so nothing derived
from these may be presented as broadcast- or mastering-grade measurement.

Pure: numpy in, floats out.
"""

from dataclasses import dataclass
from typing import Final

import numpy as np

#: Share of total spectral energy below the reported rolloff frequency.
ROLLOFF_PERCENTILE: Final = 0.85

#: Guards divisions where a frame is silent. Small enough not to shift a real
#: measurement, large enough to keep zeros out of the denominators.
_EPSILON: Final = 1e-12


@dataclass(frozen=True, slots=True)
class FrameFeatures:
    """Loudness and spectral shape of one frame."""

    rms: float
    peak: float
    zero_crossing_rate: float
    spectral_centroid_hz: float
    spectral_bandwidth_hz: float
    spectral_rolloff_hz: float
    spectral_flatness: float


def zero_crossing_rate(samples: np.ndarray) -> float:
    """Share of adjacent sample pairs that cross zero, 0–1."""
    if samples.size < 2:
        return 0.0
    signs = np.signbit(samples)
    return float(np.count_nonzero(signs[1:] != signs[:-1]) / (samples.size - 1))


def spectral_features(
    magnitude: np.ndarray, frequencies: np.ndarray
) -> tuple[float, float, float, float]:
    """Centroid, bandwidth, rolloff and flatness from one magnitude spectrum.

    Centroid is the energy-weighted mean frequency; bandwidth is the
    energy-weighted standard deviation about it; rolloff is the frequency below
    which :data:`ROLLOFF_PERCENTILE` of the energy lies; flatness is the ratio
    of geometric to arithmetic mean, near 1 for noise and near 0 for a tone.
    """
    total = float(magnitude.sum())
    if total <= _EPSILON:
        return 0.0, 0.0, 0.0, 0.0

    weights = magnitude / total
    centroid = float(np.dot(weights, frequencies))
    variance = float(np.dot(weights, np.square(frequencies - centroid)))
    bandwidth = float(np.sqrt(max(variance, 0.0)))

    cumulative = np.cumsum(magnitude)
    index = int(np.searchsorted(cumulative, ROLLOFF_PERCENTILE * total))
    rolloff = float(frequencies[min(index, frequencies.size - 1)])

    # Geometric mean via logs; adding epsilon keeps a single empty bin from
    # collapsing the whole frame to zero.
    positive = magnitude + _EPSILON
    geometric = float(np.exp(np.mean(np.log(positive))))
    arithmetic = float(np.mean(positive))
    flatness = float(geometric / arithmetic) if arithmetic > _EPSILON else 0.0

    return centroid, bandwidth, rolloff, flatness


def frame_features(samples: np.ndarray, sample_rate: int) -> FrameFeatures:
    """Measure one frame. ``samples`` is mono audio in [-1, 1]."""
    frame = np.asarray(samples, dtype=np.float64).ravel()
    if frame.size == 0:
        return FrameFeatures(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    rms = float(np.sqrt(np.mean(np.square(frame))))
    peak = float(np.max(np.abs(frame)))

    # Hann window before the FFT: a rectangular window smears energy across the
    # whole spectrum through its side lobes, which moves the centroid.
    windowed = frame * np.hanning(frame.size)
    magnitude = np.abs(np.fft.rfft(windowed))
    frequencies = np.fft.rfftfreq(frame.size, d=1.0 / sample_rate)
    centroid, bandwidth, rolloff, flatness = spectral_features(magnitude, frequencies)

    return FrameFeatures(
        rms=rms,
        peak=peak,
        zero_crossing_rate=zero_crossing_rate(frame),
        spectral_centroid_hz=centroid,
        spectral_bandwidth_hz=bandwidth,
        spectral_rolloff_hz=rolloff,
        spectral_flatness=flatness,
    )


def dynamic_range_db(frame_rms: np.ndarray) -> float | None:
    """A dynamic-range **estimate**: the 95th minus the 5th percentile, in dB.

    Percentiles rather than max-minus-min, because a single clipped sample or
    one silent frame would otherwise define the whole figure. It is an estimate
    of how much the level moves across the recording and nothing more — it is
    not a mastering measurement and not a loudness range (LRA), which is defined
    against a gated, loudness-weighted signal this does not compute.

    ``None`` when there are too few frames above silence to compare.
    """
    audible = frame_rms[frame_rms > _EPSILON]
    if audible.size < 2:
        return None
    high = float(np.percentile(audible, 95))
    low = float(np.percentile(audible, 5))
    if low <= _EPSILON or high <= _EPSILON:
        return None
    return float(20.0 * np.log10(high / low))
