"""Melodic key estimation, over constructed timelines.

Two tables of fixtures, and the second one is the point.

The first proves the estimator finds the key when a key is there: all twelve
tonics in both modes, and the same melody transposed must move the tonic by
exactly the transposition and change nothing else.

The second proves it **refuses** when a key is not there — and that matters more,
because the failure mode of this feature is not "wrong answer", it is
"confident answer to a question the recording did not ask". Every adversarial
fixture below scores a plausible-looking correlation, and one of them
(a three-class arpeggio) scores a *higher margin than real music*. Each is
recorded with the number it produces, so a future change that removes a gate
fails a test that says exactly what it let through.

Nothing here decodes audio. These run on constructed ``PitchPoint`` tuples,
which is what makes them exhaustive rather than representative.

**These fixtures do not validate the estimator against human singing, and no
test in this file may be read as doing so.** Every timeline is synthetic. There
is no annotated corpus of real singing in this repository, and establishing that
this works on real voices needs one. See ``docs/phase-8-specification.md``.
"""

import random

import pytest

from app.services.audio_analysis.key import (
    DEFAULT_PROFILE_SET,
    KRUMHANSL_KESSLER,
    MIN_DISTINCT_PITCH_CLASSES,
    MIN_KEY_CONFIDENCE,
    MIN_PITCH_CLASS_SHARE,
    MIN_VOICED_SECONDS,
    PROFILE_SETS,
    TEMPERLEY,
    KeyProfileSet,
    _correlation,
    analyse_key,
    estimate_key,
    pitch_class_profile,
)
from app.services.audio_analysis.models import (
    KeyAnalysis,
    KeyMode,
    KeyUnmeasuredReason,
    PitchPoint,
)
from app.services.audio_analysis.pitch import (
    NOTE_NAMES,
    midi_to_frequency,
    midi_to_note_name,
)

#: The analyzer's default hop. Every duration here is in these units.
HOP = 0.0232

#: Middle C. Fixtures are written as pitch classes above this.
BASE_MIDI = 60


def timeline(weights: dict[int, int], *, base: int = BASE_MIDI) -> tuple[PitchPoint, ...]:
    """Build a timeline spending ``frames`` on each named pitch class.

    Real ``PitchPoint`` objects, validated by the same model the analyzer writes,
    so a fixture cannot express something the pipeline could not produce.
    """
    points: list[PitchPoint] = []
    timestamp = 0.0
    for pitch_class, frames in sorted(weights.items()):
        midi = base + pitch_class
        frequency = midi_to_frequency(midi)
        name = midi_to_note_name(midi)
        assert frequency is not None and name is not None
        for _ in range(frames):
            points.append(
                PitchPoint(
                    timestamp_seconds=timestamp,
                    frequency_hz=frequency,
                    midi_note=midi,
                    note_name=name,
                    cents=0.0,
                    confidence=0.9,
                )
            )
            timestamp += HOP
    return tuple(points)


def transposed(weights: dict[int, int], semitones: int) -> dict[int, int]:
    return {(pitch_class + semitones) % 12: frames for pitch_class, frames in weights.items()}


def analyse(weights: dict[int, int], **kwargs: KeyProfileSet) -> KeyAnalysis:
    return analyse_key(timeline(weights), frame_seconds=HOP, **kwargs)


# A melody's shape, not a scale's: the tonic and dominant carry it, and the
# leading note is passed through. This is what distinguishes C major from A
# minor, and a fixture without it would be testing the profile set's prior
# rather than the estimator.
MAJOR_MELODY = {0: 40, 2: 20, 4: 30, 5: 20, 7: 40, 9: 20, 11: 10}
MINOR_MELODY = {0: 40, 2: 20, 3: 30, 5: 20, 7: 40, 8: 20, 10: 10}
PENTATONIC = {0: 40, 2: 20, 4: 25, 7: 35, 9: 20}


# --- The pitch-class profile ------------------------------------------------


