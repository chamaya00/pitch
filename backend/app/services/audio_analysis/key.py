"""Melodic key estimation: which key the notes that were sung best fit.

Purely an **aggregation of the pitch timeline**, like ``notes.py``. It decodes
nothing, detects nothing and re-measures nothing — every frame it sees has
already passed the clarity gate, the octave-outlier rejection and the note
conversion in ``analyzer.py``. There is one pitch detector in this system and it
is upstream of here.

::

    pitch timeline ─▶ pitch-class profile ─▶ 24 correlations ─▶ key | None

The timeline arrives as :class:`~app.services.audio_analysis.models.PitchFields`
— the semitone of every frame, as an array — because the only thing this module
reads about a frame is which semitone it was nearest. Building the rest of a
frame to count it cost ~50 ms per request and is what Step 10.15 removed.

Four decisions carry this module, and three of them exist because the obvious
implementation reports a confident key for a hum.

**A pitch class is not a note.** ``A2``, ``A4`` and ``A5`` are three pitches and
one pitch class. A key is a statement about pitch classes, so the octave is
discarded here — and *only* here. ``notes.py`` keeps it, because "how long did
you hold A4" is a different question from "did this melody use A at all".

**Time is counted in hops.** The same rule ``notes.py`` states: frames overlap,
so charging each its full length would multiply every duration. Every point
carries exactly one hop, so the hop cancels out of the shares — it is written as
time anyway, so the arithmetic stays correct if the hop is ever varied.

**Confidence is a margin, never a correlation.** Correlating a *random*
pitch-class profile against the published key profiles scores **+0.492**, and a
*single held note* scores **+0.484**. Both look like evidence and neither is —
under Krumhansl–Kessler the hum reaches **+0.684 for C major**, which would read
as near-certainty. What separates real music from arithmetic is not the height
of the best correlation but how far it stands clear of the next one: a sung C
major melody leads by 0.262 where random weights lead by 0.013.

**The margin is over the next candidate of any kind.** Not over the next
candidate with a *different tonic*, which is the natural-looking alternative and
is wrong. Two hummed pitch classes lead the next *tonic* by 0.173 —
indistinguishable from real music — and lead the next *candidate* by 0.021,
because C major and C minor fit two notes almost equally well. Only the stricter
definition catches it.

Even that is not enough on its own, and the sweep says so loudly: a three-class
arpeggio leads by **0.309**, *higher than a real sung melody*. Confidence alone
would report a confident key for it. Two independent evidence gates therefore sit
in front of the correlation — enough distinct pitch classes, and enough voiced
time — and each is the only thing standing between some fixture and a confident
wrong answer. The thresholds were chosen by sweeping the fixtures in
``test_audio_key.py``, and the measurements are recorded beside each one below.

**What this is not.** Not a claim that the key was the right one — there is no
reference melody in this product. Not harmony: a melody sung over chords in
another key is read as the melody's key. Not tempo, not beats, not a
transcription, and not a score. See ``docs/limitations.md``.

Pure: a timeline in, a verdict out. No IO, no numpy, no decoder, no provider.
"""

import math
from dataclasses import dataclass
from typing import Final

from app.services.audio_analysis.models import (
    KeyAnalysis,
    KeyCandidate,
    KeyEstimate,
    KeyMode,
    KeyUnmeasuredReason,
    PitchClassShare,
    PitchFields,
)
from app.services.audio_analysis.notes import voiced_seconds
from app.services.audio_analysis.pitch import NOTE_NAMES

__all__ = [
    "DEFAULT_PROFILE_SET",
    "MIN_DISTINCT_PITCH_CLASSES",
    "MIN_KEY_CONFIDENCE",
    "MIN_PITCH_CLASS_SHARE",
    "MIN_VOICED_SECONDS",
    "PROFILE_SETS",
    "KeyProfileSet",
    "estimate_key",
    "analyse_key",
    "pitch_class_profile",
]

_SEMITONES: Final = 12


