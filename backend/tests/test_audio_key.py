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

The performance ceiling is at the end of the file. What a test cannot state is
whether it would notice being broken, so that is measured separately, below.

Mutation audit
--------------

Run by a script kept **outside** the repository — it edits source files in place,
and a crash mid-run must not leave a mutated tree behind that somebody commits.
21 mutations across the whole feature, each required to fail a *named* test:

===========================  ======  ==========================================
invariant                    result  notes
===========================  ======  ==========================================
candidate scoring (5)        caught  profiles reversed, runner-up returned,
                                     rotation inverted, correlation used as
                                     confidence, margin redefined
candidate scoring (1)        equiv.  removing the tie-break changes no
                                     observable behaviour — the list is already
                                     built in a fixed order and the sort is
                                     stable. Documented as such in ``key.py``;
                                     confirmed again here against the whole
                                     module rather than one named test
evidence gates (4)           caught  each of the three gates dropped, and the
                                     negligible-share rule removed
null semantics (2)           caught  wrong reason reported, evidence dropped
service integration (4)      caught  frame length charged instead of the hop,
                                     status guard dropped, a second load of the
                                     analysis document, a write introduced
ownership (2)                caught  owner dropped from the scoped read, and
                                     the refusal made to distinguish "not
                                     yours" from "does not exist"
API semantics (3)            caught  wrong 404 code, ``unmeasured_reason``
                                     dropped, tonic hard-coded in the schema
===========================  ======  ==========================================

**One mutation survived the first run** and is the reason two tests below exist:
redefining the confidence margin as the lead over the next candidate with a
*different tonic* passed everything. Every adversarial fixture in this file was
refused by the pitch-class gate before the correlation was ever reached, so none
of them could see how the margin was defined — the property ``key.py`` argues
for at length was, in fact, untested. ``AMBIGUOUS_MODE`` closes it, and the
mutation was re-run against it: caught.

There is no frontend counterpart to audit. The musical-key UI is Slice 4 and is
not in this repository yet.
"""

import json
import math
import random
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.audio_analysis.analyzer import HOP_SECONDS
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
    _ranked_candidates,
    analyse_key,
    estimate_key,
    pitch_class_profile,
)
from app.services.audio_analysis.models import (
    KeyAnalysis,
    KeyMode,
    KeyUnmeasuredReason,
    PitchFields,
    PitchPoint,
)
from app.services.audio_analysis.notes import summarise_notes
from app.services.audio_analysis.pitch import (
    NOTE_NAMES,
    midi_to_frequency,
    midi_to_note_name,
)

#: The analyzer's default hop. Every duration here is in these units.
HOP = 0.0232

#: Middle C. Fixtures are written as pitch classes above this.
BASE_MIDI = 60


def timeline(weights: dict[int, int], *, base: int = BASE_MIDI) -> PitchFields:
    """Build a timeline spending ``frames`` on each named pitch class.

    Built as real ``PitchPoint`` objects, validated by the same model the
    analyzer writes, so a fixture cannot express something the pipeline could
    not produce — then reduced to the two fields the estimator reads by the same
    constructor production uses. The store projects those two fields straight
    out of the stored document; the contract suite asserts both routes reach the
    same arrays.
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
    return PitchFields.of_points(tuple(points))


