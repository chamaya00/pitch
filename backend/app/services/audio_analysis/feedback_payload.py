"""Building what an AI provider is told about a recording's audio.

This module is the whole surface between measurement and interpretation. A
provider sees exactly what is assembled here and nothing else — no audio, no
path, no filename, no recording id — so anything left out cannot be commented
on, and anything wrong here is wrong in the prose.

**Selection, not a dump.** Every measurement the pipeline produces is not worth
sending. Two are deliberately excluded:

* ``semitone_variance`` — semitones squared has no plain-language reading, and
  ``cents_std`` already answers "how much did it move" in units a person can
  picture. Sending both invites a model to restate the same fact twice.
* ``unstable_sections`` — a timestamped list reads as a fault list, and vibrato,
  slides and blues intonation all land in it. Interpreting it safely needs a
  musical judgement the measurement does not support.

Both remain in the API for a client that wants them. See ``docs/ai.md``.

**Absent stays absent.** The serialiser omits keys whose value is ``None``
rather than writing ``null``. A key with a null value invites a model to fill it
in; a missing key reads as a subject that was never raised. This is what makes
the prompt's "say nothing about it" rule enforceable rather than aspirational.

Pure: domain objects in, a typed request and a JSON-ready dict out. No provider,
no HTTP, no I/O.
"""

import json
from typing import Any, Final

from app.services.audio_analysis.models import (
    IN_TUNE_CENTS,
    AudioFeedbackRequest,
    AudioMetrics,
    NoteSummary,
)

#: Notes sent to a provider, longest first. Enough to say where the time went;
#: a forty-row table would cost tokens to restate what the top few already show.
MAX_NOTES: Final = 8


def build_request(metrics: AudioMetrics, notes: tuple[NoteSummary, ...]) -> AudioFeedbackRequest:
    """Assemble the typed payload for one completed audio analysis."""
    return AudioFeedbackRequest(
        duration_seconds=metrics.duration_seconds,
        in_tune_threshold_cents=IN_TUNE_CENTS,
        pitch=metrics.pitch,
        stability=metrics.stability,
        loudness=metrics.loudness,
        spectral=metrics.spectral,
        notes=notes[:MAX_NOTES],
    )


def _present(values: dict[str, Any]) -> dict[str, Any]:
    """Drop the keys whose measurement was unavailable. See the module docstring."""
    return {key: value for key, value in values.items() if value is not None}


def to_payload(request: AudioFeedbackRequest) -> dict[str, Any]:
    """The JSON-ready object a provider is given.

    Field names are spelled out rather than abbreviated, and units are in the
    names, because this text is the only definition a model gets.
    """
    payload: dict[str, Any] = {
        "recording": {"duration_seconds": round(request.duration_seconds, 2)},
        "definitions": {
            "in_tune_ratio": (
                "share of reliably voiced analysis frames whose absolute deviation from "
                f"the nearest equal-tempered note was within {request.in_tune_threshold_cents:.0f} "
                "cents"
            ),
            "voiced_ratio": (
                "share of analysis frames that carried a reliable pitch; the rest were "
                "silence, noise or unvoiced sound"
            ),
            "detected_range": (
                "the lowest and highest pitch held in THIS recording; not a "
                "physiological vocal range"
            ),
            "percentage_of_voiced_time": (
                "share of the pitched time, not of the recording's duration"
            ),
        },
    }

    stability = request.stability
    payload["pitch"] = _present(
        {
            "voiced_ratio": _round(stability.voiced_ratio, 3),
            "in_tune_ratio": _round(stability.in_tune_ratio, 3),
            "mean_cents_deviation": _round(stability.mean_cents_deviation, 1),
            "mean_abs_cents_deviation": _round(stability.mean_abs_cents_deviation, 1),
            "cents_std": _round(stability.cents_std, 1),
            "voiced_frames": stability.voiced_frames,
            "total_frames": stability.total_frames,
        }
    )

    if request.pitch is not None:
        payload["detected_range"] = {
            "lowest_note": request.pitch.lowest_note,
            "highest_note": request.pitch.highest_note,
            "semitone_span": request.pitch.semitone_span,
        }

    loudness = request.loudness
    payload["loudness"] = _present(
        {
            "rms_amplitude_0_to_1": _round(loudness.rms, 4),
            "peak_amplitude_0_to_1": _round(loudness.peak, 4),
            "dynamic_range_db_estimate": _round(loudness.dynamic_range_db, 1),
            "clipped_sample_ratio": _round(loudness.clipped_sample_ratio, 4),
        }
    )

    if request.spectral is not None:
        payload["spectral"] = {
            "centroid_hz": round(request.spectral.centroid_hz),
            "bandwidth_hz": round(request.spectral.bandwidth_hz),
            "rolloff_hz": round(request.spectral.rolloff_hz),
            "zero_crossing_rate": round(request.spectral.zero_crossing_rate, 4),
            "flatness": round(request.spectral.flatness, 4),
        }

    if request.notes:
        payload["note_breakdown"] = [
            {
                "note_name": note.note_name,
                "duration_seconds": round(note.duration_seconds, 2),
                "percentage_of_voiced_time": round(note.percentage_of_voiced_time, 1),
                "average_cents": round(note.average_cents, 1),
                "mean_abs_cents": round(note.mean_abs_cents, 1),
                "in_tune_ratio": round(note.in_tune_ratio, 3),
            }
            for note in request.notes
        ]

    return payload


def _round(value: float | None, digits: int) -> float | None:
    """Round a measurement, or leave it absent.

    Rounding is presentation, and it happens here rather than in the domain:
    a provider does not need fourteen decimal places, and the extra digits read
    as precision the measurement does not have.
    """
    return None if value is None else round(value, digits)


def to_json(request: AudioFeedbackRequest) -> str:
    """The payload as the text a provider actually receives."""
    return json.dumps(to_payload(request), indent=2, sort_keys=True)
