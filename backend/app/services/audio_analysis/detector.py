"""Monophonic pitch detection over one frame of samples.

Method: the **normalised square difference function** (McLeod's MPM), with
parabolic interpolation around the chosen peak — the same algorithm the browser
runs live, for the same reason, and it is worth stating why rather than reaching
for the library default.

Plain autocorrelation is the obvious choice and fails in a specific, repeatable
way: its peaks grow at integer multiples of the true period, so a harmonically
rich signal — which is what a voice is — correlates at least as strongly at
twice the period as at the true one. Taking the tallest peak therefore reports
an octave too low, intermittently. Two changes fix it:

1. **Normalising** each lag by the energy actually overlapping at that lag,
   which removes the systematic bias towards longer lags.
2. **Taking the first peak** that clears a fraction of the global maximum,
   rather than the tallest. This is the part that actually prevents the octave
   error; normalisation alone still leaves near-ties.

Parabolic interpolation matters more than it looks: without it the period is
quantised to whole samples, which in the vocal range is worth tens of cents —
enough to make the cents readout meaningless.

The clarity value returned is a **measurement** — the height of the normalised
peak, 0 to 1 — not a confidence anyone invented. Below the threshold the frame
is unvoiced and carries no pitch at all: a detector always resolves *some* lag,
so silence would otherwise produce a confident wrong note.

librosa's ``pyin`` was the documented plan. It is not used, and the reason is
dependency weight rather than quality: librosa pulls in numba, scikit-learn and
soxr for one function, where this is ~60 lines of numpy that the browser half of
the product already had to implement anyway. If a real recording defeats it, the
failure case gets written down and pyin (or CREPE) is reconsidered then — not
before, and not on a hunch.

Pure: numpy in, numbers out. No I/O, no file formats, no domain models.
"""

from dataclasses import dataclass
from typing import Final

import numpy as np

#: Search range, in Hz. Below sits room rumble and handling noise; above sits
#: nothing a voice produces as a fundamental.
DEFAULT_MIN_HZ: Final = 65.0
DEFAULT_MAX_HZ: Final = 1100.0

#: Clarity below this is not a pitch. Deliberately strict: a wrong note reported
#: confidently is worse than an honest gap in the timeline.
DEFAULT_CLARITY_THRESHOLD: Final = 0.80

#: A peak must reach this share of the strongest peak to be accepted.
PEAK_ACCEPTANCE: Final = 0.85

#: Below this RMS the frame is silence and the arithmetic is skipped entirely.
SILENCE_RMS: Final = 0.005


@dataclass(frozen=True, slots=True)
class PitchEstimate:
    """One frame's worth of detection.

    ``frequency_hz`` is ``None`` whenever no pitch could be established — the
    frame was silent, too short, or nothing in it correlated well enough.
    """

    frequency_hz: float | None
    clarity: float
    rms: float


def root_mean_square(samples: np.ndarray) -> float:
    """RMS level of a frame, 0–1 for samples in [-1, 1]."""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def _next_power_of_two(value: int) -> int:
    return 1 << (value - 1).bit_length()


def normalised_square_difference(samples: np.ndarray) -> np.ndarray:
    """NSDF of a frame, for every lag from 0 to ``len(samples) - 1``.

    ``nsdf[lag] = 2·Σ x[i]·x[i+lag] / Σ (x[i]² + x[i+lag]²)``

    The autocorrelation numerator is computed by FFT rather than by a double
    loop: the direct form is O(n²) per frame, which at a few thousand frames per
    recording is the difference between seconds and minutes.
    """
    x = np.asarray(samples, dtype=np.float64)
    n = x.size
    if n < 2:
        return np.zeros(0, dtype=np.float64)

    # DC offset shifts every correlation by a constant and flattens the peaks.
    x = x - x.mean()

    size = _next_power_of_two(2 * n)
    spectrum = np.fft.rfft(x, size)
    correlation = np.fft.irfft(spectrum * np.conjugate(spectrum), size)[:n]

    # m[lag] = Σ_{i<n-lag} (x[i]² + x[i+lag]²), by a running update from m[0].
    power = np.square(x)
    total = float(power.sum())
    head = np.concatenate(([0.0], np.cumsum(power[:-1])))  # Σ x[0..lag-1]²
    tail = np.concatenate(([0.0], np.cumsum(power[::-1][:-1])))  # Σ x[n-lag..]²
    divisor = 2.0 * total - head - tail

    nsdf = np.zeros(n, dtype=np.float64)
    usable = divisor > 0
    nsdf[usable] = 2.0 * correlation[usable] / divisor[usable]
    return nsdf