def joined(*parts: PitchFields) -> PitchFields:
    """Several fixtures as one timeline. Arrays concatenate field by field."""
    return PitchFields(
        midi_notes=tuple(note for part in parts for note in part.midi_notes),
        cents=tuple(value for part in parts for value in part.cents),
    )


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
    points = joined(
        timeline({0: 30}, base=45),  # A2
        timeline({0: 30}, base=57),  # A3
        timeline({0: 30}, base=69),  # A4
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
    assert pitch_class_profile(PitchFields(), frame_seconds=HOP) == ()


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


#: A melody that sings both thirds and settles neither mode.
#:
#: Eight pitch classes over 3.8 s, so both evidence gates pass and the
#: **confidence gate is the only thing left** — which is what makes this the one
#: fixture in the file that can see how the margin is defined:
#:
#: - best:   C minor, r = 0.8197
#: - second: C major, r = 0.7992
#: - margin over the next candidate of any kind: **0.0205** — refused
#: - margin over the next candidate with a *different tonic*: **0.4094** —
#:   comfortably confident, and wrong
#:
#: Every other adversarial fixture is stopped by the pitch-class gate before the
#: correlation is reached, so all of them survive the weaker definition. This one
#: was found by search over profiles that share their top two candidates' tonic,
#: and it is the only reason the stricter rule is load-bearing rather than
#: merely stated. See ``key.py`` on why the margin is over *any* candidate.
AMBIGUOUS_MODE = {0: 32, 2: 20, 3: 16, 4: 18, 5: 21, 7: 27, 8: 11, 11: 18}


def test_a_melody_that_settles_neither_mode_is_not_a_key() -> None:
    """Both thirds sung, near-equally. C major and C minor fit it equally well.

    The honest answer is that the recording did not choose, and the estimator
    must say so rather than pick the one that led by 0.02.
    """
    analysis = analyse(AMBIGUOUS_MODE)
    assert analysis.key is None
    assert analysis.unmeasured_reason is KeyUnmeasuredReason.AMBIGUOUS
    # Refused on confidence alone: neither evidence gate was close to firing.
    assert analysis.distinct_pitch_classes == 8
    assert analysis.voiced_seconds > MIN_VOICED_SECONDS


def test_the_margin_is_over_the_next_candidate_and_not_the_next_tonic() -> None:
    """The definition itself, asserted where the two differ.

    ``key.py`` chooses the margin over the next candidate *of any kind* over the
    natural-looking alternative — the next candidate with a different tonic —
    and this is the fixture that separates them. Under the weaker definition
    this profile leads by 0.409 and would be reported as a confident C minor.
    Under the stricter one it leads by 0.021 and is refused.

    Asserted through :func:`_ranked_candidates` rather than inferred from the
    verdict, so the numbers a reader is asked to believe are the ones measured.
    """
    shares = pitch_class_profile(timeline(AMBIGUOUS_MODE), frame_seconds=HOP)
    ranked = _ranked_candidates(shares, DEFAULT_PROFILE_SET)
    best, second = ranked[0], ranked[1]

    # The two definitions disagree only when the runner-up shares the tonic.
    assert best.tonic == second.tonic
    assert best.mode is not second.mode

    over_any_candidate = best.correlation - second.correlation
    next_other_tonic = next(c for c in ranked[1:] if c.tonic != best.tonic)
    over_other_tonic = best.correlation - next_other_tonic.correlation

    assert over_any_candidate == pytest.approx(0.0205, abs=0.001)
    assert over_other_tonic == pytest.approx(0.4094, abs=0.001)
    assert over_any_candidate < MIN_KEY_CONFIDENCE <= over_other_tonic


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


# --- Parity with the browser implementation ---------------------------------
#
# The live readout in Live Vocal Practice estimates the key of a session while
# somebody sings, and it cannot call this module: microphone audio never leaves
# the page, so the mathematics exists a second time in ``frontend/lib/live-key.ts``.
# That is the rule ``pitch.py`` and ``lib/pitch.ts`` already follow — the
# implementations are separate on purpose and the mathematics is not.
#
# ``fixtures/key-parity.json`` is what holds them together: one table of
# profiles and the verdicts they must produce, read here and by
# ``frontend/tests/live-key.test.ts``. Neither side may be changed to agree with
# the other without this table changing too, and the table is generated from
# *this* implementation, which is the authoritative one.


PARITY_TABLE = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "key-parity.json").read_text()
)


def test_the_parity_table_states_the_thresholds_this_module_uses() -> None:
    """A table written against different constants would prove nothing."""
    thresholds = PARITY_TABLE["thresholds"]
    assert PARITY_TABLE["profile_set"] == DEFAULT_PROFILE_SET.name
    assert thresholds["min_distinct_pitch_classes"] == MIN_DISTINCT_PITCH_CLASSES
    assert thresholds["min_voiced_seconds"] == MIN_VOICED_SECONDS
    assert thresholds["min_key_confidence"] == MIN_KEY_CONFIDENCE
    assert thresholds["min_pitch_class_share"] == MIN_PITCH_CLASS_SHARE
    assert len(PARITY_TABLE["cases"]) >= 15


@pytest.mark.parametrize(
    "case", PARITY_TABLE["cases"], ids=[case["name"] for case in PARITY_TABLE["cases"]]
)
def test_the_shared_table_is_what_this_estimator_produces(case: dict[str, object]) -> None:
    """Every row, against the implementation the browser copy mirrors.

    The margins are asserted exactly rather than approximately. Both sides
    perform the same operations in the same order over the same doubles, so a
    difference beyond floating-point noise is a difference in the measurement.
    """
    frames = {
        pitch_class: count
        for pitch_class, count in enumerate(case["frames"])  # type: ignore[call-overload]
        if count
    }
    analysis = analyse_key(timeline(frames), frame_seconds=float(PARITY_TABLE["frame_seconds"]))
    expected = case["expect"]  # type: ignore[index]

    assert (analysis.key.tonic if analysis.key else None) == expected["tonic"]
    assert (analysis.key.mode.value if analysis.key else None) == expected["mode"]
    assert (analysis.unmeasured_reason.value if analysis.unmeasured_reason else None) == expected[
        "unmeasured_reason"
    ]
    assert analysis.distinct_pitch_classes == expected["distinct_pitch_classes"]

    if expected["confidence"] is None:
        assert analysis.key is None
        return
    assert analysis.key is not None
    assert analysis.key.confidence == pytest.approx(expected["confidence"], abs=1e-12)
    alternative = analysis.key.alternative
    assert (alternative.tonic if alternative else None) == expected["alternative_tonic"]
    assert (alternative.mode.value if alternative else None) == expected["alternative_mode"]