def test_the_profile_always_has_twelve_entries_in_order() -> None:
    """Absence is evidence. A key is decided partly by which classes are unused."""
    analysis = analyse(MAJOR_MELODY)
    assert len(analysis.pitch_classes) == 12
    assert [share.pitch_class for share in analysis.pitch_classes] == list(range(12))
    assert [share.name for share in analysis.pitch_classes] == list(NOTE_NAMES)
    assert [
        share.percentage_of_voiced_time
        for share in analysis.pitch_classes
        if share.pitch_class in (1, 3, 6, 8, 10)
    ] == [0.0] * 5


def test_octaves_fold_into_one_pitch_class() -> None:
    """A2, A4 and A5 are three pitches and one pitch class."""
    points = (
        timeline({0: 30}, base=45)  # A2
        + timeline({0: 30}, base=57)  # A3
        + timeline({0: 30}, base=69)  # A4
    )
    shares = pitch_class_profile(points, frame_seconds=HOP)
    assert shares[6].name == "F#"  # index is the pitch class, not the order sung
    a_share = next(share for share in shares if share.name == "A")
    assert a_share.percentage_of_voiced_time == pytest.approx(100.0)


def test_shares_are_of_voiced_time_and_sum_to_one_hundred() -> None:
    shares = pitch_class_profile(timeline(MAJOR_MELODY), frame_seconds=HOP)
    assert sum(share.percentage_of_voiced_time for share in shares) == pytest.approx(100.0)


def test_an_empty_timeline_has_no_profile_rather_than_twelve_zeroes() -> None:
    """ "No pitch classes were detected" is not a measurement of twelve zeroes."""
    assert pitch_class_profile((), frame_seconds=HOP) == ()


def test_a_nonsense_hop_produces_no_profile() -> None:
    assert pitch_class_profile(timeline(MAJOR_MELODY), frame_seconds=0.0) == ()


# --- The happy path ---------------------------------------------------------


@pytest.mark.parametrize("tonic", range(12))
def test_every_major_key_is_identified(tonic: int) -> None:
    analysis = analyse(transposed(MAJOR_MELODY, tonic))
    assert analysis.key is not None, analysis.unmeasured_reason
    assert analysis.key.tonic == NOTE_NAMES[tonic]
    assert analysis.key.mode is KeyMode.MAJOR


@pytest.mark.parametrize("tonic", range(12))
def test_every_minor_key_is_identified(tonic: int) -> None:
    analysis = analyse(transposed(MINOR_MELODY, tonic))
    assert analysis.key is not None, analysis.unmeasured_reason
    assert analysis.key.tonic == NOTE_NAMES[tonic]
    assert analysis.key.mode is KeyMode.MINOR


@pytest.mark.parametrize("semitones", range(1, 12))
def test_transposing_a_melody_moves_only_the_tonic(semitones: int) -> None:
    """The property a constant-output stub cannot fake.

    A stub that always answers "C major", and an implementation whose rotation
    runs the wrong way, both pass every single-key test above and fail here.
    """
    original = analyse(MAJOR_MELODY)
    shifted = analyse(transposed(MAJOR_MELODY, semitones))

    assert original.key is not None and shifted.key is not None
    assert shifted.key.tonic == NOTE_NAMES[semitones]
    assert shifted.key.mode is original.key.mode
    assert shifted.key.confidence == pytest.approx(original.key.confidence, abs=1e-9)


def test_a_pentatonic_melody_is_answered() -> None:
    """Five pitch classes is real music, and the gate is set to admit it."""
    analysis = analyse(PENTATONIC)
    assert analysis.key is not None
    assert analysis.distinct_pitch_classes == 5


def test_the_runner_up_travels_with_the_answer() -> None:
    """A close call must be visible, not hidden behind one confident label."""
    analysis = analyse(MAJOR_MELODY)
    assert analysis.key is not None
    assert analysis.key.alternative is not None
    assert (analysis.key.alternative.tonic, analysis.key.alternative.mode) != (
        analysis.key.tonic,
        analysis.key.mode,
    )


def test_the_evidence_travels_with_the_answer() -> None:
    analysis = analyse(MAJOR_MELODY)
    assert analysis.distinct_pitch_classes == 7
    assert analysis.voiced_seconds == pytest.approx(180 * HOP)
    assert analysis.method == DEFAULT_PROFILE_SET.name