def detect_pitch(
    samples: np.ndarray,
    sample_rate: int,
    *,
    min_hz: float = DEFAULT_MIN_HZ,
    max_hz: float = DEFAULT_MAX_HZ,
    silence_rms: float = SILENCE_RMS,
) -> PitchEstimate:
    """Estimate the pitch of one frame.

    ``samples`` is mono audio in [-1, 1]. A frame shorter than two periods of
    ``min_hz`` cannot support a result and returns ``None`` rather than a guess.
    """
    frame = np.asarray(samples, dtype=np.float64).ravel()
    rms = root_mean_square(frame)

    if sample_rate <= 0 or not np.isfinite(sample_rate):
        return PitchEstimate(None, 0.0, rms)
    if rms < silence_rms:
        return PitchEstimate(None, 0.0, rms)
    if not np.all(np.isfinite(frame)):
        # A decoder should never hand us these; if one does, refuse the frame
        # rather than propagate a NaN into the aggregates.
        return PitchEstimate(None, 0.0, 0.0)

    min_lag = max(2, int(np.floor(sample_rate / max_hz)))
    max_lag = min(int(np.floor(sample_rate / min_hz)), frame.size // 2)
    if max_lag <= min_lag:
        return PitchEstimate(None, 0.0, rms)

    nsdf = normalised_square_difference(frame)
    if nsdf.size <= max_lag:
        return PitchEstimate(None, 0.0, rms)

    window = nsdf[min_lag : max_lag + 1]
    strongest = float(window.max())
    if strongest <= 0.0:
        return PitchEstimate(None, 0.0, rms)

    chosen = _first_acceptable_peak(nsdf, min_lag, max_lag, strongest * PEAK_ACCEPTANCE)
    if chosen is None:
        return PitchEstimate(None, 0.0, rms)

    refined = _interpolate_peak(nsdf, chosen)
    if refined <= 0.0:
        return PitchEstimate(None, 0.0, rms)

    frequency = sample_rate / refined
    clarity = float(np.clip(nsdf[chosen], 0.0, 1.0))
    if frequency < min_hz or frequency > max_hz:
        return PitchEstimate(None, clarity, rms)

    return PitchEstimate(float(frequency), clarity, rms)


def _first_acceptable_peak(
    nsdf: np.ndarray, min_lag: int, max_lag: int, cutoff: float
) -> int | None:
    """The first local maximum at or above ``cutoff``. See the module docstring."""
    rising = False
    for lag in range(min_lag + 1, max_lag + 1):
        if nsdf[lag] > nsdf[lag - 1]:
            rising = True
            continue
        if rising and nsdf[lag - 1] >= cutoff:
            return lag - 1
        rising = False
    return None


def _interpolate_peak(nsdf: np.ndarray, lag: int) -> float:
    """Sub-sample lag of the peak, by a parabola through its three points."""
    if lag <= 0 or lag + 1 >= nsdf.size:
        return float(lag)
    previous, current, following = nsdf[lag - 1], nsdf[lag], nsdf[lag + 1]
    denominator = previous - 2.0 * current + following
    if denominator == 0.0:
        return float(lag)
    offset = 0.5 * (previous - following) / denominator
    if not np.isfinite(offset):
        return float(lag)
    return float(lag + offset)