def test_the_fixture_that_pins_the_margin_is_in_the_shared_table() -> None:
    """The one row a second implementation would get wrong.

    Slice 5's mutation run found that defining confidence as the lead over the
    next candidate with a *different tonic* survived every other fixture — all of
    them are stopped by the pitch-class gate before the correlation is reached.
    ``AMBIGUOUS_MODE`` is the only profile that can see the difference, so a
    parity table without it would let the browser copy adopt the wrong
    definition and still pass.
    """
    row = next(
        case for case in PARITY_TABLE["cases"] if case["name"] == "both thirds, neither mode"
    )
    frames = {pitch_class: count for pitch_class, count in enumerate(row["frames"]) if count}
    assert frames == AMBIGUOUS_MODE
    assert row["expect"]["unmeasured_reason"] == KeyUnmeasuredReason.AMBIGUOUS.value


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


# --- The performance ceiling ------------------------------------------------
#
# Key estimation is a read-only fold over a timeline that is already in memory.
# What has to stay true is that it remains one, and these assertions are written
# so that an algorithmic regression fails them and a slow machine does not.
#
# Measured on the development machine at the ceiling below, best of fifteen runs.
# The second column is Step 10.15, which handed both folds the two fields they
# read as arrays instead of a tuple of ``PitchPoint``: the arithmetic is
# unchanged, and indexing an array is not attribute access on a model.
#
#                                          over frames   over fields
#   analyse_key (fold + 24 correlations)      1.35 ms       0.90 ms
#   estimate_key alone, profile pre-folded    0.18 ms       0.18 ms
#   summarise_notes, the same timeline        2.95 ms       2.04 ms
#
# The fold dominates, the 24 correlations do not: they are twelve-element
# arithmetic whatever the recording's length.


def _ceiling_points() -> int:
    """Points in the longest recording this product accepts.

    Derived from configuration and the analyzer's hop rather than written down,
    so raising ``max_audio_duration_seconds`` moves the benchmark with it
    instead of leaving it measuring a length that is no longer the maximum.
    """
    return math.floor(Settings().max_audio_duration_seconds / HOP_SECONDS)