def test_the_same_timeline_always_produces_the_same_answer() -> None:
    """Ties break on tonic then on mode, so nothing depends on iteration order."""
    points = timeline(MAJOR_MELODY)
    first = analyse_key(points, frame_seconds=HOP)
    second = analyse_key(points, frame_seconds=HOP)
    assert first.model_dump() == second.model_dump()


# --- Refusals: the fixtures that matter -------------------------------------
#
# Each row carries the margin it actually produces, so a reader can see that
# these are not weak inputs producing weak numbers — several of them produce
# numbers indistinguishable from music, and one beats it.


def test_a_single_hummed_note_is_not_a_key() -> None:
    """Margin 0.022 under Temperley, and +0.684 raw under Krumhansl-Kessler.

    One held note fits C major and C minor equally well. Nothing but the
    pitch-class gate stops this becoming a confident label.
    """
    analysis = analyse({0: 140})
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.TOO_FEW_PITCH_CLASSES
    assert analysis.distinct_pitch_classes == 1


def test_two_pitch_classes_are_not_a_key() -> None:
    """Margin 0.021 over the next candidate — but 0.173 over the next *tonic*.

    This is the fixture that decides how confidence is defined. Measured against
    a different tonic it looks exactly like real music.
    """
    analysis = analyse({0: 80, 7: 60})
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.TOO_FEW_PITCH_CLASSES


def test_a_three_note_arpeggio_is_not_a_key() -> None:
    """Margin **0.309** — higher than a real sung melody scores.

    The confidence gate cannot catch this one. Only the pitch-class gate can,
    which is why both exist.
    """
    analysis = analyse({0: 60, 4: 45, 7: 50})
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.TOO_FEW_PITCH_CLASSES


def test_four_pitch_classes_are_not_a_key() -> None:
    """Margin 0.171, again above the confidence gate and caught by the other."""
    analysis = analyse({0: 50, 4: 40, 7: 45, 10: 30})
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.TOO_FEW_PITCH_CLASSES


def test_a_bare_unweighted_scale_is_answered_but_only_just() -> None:
    """The one case where the sweep changed the specification.

    Seven degrees weighted equally: the same seven pitch classes as the relative
    minor, with neither tonic emphasised. Step 10.7 predicted this must be
    refused, on Krumhansl-Kessler numbers where it leads by 0.044 — level with
    noise. Under the profile set that actually shipped it leads by 0.146: above
    the gate, and well below the 0.19-0.23 a weighted melody reaches.

    So it is answered, and answered *weakly*, which is the honest outcome — the
    confidence and the runner-up both say how thin it is, and the twelve equal
    shares are returned so a reader can see it for themselves. Reporting a bare
    scale as its major key is also what a listener would do.
    """
    analysis = analyse({pitch_class: 20 for pitch_class in (0, 2, 4, 5, 7, 9, 11)})

    assert analysis.key is not None
    assert (analysis.key.tonic, analysis.key.mode) == ("C", KeyMode.MAJOR)
    assert analysis.key.alternative is not None
    assert analysis.key.alternative.tonic == "A"
    assert analysis.key.alternative.mode is KeyMode.MINOR

    weighted = analyse(MAJOR_MELODY)
    assert weighted.key is not None
    assert analysis.key.confidence < weighted.key.confidence


def test_a_chromatic_wander_is_not_a_key() -> None:
    """Twelve classes, so only the confidence gate stands here. Margin 0.010."""
    analysis = analyse({pitch_class: 20 + pitch_class for pitch_class in range(12)})
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.AMBIGUOUS
    assert analysis.distinct_pitch_classes == 12


def test_a_uniform_profile_is_not_a_key() -> None:
    """Equally unlike every key. The correlation is 0, not a division by zero."""
    analysis = analyse({pitch_class: 20 for pitch_class in range(12)})
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.AMBIGUOUS


def test_random_pitch_class_weights_are_not_a_key() -> None:
    """Margin 0.013, from a raw correlation of +0.492 that looks like evidence."""
    generator = random.Random(1)
    weights = {pitch_class: generator.randint(5, 45) for pitch_class in range(12)}
    analysis = analyse(weights)
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.AMBIGUOUS