@dataclass(frozen=True, slots=True)
class KeyProfileSet:
    """One published pair of key profiles, named so a result can cite it.

    ``major`` and ``minor`` are the expected relative weight of each scale
    degree, starting at the tonic. Rotating them gives the other eleven keys.
    """

    name: str
    major: tuple[float, ...]
    minor: tuple[float, ...]

    def weights(self, mode: KeyMode) -> tuple[float, ...]:
        return self.major if mode is KeyMode.MAJOR else self.minor


#: Krumhansl–Kessler, from the probe-tone listening experiments (1982). Asked
#: listeners how well each pitch class fitted an established context.
KRUMHANSL_KESSLER: Final = KeyProfileSet(
    name="krumhansl-kessler",
    major=(6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88),
    minor=(6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17),
)

#: Temperley's revised profiles (2001), derived from the Kostka–Payne corpus
#: rather than from listening experiments — counts of what music actually
#: contains rather than reports of what listeners felt.
TEMPERLEY: Final = KeyProfileSet(
    name="temperley",
    major=(0.748, 0.060, 0.488, 0.082, 0.670, 0.460, 0.096, 0.715, 0.104, 0.366, 0.057, 0.400),
    minor=(0.712, 0.084, 0.474, 0.618, 0.049, 0.460, 0.105, 0.747, 0.404, 0.067, 0.133, 0.330),
)

PROFILE_SETS: Final[dict[str, KeyProfileSet]] = {
    KRUMHANSL_KESSLER.name: KRUMHANSL_KESSLER,
    TEMPERLEY.name: TEMPERLEY,
}

#: The profile set used unless a caller names another.
#:
#: Both were run against the whole fixture set before this was chosen, and
#: neither was adopted on reputation. Both identify **all 24** synthetic keys
#: correctly, so accuracy did not decide it. What decided it is the gap between
#: the inputs that must be answered and the inputs that must not:
#:
#: ==================  ==============  ==========
#: margin              Krumhansl–K.    Temperley
#: ==================  ==============  ==========
#: sung major melody           0.246       0.262
#: sung minor melody           0.276       0.205
#: chromatic wander            0.072       0.010
#: random weights              0.088       0.013
#: ==================  ==============  ==========
#:
#: Krumhansl–Kessler separates music from noise by about 3×; Temperley by about
#: 12×. A wider band is what lets the confidence gate below sit clear of the
#: noise floor without discarding real melodies, so Temperley wins on the one
#: measurement that matters here. See ``docs/audio-analysis.md``.
DEFAULT_PROFILE_SET: Final = TEMPERLEY

#: A pitch class carrying less of the voiced time than this is not counted
#: towards the evidence gate below.
#:
#: It is not excluded from the profile: every frame that was measured stays in
#: the shares, because the correlation should see the recording as it was. This
#: threshold decides only whether a class counts as *used*, and it exists
#: because a single frame at a note boundary would otherwise make a two-note hum
#: look like three-note music. Sized against the fixtures: a passing frame in a
#: two-second recording is worth ~1.2% of voiced time, while the least-used
#: class of a real seven-note melody is worth several times that.
MIN_PITCH_CLASS_SHARE: Final = 2.0

#: Distinct pitch classes required before any key is reported.
#:
#: **The gate that does the most work**, and the sweep is unambiguous about why:
#: a thin input does not produce an *uncertain* correlation, it produces a
#: confident and meaningless one. Measured margins, where anything below 5
#: classes must be refused:
#:
#: - one class (a hum): margin **0.022** — caught here, and only here
#: - two classes: **0.021** — caught here, and only here
#: - three classes: **0.309** — *higher than a real sung melody*. Nothing but
#:   this gate stands between an arpeggio and a confident key
#: - four classes: **0.171** — likewise
#: - five classes (pentatonic): **0.241** — real music, and it must pass
#:
#: Five is therefore the smallest value that admits a pentatonic melody, which
#: people genuinely sing, while refusing the four-class arpeggio above it.
MIN_DISTINCT_PITCH_CLASSES: Final = 5

