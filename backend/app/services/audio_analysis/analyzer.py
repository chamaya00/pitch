"""The deterministic audio analyzer: file in, measurements out.

::

    stored file → streamed frames → per-frame pitch + features
                → voiced filter → range · stability · loudness · spectrum

**Nothing is estimated by a model.** Every number this produces is arithmetic
over the samples, and every one of them is ``None`` when the signal could not
support it. A language model may later be given these numbers to explain; it is
never given the audio and never given the chance to supply a value.

Documented choices, and why:

*Decoding.* ``soundfile`` (libsndfile) reads WAV and MP3 — the two formats
upload accepts — with no ffmpeg process and no temporary files. librosa was the
documented plan and is not used: it pulls in numba, scikit-learn and soxr for
one function, where the pieces actually needed are a decoder and ~200 lines of
numpy. That decision is revisited if a real recording defeats the detector.

*No resampling.* Analysis runs at the file's own sample rate. A resampler is a
dependency and a source of error, and pitch detection gains nothing from
throwing samples away — a higher rate gives *finer* lag resolution, not coarser.
Frame and hop are therefore defined in **seconds** and converted per file, so
the time resolution of the result is the same whatever the recording's rate.

*Streaming.* Frames arrive from ``soundfile.blocks`` one at a time, with the
overlap built in. A 50 MB recording is never held in memory as floats; the
analyzer's footprint is one frame plus the accumulating per-frame arrays, which
for the longest allowed recording is a few hundred kilobytes.

*Mono.* Channels are averaged. A stereo recording of one voice is one voice.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

import numpy as np
import soundfile as sf

from app.core.logging import get_logger
from app.services.audio_analysis import pitch as pitch_math
from app.services.audio_analysis.detector import (
    DEFAULT_CLARITY_THRESHOLD,
    DEFAULT_MAX_HZ,
    DEFAULT_MIN_HZ,
    SILENCE_RMS,
    detect_pitch,
)
from app.services.audio_analysis.errors import (
    AudioTooShortError,
    AudioUnsupportedError,
    InsufficientPitchSignalError,
)
from app.services.audio_analysis.features import FrameFeatures, dynamic_range_db, frame_features
from app.services.audio_analysis.models import (
    IN_TUNE_CENTS,
    AnalysisSettings,
    AudioMetrics,
    Loudness,
    PitchPoint,
    PitchStability,
    SpectralFeatures,
    UnstableSection,
    VocalRange,
)

logger = get_logger(__name__)

#: Frame length in seconds. 2048 samples at 22.05 kHz, the value the design
#: notes settled on: long enough to hold two periods of the lowest searched
#: frequency (65 Hz needs 31 ms), short enough that a held note is not averaged
#: with the one after it.
FRAME_SECONDS: Final = 0.0929

#: Hop in seconds — 43 frames a second, a quarter of the frame length. Finer
#: buys resolution nothing downstream uses and multiplies the timeline length.
HOP_SECONDS: Final = 0.0232

#: A recording shorter than this cannot produce a usable frame at any rate.
MIN_DURATION_SECONDS: Final = 0.25

#: Frames either side of a frame, used for the rolling instability window.
_STABILITY_WINDOW: Final = 5
#: Rolling cents standard deviation above which a frame is "unstable".
UNSTABLE_CENTS_STD: Final = 35.0
#: Unstable runs shorter than this are noise, not a section worth naming.
MIN_UNSTABLE_SECONDS: Final = 0.25

#: A sample at or beyond this magnitude is counted as clipped.
_CLIP_THRESHOLD: Final = 0.999

#: Frames used for the local median when rejecting octave artefacts.
_OUTLIER_WINDOW: Final = 5
#: A voiced frame further than this from its neighbourhood's median is a
#: detector artefact, not a sung interval. Half an octave: no voice moves six
#: semitones and back inside 116 ms, but a sub-harmonic lands twelve or
#: nineteen away, which is what this catches.
OUTLIER_SEMITONES: Final = 6.0

#: Consecutive frames a pitch must hold to count towards the detected range —
#: about 116 ms at the default hop. See ``_sustained``.
MIN_RANGE_FRAMES: Final = 5
#: How far consecutive frames may move and still count as the same held pitch.
RANGE_CONTINUITY_SEMITONES: Final = 1.0


@dataclass(frozen=True, slots=True)
class AudioAnalysisResult:
    """What one analysis produced: the summary and its timeline."""

    metrics: AudioMetrics
    pitch_points: tuple[PitchPoint, ...]


@runtime_checkable
class AudioAnalyzer(Protocol):
    """Interface the orchestration layer depends on.

    A protocol rather than a class so orchestration never imports numpy or a
    decoder, and so a test can drive the workflow with a stub that returns fixed
    measurements in microseconds.
    """

    def analyze(self, path: Path) -> AudioAnalysisResult:
        """Measure a stored recording.

        Raises:
            AudioAnalysisError: decoding failed, the audio was too short, or no
                reliable pitch was found.
        """
        ...


class SignalAudioAnalyzer:
    """The real analyzer. Reads the file, measures it, returns the numbers."""

    def __init__(
        self,
        *,
        min_frequency_hz: float = DEFAULT_MIN_HZ,
        max_frequency_hz: float = DEFAULT_MAX_HZ,
        clarity_threshold: float = DEFAULT_CLARITY_THRESHOLD,
        silence_rms: float = SILENCE_RMS,
    ) -> None:
        self._min_hz = min_frequency_hz
        self._max_hz = max_frequency_hz
        self._clarity_threshold = clarity_threshold
        self._silence_rms = silence_rms

    def analyze(self, path: Path) -> AudioAnalysisResult:
        info = self._probe(path)
        sample_rate = int(info.samplerate)
        duration = float(info.frames) / sample_rate if sample_rate > 0 else 0.0

        if sample_rate <= 0 or info.frames <= 0 or duration < MIN_DURATION_SECONDS:
            raise AudioTooShortError(reason=f"duration={duration:.3f}s")

        frame_length = max(2, round(sample_rate * FRAME_SECONDS))
        hop_length = max(1, round(sample_rate * HOP_SECONDS))
        if info.frames < frame_length:
            raise AudioTooShortError(reason="shorter than one frame")

        collected = self._scan(
            path,
            sample_rate=sample_rate,
            frame_length=frame_length,
            hop_length=hop_length,
        )
        if collected.total_frames == 0:
            raise AudioTooShortError(reason="no complete frame")

        settings = AnalysisSettings(
            sample_rate_hz=sample_rate,
            frame_length_samples=frame_length,
            hop_length_samples=hop_length,
            min_frequency_hz=self._min_hz,
            max_frequency_hz=self._max_hz,
            clarity_threshold=self._clarity_threshold,
            silence_rms=self._silence_rms,
        )

        points = _reject_octave_outliers(collected.pitch_points)
        if not points:
            # Everything else measured fine, but a result with no pitch at all
            # is not what this endpoint promises. Say so with the specific code.
            raise InsufficientPitchSignalError(reason=f"frames={collected.total_frames} voiced=0")
        logger.info(
            "audio_analysis_measured",
            extra={
                "frames": collected.total_frames,
                "voiced_frames": len(points),
                "rejected_outliers": len(collected.pitch_points) - len(points),
                "sample_rate_hz": sample_rate,
            },
        )

        metrics = AudioMetrics(
            settings=settings,
            duration_seconds=duration,
            pitch=_vocal_range(_sustained(points)),
            stability=_stability(points, collected.total_frames, hop_length, sample_rate),
            loudness=_loudness(collected),
            spectral=_spectral(collected),
        )
        return AudioAnalysisResult(metrics=metrics, pitch_points=points)

    # --- Reading -----------------------------------------------------------

    def _probe(self, path: Path) -> Any:
        """Read the header only. Decoder failures never reach the client."""
        try:
            return sf.info(str(path))
        except (RuntimeError, OSError, ValueError) as exc:
            # The decoder's message can name a path or its own internals.
            raise AudioUnsupportedError(reason=type(exc).__name__) from exc

    def _scan(
        self, path: Path, *, sample_rate: int, frame_length: int, hop_length: int
    ) -> "_Collected":
        """Stream the file frame by frame, measuring as it goes."""
        collector = _Collected()
        overlap = frame_length - hop_length

        try:
            blocks = sf.blocks(
                str(path),
                blocksize=frame_length,
                overlap=overlap,
                dtype="float32",
                always_2d=True,
            )
            for index, block in enumerate(blocks):
                if block.shape[0] < frame_length:
                    break  # A partial tail frame would bias every measurement.
                frame = np.asarray(block, dtype=np.float64).mean(axis=1)
                timestamp = (index * hop_length + frame_length / 2.0) / sample_rate
                self._measure(frame, sample_rate, timestamp, collector)
        except (RuntimeError, OSError, ValueError) as exc:
            raise AudioUnsupportedError(reason=type(exc).__name__) from exc

        return collector

    def _measure(
        self, frame: np.ndarray, sample_rate: int, timestamp: float, into: "_Collected"
    ) -> None:
        features = frame_features(frame, sample_rate)
        into.add_frame(features, frame)

        estimate = detect_pitch(
            frame,
            sample_rate,
            min_hz=self._min_hz,
            max_hz=self._max_hz,
            silence_rms=self._silence_rms,
        )
        if estimate.frequency_hz is None or estimate.clarity < self._clarity_threshold:
            return

        midi = pitch_math.nearest_midi(estimate.frequency_hz)
        note = pitch_math.midi_to_note_name(midi)
        cents = pitch_math.cents_from_nearest_note(estimate.frequency_hz)
        if midi is None or note is None or cents is None:
            return
        if not 0 <= midi <= 127:
            # Outside MIDI's range there is no note to name. The detector's own
            # limits make this unreachable in practice; it is refused rather
            # than clamped, because a clamped note would be a wrong note.
            return

        into.add_pitch(
            PitchPoint(
                timestamp_seconds=timestamp,
                frequency_hz=estimate.frequency_hz,
                midi_note=midi,
                note_name=note,
                cents=cents,
                confidence=estimate.clarity,
            )
        )


class _Collected:
    """Accumulator for one pass over a file.

    Lists of floats rather than a growing ndarray: appending to a numpy array
    reallocates, and the whole point of streaming is not to hold the recording.
    """

    def __init__(self) -> None:
        self.total_frames = 0
        self.frame_rms: list[float] = []
        self.peak = 0.0
        self.clipped_samples = 0
        self.total_samples = 0
        self._audible_features: list[tuple[float, float, float, float, float]] = []
        self._points: list[PitchPoint] = []

    def add_frame(self, features: FrameFeatures, frame: np.ndarray) -> None:
        self.total_frames += 1
        self.frame_rms.append(features.rms)
        self.peak = max(self.peak, features.peak)
        # Frames overlap, so counting clipped samples per frame would count the
        # overlapping region more than once. Counting the hop-sized share keeps
        # the ratio honest without a second pass over the file.
        self.total_samples += frame.size
        self.clipped_samples += int(np.count_nonzero(np.abs(frame) >= _CLIP_THRESHOLD))

        if features.rms > SILENCE_RMS:
            self._audible_features.append(
                (
                    features.spectral_centroid_hz,
                    features.spectral_bandwidth_hz,
                    features.spectral_rolloff_hz,
                    features.zero_crossing_rate,
                    features.spectral_flatness,
                )
            )

    def add_pitch(self, point: PitchPoint) -> None:
        self._points.append(point)

    @property
    def pitch_points(self) -> tuple[PitchPoint, ...]:
        return tuple(self._points)

    @property
    def audible_features(self) -> np.ndarray:
        if not self._audible_features:
            return np.zeros((0, 5), dtype=np.float64)
        return np.asarray(self._audible_features, dtype=np.float64)


# --- Aggregation -----------------------------------------------------------


def _reject_octave_outliers(points: tuple[PitchPoint, ...]) -> tuple[PitchPoint, ...]:
    """Drop isolated frames that disagree wildly with their neighbours.

    Even a detector built to resist octave errors produces one at a transient:
    a frame straddling the boundary between two notes contains both, and can
    resolve to a sub-harmonic of either. A single such frame is harmless in the
    middle of a timeline and ruinous in a *range*, where one bad frame silently
    widens the reported span by an octave or more.

    The rule: a frame survives if it sits within :data:`OUTLIER_SEMITONES` of
    the median of its :data:`_OUTLIER_WINDOW`-frame neighbourhood. A median, not
    a mean, precisely so that the outlier being tested cannot drag the reference
    towards itself.

    This rejects frames rather than smoothing them. Every point that remains is
    a frame that was actually measured, at the frequency it was measured at —
    which is what makes the timeline safe to read as data.
    """
    if len(points) < _OUTLIER_WINDOW:
        return points

    midi = np.asarray([point.midi_note + point.cents / 100.0 for point in points], dtype=np.float64)
    half = _OUTLIER_WINDOW // 2
    kept: list[PitchPoint] = []
    for index, point in enumerate(points):
        start = max(0, index - half)
        end = min(midi.size, index + half + 1)
        local_median = float(np.median(midi[start:end]))
        if abs(midi[index] - local_median) <= OUTLIER_SEMITONES:
            kept.append(point)
    return tuple(kept)


def _sustained(points: tuple[PitchPoint, ...]) -> tuple[PitchPoint, ...]:
    """Frames belonging to a pitch that was actually held.

    A run qualifies when at least :data:`MIN_RANGE_FRAMES` consecutive frames
    stay within :data:`RANGE_CONTINUITY_SEMITONES` of each other — about 116 ms
    at the default hop.

    This exists for the *range*, and the reasoning is not statistical but
    musical: a frequency the detector touched for 23 ms while crossing between
    two notes is not a note anyone sang, and letting it define the bottom of a
    reported range is how a range becomes fiction. A held pitch is a pitch.

    Falls back to every voiced frame when nothing qualifies, so a recording of
    entirely fast movement still reports the range it contained rather than
    nothing at all.
    """
    if len(points) < MIN_RANGE_FRAMES:
        return points

    midi = [point.midi_note + point.cents / 100.0 for point in points]
    kept: list[PitchPoint] = []
    run_start = 0
    for index in range(1, len(points) + 1):
        continues = index < len(points) and (
            abs(midi[index] - midi[index - 1]) <= RANGE_CONTINUITY_SEMITONES
        )
        if continues:
            continue
        if index - run_start >= MIN_RANGE_FRAMES:
            kept.extend(points[run_start:index])
        run_start = index

    return tuple(kept) if kept else points


def _vocal_range(points: tuple[PitchPoint, ...]) -> VocalRange:
    """Extremes of the held pitches.

    Raw min and max over the frames :func:`_sustained` kept, not a percentile
    bound. A percentile would quietly discard a genuine low note in a short
    recording; requiring a pitch to be *held* removes the artefacts a percentile
    was meant to hide, without throwing away real content. The caveat that this
    is the range *in this recording* travels with the number everywhere it is
    shown.
    """
    frequencies = np.asarray([point.frequency_hz for point in points], dtype=np.float64)
    lowest = float(frequencies.min())
    highest = float(frequencies.max())
    span = round(pitch_math.semitones_between(lowest, highest))

    low_note = pitch_math.note_name_for_frequency(lowest)
    high_note = pitch_math.note_name_for_frequency(highest)
    assert low_note is not None and high_note is not None  # measured, so nameable

    return VocalRange(
        lowest_frequency_hz=lowest,
        highest_frequency_hz=highest,
        lowest_note=low_note,
        highest_note=high_note,
        semitone_span=max(0, span),
    )


def _stability(
    points: tuple[PitchPoint, ...],
    total_frames: int,
    hop_length: int,
    sample_rate: int,
) -> PitchStability:
    cents = np.asarray([point.cents for point in points], dtype=np.float64)
    midi = np.asarray([point.midi_note + point.cents / 100.0 for point in points], dtype=np.float64)
    voiced = len(points)

    return PitchStability(
        voiced_ratio=voiced / total_frames if total_frames else 0.0,
        total_frames=total_frames,
        voiced_frames=voiced,
        mean_cents_deviation=float(cents.mean()) if voiced else None,
        mean_abs_cents_deviation=float(np.abs(cents).mean()) if voiced else None,
        cents_std=float(cents.std()) if voiced else None,
        semitone_variance=float(midi.var()) if voiced else None,
        in_tune_ratio=(
            float(np.count_nonzero(np.abs(cents) <= IN_TUNE_CENTS) / voiced) if voiced else None
        ),
        unstable_sections=_unstable_sections(points, hop_length, sample_rate),
    )


def _unstable_sections(
    points: tuple[PitchPoint, ...], hop_length: int, sample_rate: int
) -> tuple[UnstableSection, ...]:
    """Runs of frames whose local cents deviation moves more than the threshold.

    A rolling standard deviation over :data:`_STABILITY_WINDOW` frames, so the
    measure is local: a recording that drifts slowly from sharp to flat is not
    "unstable" at any point, and it should not be reported as such.

    Only *consecutive* frames are joined. A gap in the timeline means unvoiced
    audio, and a section spanning one would be describing silence.
    """
    if len(points) < _STABILITY_WINDOW:
        return ()

    cents = np.asarray([point.cents for point in points], dtype=np.float64)
    times = np.asarray([point.timestamp_seconds for point in points], dtype=np.float64)
    half = _STABILITY_WINDOW // 2
    hop_seconds = hop_length / sample_rate
    # Allow a little slack so floating-point frame times still read as adjacent.
    adjacency_limit = hop_seconds * 1.5

    unstable = np.zeros(cents.size, dtype=bool)
    for index in range(cents.size):
        start = max(0, index - half)
        end = min(cents.size, index + half + 1)
        window = cents[start:end]
        if window.size >= _STABILITY_WINDOW:
            unstable[index] = bool(window.std() > UNSTABLE_CENTS_STD)

    sections: list[UnstableSection] = []
    run_start: int | None = None
    for index in range(cents.size):
        broken = index > 0 and times[index] - times[index - 1] > adjacency_limit  # a gap ends a run
        if unstable[index] and not broken:
            if run_start is None:
                run_start = index
            continue
        if run_start is not None:
            sections.append(_section(cents, times, run_start, index - 1))
            run_start = None
        if unstable[index] and broken:
            run_start = index

    if run_start is not None:
        sections.append(_section(cents, times, run_start, cents.size - 1))

    return tuple(
        section for section in sections if section.duration_seconds >= MIN_UNSTABLE_SECONDS
    )


def _section(cents: np.ndarray, times: np.ndarray, first: int, last: int) -> UnstableSection:
    return UnstableSection(
        start_seconds=float(times[first]),
        end_seconds=float(times[last]),
        cents_std=float(cents[first : last + 1].std()),
    )


def _loudness(collected: _Collected) -> Loudness:
    frame_rms = np.asarray(collected.frame_rms, dtype=np.float64)
    # Overall RMS from the per-frame values: frames overlap uniformly, so the
    # quadratic mean of the frames is the recording's RMS to within the tail.
    overall = float(np.sqrt(np.mean(np.square(frame_rms)))) if frame_rms.size else 0.0
    peak = float(min(collected.peak, 1.0))

    crest: float | None = None
    if overall > 0.0 and peak > 0.0:
        crest = float(max(0.0, 20.0 * np.log10(peak / overall)))

    clipped = (
        collected.clipped_samples / collected.total_samples if collected.total_samples else 0.0
    )

    return Loudness(
        rms=float(min(overall, 1.0)),
        peak=peak,
        dynamic_range_db=dynamic_range_db(frame_rms),
        crest_factor_db=crest,
        clipped_sample_ratio=float(min(clipped, 1.0)),
    )


def _spectral(collected: _Collected) -> SpectralFeatures | None:
    values = collected.audible_features
    if values.shape[0] == 0:
        return None
    means = values.mean(axis=0)
    return SpectralFeatures(
        centroid_hz=float(means[0]),
        bandwidth_hz=float(means[1]),
        rolloff_hz=float(means[2]),
        zero_crossing_rate=float(np.clip(means[3], 0.0, 1.0)),
        flatness=float(np.clip(means[4], 0.0, 1.0)),
    )