def test_a_recording_too_short_to_hold_five_notes_is_not_a_key() -> None:
    """Five classes and a margin of 0.281 — refused on time alone.

    0.53 s cannot contain five *held* notes at the analyzer's own definition of
    held, so whatever this is, it is not five notes.
    """
    analysis = analyse({0: 5, 2: 4, 4: 5, 5: 4, 7: 5})
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.TOO_LITTLE_VOICED_TIME
    assert analysis.voiced_seconds < MIN_VOICED_SECONDS


def test_an_empty_timeline_is_refused_rather_than_answered() -> None:
    analysis = analyse_key((), frame_seconds=HOP)
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.TOO_LITTLE_VOICED_TIME
    assert analysis.pitch_classes == ()


def test_a_refusal_still_carries_its_evidence() -> None:
    """The difference between a refusal and a shrug."""
    analysis = analyse({0: 140})
    assert len(analysis.pitch_classes) == 12
    assert analysis.voiced_seconds == pytest.approx(140 * HOP)
    assert analysis.method == DEFAULT_PROFILE_SET.name


# --- Symmetry: the gates must not be tuned until nothing passes -------------


@pytest.mark.parametrize(
    "weights", [MAJOR_MELODY, MINOR_MELODY, PENTATONIC], ids=["major", "minor", "pentatonic"]
)
def test_every_real_melody_is_answered(weights: dict[int, int]) -> None:
    """The false-negative half. A gate tuned until nothing passes has broken it."""
    analysis = analyse(weights)
    assert analysis.key is not None, analysis.unmeasured_reason


def test_jittered_melodies_keep_their_tonic() -> None:
    """Real singing is not evenly weighted, so the fixtures must not be either.

    Every degree scaled by a random 0.5–1.5. The tonic and mode must survive it;
    the margin need not, and a draw that falls below the gate is refused rather
    than guessed — which is the specified behaviour.
    """
    generator = random.Random(11)
    answered = 0
    for _ in range(100):
        weights = {
            pitch_class: max(1, round(frames * generator.uniform(0.5, 1.5)))
            for pitch_class, frames in MAJOR_MELODY.items()
        }
        analysis = analyse(weights)
        if analysis.key is None:
            assert analysis.unmeasured_reason is KeyUnmeasuredReason.AMBIGUOUS
            continue
        answered += 1
        assert (analysis.key.tonic, analysis.key.mode) == ("C", KeyMode.MAJOR)

    # Measured at 97/100 when the thresholds were set. Asserted loosely because
    # the point is that the gate does not swallow ordinary singing, not that it
    # swallows an exact share of it.
    assert answered > 80


# --- The gates, one at a time -----------------------------------------------


def test_the_pitch_class_gate_ignores_a_negligible_share() -> None:
    """One stray frame at a foreign pitch class is not a sixth note.

    It stays in the profile — every measured frame does — but it does not make
    a five-note melody look like a six-note one.
    """
    with_stray = dict(PENTATONIC)
    with_stray[1] = 1
    analysis = analyse(with_stray)

    assert analysis.distinct_pitch_classes == 5
    stray = analysis.pitch_classes[1]
    assert 0 < stray.percentage_of_voiced_time < MIN_PITCH_CLASS_SHARE


def test_the_gates_are_reported_in_order_of_how_fundamental_they_are() -> None:
    """A fixture failing two gates is described by the more basic one."""
    analysis = analyse({0: 10})  # one class, and far too short
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.TOO_LITTLE_VOICED_TIME


def test_confidence_is_a_margin_and_never_a_raw_correlation() -> None:
    """A raw correlation of +0.48 is reachable by a hum; a margin of 0.48 is not."""
    analysis = analyse(MAJOR_MELODY)
    assert analysis.key is not None
    assert MIN_KEY_CONFIDENCE <= analysis.key.confidence < 1.0