#: Pitched seconds required before any key is reported.
#:
#: Derived rather than picked: the analyzer counts a pitch as *held* only after
#: ``MIN_RANGE_FRAMES`` frames, about 116 ms, so the shortest recording that
#: could honestly contain ``MIN_DISTINCT_PITCH_CLASSES`` held notes is about
#: 0.58 s. Rounded up to a full second.
#:
#: It earns its place independently of the other two: a five-class fixture only
#: 0.53 s long scores a margin of **0.281** and passes every other gate.
MIN_VOICED_SECONDS: Final = 1.0

#: The margin the best candidate must hold over the next one.
#:
#: Set from the **noise floor**, measured across the fixtures that reach this
#: gate at all — the ones with twelve pitch classes, which no earlier gate
#: stops:
#:
#: - a uniform profile: **0.000**
#: - a chromatic wander: **0.010**
#: - random weights: **0.013**
#: - the worst of any refusable fixture: **0.022**
#:
#: Against that, real melodies jittered ±50% on every degree (200 draws each,
#: the tonic and mode correct in 599 of 600) have 5th-percentile margins of
#: 0.097 (major), 0.081 (pentatonic) and 0.046 (minor).
#:
#: 0.05 sits in the empty band between the two: it clears the worst noise
#: fixture by more than 2× and discards roughly the weakest 5% of real melodies
#: — which are the genuinely ambiguous ones, and refusing them is the point.
#:
#: The tails do overlap: one jittered minor melody in 200 scored 0.004, below
#: the chromatic fixture. No threshold separates those, and a feature whose
#: honest answer is sometimes "not measured" is the specified behaviour rather
#: than a shortfall.
MIN_KEY_CONFIDENCE: Final = 0.05


def pitch_class_profile(
    frames: PitchFields, *, frame_seconds: float
) -> tuple[PitchClassShare, ...]:
    """Fold a pitch timeline into twelve pitch-class shares of voiced time.

    Always twelve entries, in pitch-class order, including the unused ones at
    zero: a key is decided as much by which classes are *absent* as by which are
    present, so dropping them would hide half the evidence.

    Returns an empty tuple when there is nothing pitched to fold. That is "no
    pitch classes were detected", which a caller must render as such — never as
    twelve measured zeroes.
    """
    if not frames or frame_seconds <= 0:
        return ()

    counts = [0] * _SEMITONES
    for midi in frames.midi_notes:
        counts[midi % _SEMITONES] += 1

    total = len(frames)
    return tuple(
        PitchClassShare(
            pitch_class=pitch_class,
            name=NOTE_NAMES[pitch_class],
            # Of voiced time, the same denominator ``notes.py`` uses, so the
            # twelve shares sum to 100 without needing to be normalised after.
            percentage_of_voiced_time=100.0 * count / total,
        )
        for pitch_class, count in enumerate(counts)
    )


def estimate_key(
    shares: tuple[PitchClassShare, ...],
    *,
    voiced_seconds: float,
    profiles: KeyProfileSet = DEFAULT_PROFILE_SET,
) -> KeyAnalysis:
    """Decide which key a pitch-class profile best fits, or refuse to.

    The gates are checked before the correlation and in a fixed order — voiced
    time, then distinct pitch classes, then the margin — so a recording that
    fails more than one is described by the most fundamental thing missing.
    """
    distinct = sum(
        1 for share in shares if share.percentage_of_voiced_time >= MIN_PITCH_CLASS_SHARE
    )

    def refuse(reason: KeyUnmeasuredReason) -> KeyAnalysis:
        return KeyAnalysis(
            unmeasured_reason=reason,
            pitch_classes=shares,
            distinct_pitch_classes=distinct,
            voiced_seconds=voiced_seconds,
            method=profiles.name,
        )

    if not shares or voiced_seconds < MIN_VOICED_SECONDS:
        return refuse(KeyUnmeasuredReason.TOO_LITTLE_VOICED_TIME)
    if distinct < MIN_DISTINCT_PITCH_CLASSES:
        return refuse(KeyUnmeasuredReason.TOO_FEW_PITCH_CLASSES)

    ranked = _ranked_candidates(shares, profiles)
    best, second = ranked[0], ranked[1]
    confidence = best.correlation - second.correlation
    if confidence < MIN_KEY_CONFIDENCE:
        return refuse(KeyUnmeasuredReason.AMBIGUOUS)

    return KeyAnalysis(
        key=KeyEstimate(
            tonic=NOTE_NAMES[best.tonic],
            mode=best.mode,
            confidence=confidence,
            # The runner-up travels with the answer so a close call is visible
            # rather than hidden behind one confident-looking label. Its own
            # margin is over the *third* candidate, by the same definition.
            alternative=KeyCandidate(
                tonic=NOTE_NAMES[second.tonic],
                mode=second.mode,
                confidence=max(0.0, second.correlation - ranked[2].correlation),
            ),
        ),
        pitch_classes=shares,
        distinct_pitch_classes=distinct,
        voiced_seconds=voiced_seconds,
        method=profiles.name,
    )