def long_timeline(points: int) -> PitchFields:
    """A melody-shaped timeline of ``points`` frames, spanning three octaves.

    Not one held note repeated: the fold's work is a modulo and a list index per
    point either way, but a single pitch class would be refused by the gates and
    so would never reach the correlation this is timing. The octave spread is
    what the estimator is for — three octaves of the same seven pitch classes
    fold to the same seven shares.
    """
    order = sorted(MAJOR_MELODY)
    built: list[PitchPoint] = []
    timestamp = 0.0
    index = 0
    while len(built) < points:
        pitch_class = order[index % len(order)]
        octave_offset = 12 * ((index // len(order)) % 3) - 12
        midi = BASE_MIDI + pitch_class + octave_offset
        frequency = midi_to_frequency(midi)
        name = midi_to_note_name(midi)
        assert frequency is not None and name is not None
        run = max(1, MAJOR_MELODY[pitch_class] * points // (sum(MAJOR_MELODY.values()) * 8))
        for _ in range(min(run, points - len(built))):
            built.append(
                PitchPoint(
                    timestamp_seconds=timestamp,
                    frequency_hz=frequency,
                    midi_note=midi,
                    note_name=name,
                    cents=0.0,
                    confidence=0.9,
                )
            )
            timestamp += HOP_SECONDS
        index += 1
    return PitchFields.of_points(tuple(built))


def fastest(call: Callable[[], object], runs: int = 9) -> float:
    """Seconds taken by the quickest of ``runs`` calls, after one warm-up.

    The quickest rather than the mean on purpose. A shared CI machine adds time
    to a run and never removes it, so the minimum is the closest thing to the
    work itself; a mean measures the neighbours as much as the code.
    """
    call()
    return min(_timed(call) for _ in range(runs))


def _timed(call: Callable[[], object]) -> float:
    start = time.perf_counter()
    call()
    return time.perf_counter() - start


def test_the_benchmark_length_is_the_longest_recording_that_can_be_uploaded() -> None:
    """The ceiling is a consequence of the limits, not a number someone chose.

    300 s at a 23.2 ms hop is 12 931 frames. If either moves, every assertion
    below moves with it — which is the point of deriving it. The upload limits
    themselves are asserted in ``test_config.py``; what matters here is that
    this suite measures *that* length and not another one.
    """
    settings = Settings()
    assert settings.max_audio_duration_seconds == 300
    assert settings.max_audio_size_mb == 50
    assert _ceiling_points() == 12931


def test_the_benchmark_timeline_is_one_the_estimator_would_actually_answer() -> None:
    """A benchmark on a refused input would time the gates and nothing else."""
    points = long_timeline(_ceiling_points())
    assert len(points) == _ceiling_points()

    analysis = analyse_key(points, frame_seconds=HOP_SECONDS)
    assert analysis.key is not None
    assert analysis.key.tonic == "C"
    assert analysis.key.mode is KeyMode.MAJOR
    # The whole accepted duration, so the correlation ran over a full-length
    # profile rather than an early return.
    assert analysis.voiced_seconds == pytest.approx(300.0, abs=1.0)


def test_key_estimation_stays_under_its_budget_at_the_longest_recording() -> None:
    """The stated ceiling: under 5 ms for a 300-second recording.

    Deliberately about 3.5x the measured 1.35 ms. A tighter bound would fail on
    a loaded machine, and a bound that only fails on a loaded machine teaches a
    reader to ignore it. Anything that makes this fail — a per-point
    correlation, a re-fold per candidate, a sort inside the loop — costs far
    more than 3.5x.
    """
    points = long_timeline(_ceiling_points())
    assert fastest(lambda: analyse_key(points, frame_seconds=HOP_SECONDS)) < 0.005


def test_key_estimation_costs_no_more_than_the_aggregation_beside_it() -> None:
    """The machine-independent half of the budget above.

    ``summarise_notes`` folds the same timeline and is already served
    synchronously on every ``/notes`` request, so it is a ceiling this product
    has been paying in production since Step 7I. Measured at 1.35 ms against
    2.95 ms — key is under half — and stated as "no more than", which leaves
    room for machine noise but none for an algorithmic regression.

    A ratio survives being run on hardware nobody has measured, which the 5 ms
    above does not. Both are kept: this one catches the regression, that one
    catches the two of them getting slow together.
    """
    points = long_timeline(_ceiling_points())
    key_seconds = fastest(lambda: analyse_key(points, frame_seconds=HOP_SECONDS))
    notes_seconds = fastest(lambda: summarise_notes(points, frame_seconds=HOP_SECONDS))
    assert key_seconds <= notes_seconds


def test_key_estimation_grows_with_the_timeline_and_not_with_its_square() -> None:
    """Four times the points must not cost sixteen times the work.

    The ceiling above is a bound on one length. This is the bound on the shape,
    and it is the assertion that would survive someone raising the accepted
    duration. Four times the longest accepted recording is measured at about
    3.7x; the gate is 8x, which linear clears comfortably and quadratic cannot.
    """
    at_ceiling = long_timeline(_ceiling_points())
    at_four_times = long_timeline(_ceiling_points() * 4)

    one = fastest(lambda: analyse_key(at_ceiling, frame_seconds=HOP_SECONDS), runs=5)
    four = fastest(lambda: analyse_key(at_four_times, frame_seconds=HOP_SECONDS), runs=5)
    assert four < one * 8


def test_key_estimation_never_copies_the_timeline() -> None:
    """Memory is twelve shares and 24 candidates, whatever the recording's length.

    The bound is what makes that a measurement rather than a claim. A copy of
    the timeline at four times the ceiling would be 413 KB of pointers alone
    before any ``PitchPoint`` was rebuilt, so a 64 KiB ceiling cannot be met by
    anything that keeps a second reference to every point. Measured: 7 216 bytes
    at the ceiling and 13 856 at four times it — the growth is the interpreter's
    integer objects for the running counts, not the timeline.
    """
    points = long_timeline(_ceiling_points() * 4)

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        analyse_key(points, frame_seconds=HOP_SECONDS)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak - before < 64 * 1024


def test_the_correlations_cost_the_same_whatever_the_recording_length() -> None:
    """24 correlations over twelve values each. The length is folded away first.

    Timed on a pre-folded profile, which is what :func:`estimate_key` receives,
    so this measures the scoring alone: 0.18 ms at any length. It is the reason
    the ceiling above is dominated by the fold and the reason no cache is
    warranted for the scoring half.
    """
    short = pitch_class_profile(timeline(MAJOR_MELODY), frame_seconds=HOP)
    long = pitch_class_profile(long_timeline(_ceiling_points()), frame_seconds=HOP_SECONDS)

    short_seconds = fastest(lambda: estimate_key(short, voiced_seconds=999.0))
    long_seconds = fastest(lambda: estimate_key(long, voiced_seconds=999.0))
    assert long_seconds < short_seconds * 4