def test_the_thresholds_are_the_ones_the_measurements_chose() -> None:
    """Pins the values so a change has to be deliberate and re-measured."""
    assert MIN_DISTINCT_PITCH_CLASSES == 5
    assert MIN_VOICED_SECONDS == 1.0
    assert MIN_KEY_CONFIDENCE == 0.05
    assert MIN_PITCH_CLASS_SHARE == 2.0


# --- The profile sets -------------------------------------------------------


@pytest.mark.parametrize("profiles", list(PROFILE_SETS.values()), ids=list(PROFILE_SETS))
@pytest.mark.parametrize("tonic", range(12))
def test_both_published_profile_sets_identify_every_major_key(
    profiles: KeyProfileSet, tonic: int
) -> None:
    """Accuracy did not decide which set to use — both are exact."""
    analysis = analyse(transposed(MAJOR_MELODY, tonic), profiles=profiles)
    assert analysis.key is not None
    assert analysis.key.tonic == NOTE_NAMES[tonic]
    assert analysis.method == profiles.name


def test_temperley_separates_music_from_noise_more_widely_than_krumhansl() -> None:
    """The measurement that chose the default, asserted rather than asserted about.

    Both sets find the key. The one that ships is the one whose margin between a
    sung melody and random weights is wide enough to put a gate inside.
    """
    noise = {pitch_class: 20 + pitch_class for pitch_class in range(12)}

    def margin(weights: dict[int, int], profiles: KeyProfileSet) -> float:
        analysis = estimate_key(
            pitch_class_profile(timeline(weights), frame_seconds=HOP),
            voiced_seconds=999.0,
            profiles=profiles,
        )
        return analysis.key.confidence if analysis.key is not None else 0.0

    temperley_gap = margin(MAJOR_MELODY, TEMPERLEY) / max(margin(noise, TEMPERLEY), 1e-9)
    krumhansl_gap = margin(MAJOR_MELODY, KRUMHANSL_KESSLER) / max(
        margin(noise, KRUMHANSL_KESSLER), 1e-9
    )
    assert temperley_gap > krumhansl_gap
    assert DEFAULT_PROFILE_SET is TEMPERLEY


def test_a_flat_profile_correlates_to_nothing_rather_than_crashing() -> None:
    """The guard :func:`estimate_key` cannot reach, tested where it lives.

    Mutation showed the gates in front make this branch unreachable through the
    public function, so asserting it via ``estimate_key`` would be asserting
    nothing. Arithmetic that a caller's own type signature permits must not
    raise, and this is where that is true or not.
    """
    flat = [5.0] * 12
    assert _correlation(flat, list(TEMPERLEY.major)) == 0.0
    assert _correlation(list(TEMPERLEY.major), flat) == 0.0
    assert _correlation([0.0] * 12, [0.0] * 12) == 0.0


def test_a_profile_correlates_perfectly_with_itself() -> None:
    assert _correlation(list(TEMPERLEY.major), list(TEMPERLEY.major)) == pytest.approx(1.0)


def test_every_profile_set_has_twelve_weights_per_mode() -> None:
    for profiles in PROFILE_SETS.values():
        assert len(profiles.major) == 12
        assert len(profiles.minor) == 12
        assert profiles.weights(KeyMode.MAJOR) == profiles.major
        assert profiles.weights(KeyMode.MINOR) == profiles.minor


# --- The model's own guarantees ---------------------------------------------


def test_a_verdict_cannot_be_both_a_key_and_a_refusal() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        KeyAnalysis()


def test_a_key_and_a_reason_cannot_both_be_recorded() -> None:
    analysis = analyse(MAJOR_MELODY)
    assert analysis.key is not None
    with pytest.raises(ValueError, match="exactly one"):
        KeyAnalysis(key=analysis.key, unmeasured_reason=KeyUnmeasuredReason.AMBIGUOUS)


def test_a_partial_profile_is_refused_by_the_model() -> None:
    analysis = analyse(MAJOR_MELODY)
    with pytest.raises(ValueError, match="twelve entries"):
        KeyAnalysis(
            unmeasured_reason=KeyUnmeasuredReason.AMBIGUOUS,
            pitch_classes=analysis.pitch_classes[:6],
        )