def analyse_key(
    frames: PitchFields,
    *,
    frame_seconds: float,
    profiles: KeyProfileSet = DEFAULT_PROFILE_SET,
) -> KeyAnalysis:
    """Fold a timeline and estimate its key in one call."""
    return estimate_key(
        pitch_class_profile(frames, frame_seconds=frame_seconds),
        voiced_seconds=voiced_seconds(frames, frame_seconds),
        profiles=profiles,
    )


@dataclass(frozen=True, slots=True)
class _Candidate:
    correlation: float
    tonic: int
    mode: KeyMode


def _ranked_candidates(
    shares: tuple[PitchClassShare, ...], profiles: KeyProfileSet
) -> list[_Candidate]:
    """All 24 keys, best correlation first.

    The ordering is total: ties break on tonic, then on major before minor.
    The sort is stable and the list is built in a fixed order, so the result is
    already deterministic without the explicit tie-break; the key states the
    intended order rather than leaving it as a property of `list.sort` that a
    later refactor could lose without any test noticing. Mutation confirms this:
    removing the tie-break changes no observable behaviour today.
    """
    observed = [share.percentage_of_voiced_time for share in shares]
    candidates = [
        _Candidate(
            correlation=_correlation(observed, _rotated(profiles.weights(mode), tonic)),
            tonic=tonic,
            mode=mode,
        )
        for mode in (KeyMode.MAJOR, KeyMode.MINOR)
        for tonic in range(_SEMITONES)
    ]
    candidates.sort(key=lambda c: (-c.correlation, c.tonic, c.mode is KeyMode.MINOR))
    return candidates


def _rotated(weights: tuple[float, ...], tonic: int) -> list[float]:
    """The profile expressed in absolute pitch classes for one tonic.

    ``weights`` is written relative to its own tonic, so the weight expected at
    pitch class *i* for a key on *tonic* is the profile's *(i − tonic)*-th degree.
    """
    return [weights[(index - tonic) % _SEMITONES] for index in range(_SEMITONES)]


def _correlation(observed: list[float], expected: list[float]) -> float:
    """Pearson correlation between two twelve-value profiles.

    Zero when either side has no variance at all: a perfectly flat profile is
    equally unlike every key, and "no relationship" is the honest answer where a
    division by zero is a crash.

    That branch is **defensive and unreachable through** :func:`estimate_key`, and
    the distinction is worth stating rather than implying. A flat profile only
    arrives here if it also cleared the pitch-class gate, which counts classes
    carrying a real share — and a genuinely flat twelve-class profile is refused
    by the confidence gate anyway, because near-equal correlations leave no
    margin. It is kept because this function is arithmetic that should not raise
    for an input its own signature permits, not because a caller depends on it.
    """
    mean_observed = sum(observed) / _SEMITONES
    mean_expected = sum(expected) / _SEMITONES

    covariance = sum(
        (o - mean_observed) * (e - mean_expected) for o, e in zip(observed, expected, strict=True)
    )
    variance_observed = sum((o - mean_observed) ** 2 for o in observed)
    variance_expected = sum((e - mean_expected) ** 2 for e in expected)

    denominator = math.sqrt(variance_observed * variance_expected)
    if denominator <= 0.0:
        return 0.0
    return covariance / denominator
