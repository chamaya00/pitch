"""Note breakdown: how the pitched time was spent, note by note.

Purely an **aggregation of the pitch timeline**. It decodes nothing, detects
nothing and re-measures nothing — every frame it sees has already passed the
clarity gate, the octave-outlier rejection and the note conversion in
``analyzer.py``. There is one pitch detector in this system and it is upstream
of here.

Three definitions carry the whole module, and each exists because the obvious
alternative would mislead:

**Duration is counted in hops, not frames.** Analysis frames overlap — 2048
samples advancing 512 at a time — so charging each frame its *frame length*
would count the overlap repeatedly and produce durations several times longer
than the recording. Each frame is charged one **hop**, the amount of new audio
it brought, which makes the durations sum to the voiced time exactly.

**Percentages are of voiced time, never of the recording.** A recording that is
half silence would otherwise report every note at half its real share, and the
shares would sum to 50%. Voiced time is ``frame count × hop``, and the shares
sum to 100%.

**A note is a semitone, not a frequency.** Frames are grouped by nearest MIDI
note, so C4 at −12, −5, +4 and +8 cents is one entry for C4 with a cents
deviation of its own — not four entries. The deviations are what
``average_cents`` and ``mean_abs_cents`` report.

**No minimum duration is applied.** Transient artefacts were already removed
upstream by rules that have tests behind them; adding a second, arbitrary
threshold here would silently delete short notes that are real. A passing note
held for two frames appears with ``frame_count: 2``, which says plainly how thin
the evidence is. See ``docs/audio-analysis.md``.
"""

from collections import defaultdict
from typing import Final

# The same threshold the recording-level ``in_tune_ratio`` uses, imported from
# the domain rather than restated so the two can never drift apart — and taken
# from ``models`` rather than from the analyzer, so aggregating a timeline does
# not drag numpy and a decoder in behind it.
from app.services.audio_analysis.models import IN_TUNE_CENTS, NoteSummary, PitchPoint

__all__ = [
    "IN_TUNE_CENTS",
    "frame_duration_seconds",
    "summarise_notes",
    "voiced_seconds",
]

#: Percentages are rounded for display; this is the tolerance within which their
#: sum is expected to reach 100. Used by the tests, not by the arithmetic.
PERCENTAGE_TOLERANCE: Final = 0.01


def frame_duration_seconds(hop_length_samples: int, sample_rate_hz: int) -> float:
    """Seconds of new audio each successive frame contributes.

    The hop, not the frame length — see the module docstring.
    """
    if hop_length_samples <= 0 or sample_rate_hz <= 0:
        raise ValueError("hop length and sample rate must both be positive")
    return hop_length_samples / sample_rate_hz


def voiced_seconds(points: tuple[PitchPoint, ...], frame_seconds: float) -> float:
    """Total reliably pitched time in a timeline.

    Every point in the timeline is a voiced frame — unvoiced audio is omitted
    upstream rather than stored with null fields — so this is a frame count
    times the hop.
    """
    return len(points) * frame_seconds


def summarise_notes(
    points: tuple[PitchPoint, ...], *, frame_seconds: float
) -> tuple[NoteSummary, ...]:
    """Aggregate a pitch timeline into one entry per musical note.

    Returns an empty tuple when there is nothing pitched to break down. That is
    "no notes were detected", which a caller must render as such — never as a
    successful measurement of zero notes.

    Ordering is total and deterministic: longest first, and where two notes were
    held for exactly the same time, the lower one first. Without a tie-break the
    order would depend on dictionary iteration and the same recording could
    render differently between runs.
    """
    if not points or frame_seconds <= 0:
        return ()

    grouped: dict[int, list[PitchPoint]] = defaultdict(list)
    for point in points:
        grouped[point.midi_note].append(point)

    total_frames = len(points)
    summaries = [
        _summarise(midi, frames, frame_seconds, total_frames) for midi, frames in grouped.items()
    ]
    return tuple(sorted(summaries, key=lambda note: (-note.duration_seconds, note.midi_note)))


def _summarise(
    midi: int, frames: list[PitchPoint], frame_seconds: float, total_frames: int
) -> NoteSummary:
    cents = [frame.cents for frame in frames]
    in_tune = sum(1 for value in cents if abs(value) <= IN_TUNE_CENTS)

    return NoteSummary(
        midi_note=midi,
        note_name=frames[0].note_name,
        duration_seconds=len(frames) * frame_seconds,
        # Of voiced time, which is the same denominator for every note, so the
        # shares sum to 100 without needing to be normalised afterwards.
        percentage_of_voiced_time=100.0 * len(frames) / total_frames,
        frame_count=len(frames),
        average_cents=sum(cents) / len(cents),
        mean_abs_cents=sum(abs(value) for value in cents) / len(cents),
        in_tune_ratio=in_tune / len(frames),
    )
