# Phase 8 — specification

> **Status: superseded. Phase 8 is built, and this is the record of how it was
> decided — not a description of the running system.**
>
> Candidate A shipped in full: all six slices are delivered and the definition of
> done is met. For what the code actually does, read
> [audio-analysis.md](audio-analysis.md) (algorithm, profile set, thresholds,
> what didn't work, cost), [api.md](api.md) (the endpoint and both response
> shapes), [limitations.md](limitations.md) (what it will not tell you) and
> [architecture.md](architecture.md) (where it lives). Where this file and those
> disagree, **they are right and this is history** — three of its claims were
> corrected by measurement during implementation, and the corrections are
> recorded below rather than edited into the original text.
>
> Kept rather than deleted because the reasoning is the valuable part: the
> decision gate, the two candidate scopes, the three corrections, and the
> measurements that ruled out the obvious implementation. Candidate B (§14) and
> every non-scope item in §5 remain unbuilt and out of scope.
>
> **The decision gate was answered on 2026-08-12: Candidate A — musical key.**
> See [The decision gate](#4-the-decision-gate).
>
> **§13 plans a live, in-browser key readout** for Live Vocal Practice — the key
> updating while somebody sings, in Live Vocal Practice. It is an extension
> numbered *after* this phase, planned only, and **nothing in it is built**.
> Phase 8's completion above does not depend on it.

## Revision history

| Step | What happened |
| --- | --- |
| 10.7 | First specification. Audited the repository, found no Phase 8 implementation, and scoped Phase 8 to a melodic key estimator derived from the stored pitch timeline. |
| 10.8 | Re-audited to resolve the blocking question 10.7 raised. **Two of 10.7's load-bearing claims did not survive re-inspection.** The key-estimation scope is no longer recommended on evidence; it is now a product decision. A second candidate scope was recovered from a deferral 10.7 got wrong. |
| 10.8 (decision) | Product answered: **Candidate A**, musical key only. Phase 9 confirmed as a blocked product dependency, not something to work around. |
| 10.8 (Slice 1) | Pure domain built and measured. Thresholds set from a sweep, the profile set chosen on evidence, and **one specification claim corrected by the measurements** — see below. |
| 10.8 (Slices 2–3) | The service read and the endpoint. Derived on read, no migration, no new error code. |
| 10.8 (Slice 4) | The card. Every state in §9 built, and each one reached deliberately in a browser against the real stack; §9's open question 3 answered by measurement — see below. |
| 10.8 (Slice 5) | The performance ceiling and the mutation run. The endpoint stopped loading the analysis document twice. One mutation survived the first run and exposed an untested property — the definition of the confidence margin — which `AMBIGUOUS_MODE` now pins. |
| 10.8 (Slice 6) | The documentation sweep, and this file marked superseded. **Phase 8 complete.** |
| Live key (planning) | A live, in-browser key readout added as **§13, Slices 7–9** — an extension after this phase, not a widening of it. Planned only; nothing implemented. |
| Live key (mutation audit) | 24 mutations across the browser estimator and the fold: 23 caught, one equivalent. Six survived the first run — five of them one blind spot in the fixtures, one a field the parity table did not assert. Both closed; the table gained `alternative_confidence`. |
| Live key (Slices 7–9) | Built. The estimator in TypeScript, held to `key.py` by a shared parity table; cumulative accumulation over every voiced frame; the readout in the practice card. **The plan's two commitment rules became one**: the dwell was set by the sweep the plan asked for, and hysteresis was measured to be unreachable below the confidence gate — see §13. |

### What the Slice 1 sweep settled

Every threshold in §6 was left deliberately unset until the fixtures existed.
Running them produced these, all now recorded beside the constants in
`app/services/audio_analysis/key.py`:

| Constant | Value | What set it |
| --- | --- | --- |
| Profile set | **Temperley** | Both sets identify all 24 synthetic keys, so accuracy did not decide it. Temperley separates a sung melody from random weights by ~12× (0.262 vs 0.013); Krumhansl–Kessler by ~3× (0.246 vs 0.088). The wider band is what leaves room for a gate |
| `MIN_DISTINCT_PITCH_CLASSES` | **5** | The smallest value admitting a pentatonic melody while refusing a four-class arpeggio. It carries more weight than expected: a *three*-class arpeggio scores a margin of **0.309**, higher than real music, so confidence alone can never catch it |
| `MIN_VOICED_SECONDS` | **1.0** | Derived: five held notes at the analyzer's own 116 ms rule is ~0.58 s, rounded up. Earns its place independently — a 0.53 s five-class fixture scores 0.281 and passes every other gate |
| `MIN_KEY_CONFIDENCE` | **0.05** | Set from the noise floor: uniform 0.000, chromatic 0.010, random 0.013, worst refusable fixture 0.022. Real melodies jittered ±50% (200 draws each, tonic and mode correct in 599 of 600) have 5th-percentile margins of 0.097 / 0.081 / 0.046 |
| `MIN_PITCH_CLASS_SHARE` | **2.0** | A stray frame in a two-second recording is worth ~1.2% of voiced time; the least-used class of a real seven-note melody is several times that |

**Correction 3 — a bare unweighted scale is answered, not refused.** 10.7 stated
that seven equally-weighted diatonic degrees must return `null`, on the
Krumhansl–Kessler measurement where it leads by 0.044, level with noise. Under
the profile set that actually shipped it leads by **0.146** — above the gate, and
well below the 0.19–0.23 a weighted melody reaches. It is therefore answered,
and answered *weakly*: the confidence and the runner-up both say how thin the
evidence is, and the twelve equal shares are returned so a reader can see it.
Reporting a bare C-major scale as C major is also what a listener would do.
`test_a_bare_unweighted_scale_is_answered_but_only_just` pins this.

The tails do overlap, and the specification says so rather than hiding it: one
jittered minor melody in 200 scored 0.004, below the chromatic fixture. No
threshold separates those two, and a feature whose honest answer is sometimes
"not measured" is the specified behaviour rather than a shortfall.

### What 10.8 changed, and why

**Correction 1 — Phase 9 does not consume a musical key.**

10.7 classified key estimation as *required* and BPM as *deferred*, and the whole
asymmetry rested on one sentence: *"Phase 9's 'transpose suggestions' needs a key
to transpose from."*

That is wrong, and the repository already said so. `docs/limitations.md:373`:

> A compatibility score compares **a detected range against an estimated song
> range**.

Phase 9 is a **range** operation, not a key operation. Transposing a song to fit
a singer shifts the song until its range sits inside the singer's range; the
singer's own key on some unrelated recording is not an input to that at any
point. `docs/roadmap.md:17` lists "range overlap" first for the same reason.

The consequence is direct. 10.7 deferred BPM on three grounds, of which the
second was *"nothing consumes it … Key does have a consumer; tempo has none."*
With that distinction gone, **the same three grounds now apply to key**, and
applying a rule to one feature and not to an identical one is the failure this
document exists to prevent.

**Correction 2 — the deferral of note events was based on a contradiction that
does not exist.**

10.7 deferred melody note events (D4) because building them "requires a minimum-
duration threshold, and `notes.py` documents refusing exactly that threshold as
*the one place the feature could have quietly lied*."

Re-reading both modules, that is a misreading. `notes.py` refuses a *new,
arbitrary* threshold on a **share-of-time table**, where deleting short notes
would silently misreport percentages. It does not refuse the idea of a held
pitch. The analyzer already has a held-pitch rule — `_sustained()`, with
`MIN_RANGE_FRAMES = 5` and `RANGE_CONTINUITY_SEMITONES = 1.0`
(`analyzer.py:104-106`) — which is documented, tested, and exists for precisely
the question "is this a note somebody actually sang?" A note-event sequence built
on **that** rule introduces no new threshold at all.

So note events are cheaper and better-grounded than 10.7 judged, and they are
now recorded as the alternative candidate scope.

---

## 1. What the product can already do

Reconstructed by reading the code, not the roadmap. Every "yes" below is backed
by a named test.

| # | Question | Answer | Where | Evidence |
| --- | --- | --- | --- | --- |
| **A** | Instantaneous pitch — is A4 ≈ 440 Hz? | **Yes**, three times over | `audio_analysis/detector.py` (backend, NSDF), `audio_analysis/pitch.py` (conversions), `frontend/lib/pitch-detector.ts` (live) | `test_440_hz_is_a4`, `test_440_hz_is_detected_as_a4` (within 5 cents), `test_a_held_a4_is_measured_as_a4` end to end through a real WAV |
| **B** | A pitch timeline — C4 → E4 → G4 → E4 | **Yes**, at frame resolution | `AudioMetrics.pitch_points`, `GET …/audio-analysis/pitch` | `test_the_timeline_is_ordered_and_covers_the_recording`. Each point carries timestamp, Hz, MIDI, note name, cents and a measured confidence |
| **C** | Lowest / highest detected pitch | **Yes** | `_vocal_range()` | `test_a_two_note_recording_reports_the_span_between_them` |
| **D** | Vocal range | **Yes**, as *range in this recording* | `VocalRange` — Hz, note names, semitone span, over *held* pitches only | `test_a_momentary_glitch_does_not_widen_the_reported_range`. Never a physiological limit, everywhere |
| **E** | Musical key | **No.** Nothing anywhere | — | Zero occurrences of `chroma`, `key_detection`, `pitch_class` in any source file |
| **F** | Melody note events | **Partially, and not as events** | `notes.py` aggregates the timeline per note — but **sorted by duration, not by time**, so it is a histogram, not a sequence. `PitchGraph` draws the contour visually | `test_audio_notes.py`. No endpoint returns an ordered note sequence |
| **G** | Any concept of an original / reference song | **No.** Categorically | — | `song` appears in code **only as a test upload filename** (`song.mp3`). `reference` never means a reference recording — it is A4 = 440 Hz, a loudness reference level, a foreign key, or a test's expected value |
| **H** | Any way to compare singing against a reference melody | **No** | — | Four places in the shipped docs and one in the shipped UI say so outright: *"there is no reference melody to compare against"* (`limitations.md:64,93,107,140`, `audio-analysis-result.tsx:113`). The nearest thing is Live Practice's optional **target note** — a single user-chosen note, guaranteed by its own tests not to imply correctness |

The honest summary: **this product measures pitch extremely well and has no
musical context whatsoever.** It knows what you sang. It has no idea what you
were trying to sing.

---

## 2. The concepts, kept apart

"Key detection" and "pitch detection" are not the same thing, are not the same
kind of thing, and must never be allowed to blur into each other in this
codebase. Each row is one concept, one example, one status.

| Concept | What it is | Example | Status here |
| --- | --- | --- | --- |
| **Frequency** | A physical measurement, in hertz. Continuous | `441.3 Hz` | Measured per frame |
| **Pitch** | A frequency interpreted against a tuning reference — a note plus a deviation | `441.3 Hz → A4, +5 cents` | Measured. A4 = 440 Hz, 12-TET |
| **Pitch class** | A note name with the octave discarded | `A4, A3, A5 → A` | **Does not exist** |
| **Note event** | One held pitch with a start and an end. A *sequence* of these has order | `A4 from 0.42 s to 0.91 s` | **Does not exist as an event.** The frames exist; nothing groups them into ordered events |
| **Melody** | The ordered sequence of note events | `A4 → B4 → C5` | Drawable from the timeline; **not returned as a sequence** |
| **Vocal range** | The lowest and highest pitch *held* in one recording | `C4–A4, 9 semitones` | **Built** (Step 7I). Not a physiological limit |
| **Musical key** | A *classification*: which tonic and mode a set of pitch classes best fits | `C major` | **Does not exist** |
| **BPM / tempo** | Beats per minute — a rate, from onsets in time | `120 BPM` | **Does not exist** |
| **Beat tracking** | *Where* the beats fall, in seconds | `0.5 s, 1.0 s, 1.5 s` | **Does not exist** |
| **Reference song** | A second audio input, or a stored melody, to compare against | the original recording of a song | **Does not exist, and cannot be invented** |
| **Transposition** | Shifting a *song* by *n* semitones so it fits a voice | `down 3 semitones` | Phase 9. Needs a reference |
| **Compatibility** | Whether a singer's range overlaps a song's range | `your range covers 82% of it` | Phase 9. Needs a reference |

Three distinctions carry the rest of this document:

**Pitch is a measurement; key is a classification.** A pitch is arithmetic on a
frequency — `midi = 69 + 12·log₂(f/440)` — and it is either right or wrong in a
checkable way. A key is a *label* chosen by correlating a distribution against
hand-authored profiles. `CLAUDE.md`'s non-negotiable list contains *"Do not label
timbre ('bright', 'breathy') from unvalidated spectral numbers"*, and a key label
is structurally the same move on different numbers. That does not make it
forbidden — it makes it a decision somebody must take deliberately.

**A pitch class is not a note, and a note is not a note event.** `A4` is a pitch;
`A` is a pitch class (an octave's worth of information deliberately thrown away);
`A4 from 0.42 s to 0.91 s` is an event. The existing note breakdown is at the
*second* level and sorted by duration — it is a histogram, and calling it a
melody would be a category error.

**Range is not key.** The range is a pair of extremes. The key is a claim about
tonal centre. A singer with a C4–A4 range could be singing in any key. Phase 9
consumes the first and not the second.

---

## 3. Feature classification

Classified against repository evidence only.

| Feature | Classification | Evidence |
| --- | --- | --- |
| Instantaneous pitch | **Already built** (7H, 7I) | Detector, conversions, three test layers |
| Pitch timeline | **Already built** (7I) | `pitch_points`, `GET …/pitch` |
| Vocal range | **Already built** (7I) | `VocalRange`, held-pitch rule |
| Pitch-class profile | **Product decision required** | Buildable and cheap, but exists only to feed a key estimate |
| Musical key | **Product decision required** | See §4. No consumer, no validation path, and it is a label rather than a measurement |
| Note events / melody sequence | **Candidate for Phase 8** | Named in the roadmap row; reuses an existing tested rule; no new threshold; the one gap in "can it show A4 → B4 → C5?" |
| BPM / tempo | **Not supported by the current product** | Unaccompanied voice, no percussion, no consumer, no ground-truth fixture |
| Beat tracking | **Not supported by the current product** | Strictly harder than BPM, with no consumer even hypothetically |
| Chroma (spectral) | **Not supported by the current product** | Exists to recover harmony from polyphony. There is no polyphony here |
| Melody extraction *from a song* | **Not supported by the current product** | There is no song |
| Reference-song input | **Not supported by the current product** | No catalogue, no second input, no provider. **Must not be invented** |
| Vocal separation | **Not supported by the current product** | Nothing to separate — the input is already one voice |
| Transposition | **Phase 9** | `roadmap.md:17` |
| Compatibility | **Phase 9** | `roadmap.md:17`, `limitations.md:371` |

---

## 4. The decision gate

### Does "pitch timeline → pitch-class profile → musical key" make sense here?

Answered against the eight questions this step asked, and the answers are not
uniform.

**What user problem does it solve?** None that the repository states. The
repeated, documented complaint in this product — five separate places, four in
the docs and one in the shipped UI — is *"there is no reference melody, so it
cannot say whether the note was the right one."* A key estimate does not answer
that. Knowing you sang in G major does not tell you whether G major was right.

**What existing feature consumes the result?** **Nothing.** This is Correction 1.
Phase 9 compares ranges, not keys.

**Is there a UI use?** A card stating a fact. That is not disqualifying by itself
— the product already reports spectral centroid and flatness with no action
attached — but those are direct measurements of the signal, and this would be the
first *classification with a label* in the system.

**Does Phase 9 depend on it?** No. And Phase 9 is blocked anyway, on the
reference input that does not exist.

**Can it work meaningfully on an isolated vocal?** Only sometimes, and the
measurements say when. Taken during Step 10.7, correlating pitch-class profiles
against the standard key profiles:

| Input | Best candidate | *r* | Margin over 2nd |
| --- | --- | --- | --- |
| Random weights | G minor | **+0.428** | 0.040 |
| One held note — a hum | C major | **+0.684** | **0.000** |
| Two pitch classes | C major | **+0.831** | 0.079 |
| Chromatic wander | G♯ minor | +0.393 | 0.072 |
| Bare C major scale, unweighted | C major | +0.756 | 0.044 |
| C major melody, tonic/dominant heavy | C major | +0.966 | **0.246** |
| A minor melody, same seven classes | A minor | +0.919 | **0.276** |

- **A single hummed note** scores +0.684 for C major. Only a margin computed
  against the next-best candidate *of any kind* catches it — its margin over a
  different tonic is 0.248, indistinguishable from real music.
- **Two pitch classes** score +0.831 with a margin above chromatic noise. No
  correlation rule catches this; only a separate distinct-pitch-class gate does.
- **Noise** scores +0.428. An estimator built the obvious way reports a confident
  key for meaningless input.
- **A bare, unweighted scale** has a margin of 0.044 against noise's 0.040 —
  genuinely ambiguous between C major and A minor, and correctly unanswerable.

So it works on a sung melody that uses most of a scale and leans on its tonic,
and honestly refuses on everything else. That is a defensible feature. It is not
an obviously valuable one for a product whose homepage says *"Hear how you
speak."*

**Computability is not usefulness.** All of the above is cheap, deterministic and
provably correct against synthetic input. None of that establishes that anyone
wants it.

### The verdict

Under this project's own rules, key estimation fails two tests and passes one:

- ✗ **No consumer**, which is the exact ground on which BPM was deferred.
- ✗ **A label, not a measurement**, never validated against human singing —
  and §7 records that no corpus exists here to validate it with.
- ✓ **Honest under-answering.** With the gates specified in §6 it refuses far
  more often than it answers, which is the correct behaviour and is unusual
  enough to be worth something.

That balance is close, and *close is what makes it a product decision rather than
an engineering one.* An engineer choosing here would be inventing a requirement.

> ### Product decision — answered 2026-08-12
>
> The question put was: **should VocalLens report an estimated musical key for a
> recording, as a measurement in its own right, knowing that nothing consumes it,
> that it will often answer "not measured", and that it cannot be validated
> against real singing in this repository?**
>
> **Answer: yes — Candidate A, and only Candidate A.** Phase 8 implements the
> pitch-class profile, a tonic and major/minor estimate, a confidence, `null`
> when the evidence is insufficient, and the frontend presentation. Nothing else.
>
> The three costs above are accepted knowingly rather than argued away, and each
> one has an obligation attached that this specification already carries:
>
> - *No consumer* → the feature must stand on its own as a measurement, so §9
>   requires the pitch-class evidence to be shown beside the label in every state.
> - *Often "not measured"* → that is the specified behaviour, not a shortfall.
>   The adversarial fixtures in §10 make it a test failure to guess instead.
> - *Unvalidatable here* → §10 states it outright, and no test may claim
>   otherwise.
>
> **Phase 9 was answered at the same time and separately:** the absence of a
> reference song is a *blocked product dependency*, not something to invent or
> work around. No catalogue, external metadata provider, vocal separation or
> reference-audio input may be introduced by Phase 8, and how a reference song is
> supplied is its own product decision when Phase 9 starts.
>
> Candidate B (§14) is **not** being built. It stays recorded because the
> reasoning that recovered it is worth keeping, not because it is queued.

---

## 5. Candidate A — musical key: scope and non-scope

Everything from §5 to §12 specifies Candidate A. **It is fully specified and must
not be built until the gate above is answered.**

### Scope

1. **Pitch-class profile** — twelve values, the share of voiced time spent on
   each pitch class, folded from `PitchPoint.midi_note` modulo 12, hop-weighted,
   normalised to sum to 1.
2. **Key estimate** — tonic, mode, a measured confidence, and the runner-up.
   `tonic: null` whenever the evidence gates are not cleared.
3. **Limitations messaging** — named in the roadmap row, and not decoration:
   §11 must ship with the feature or the feature must not ship.

### Non-scope

BPM · beat tracking · downbeats · spectral chroma · note events · melody
extraction from a song · reference-song input · vocal separation · transposition
· compatibility · key in the AI feedback payload · key as a progress series · key
in comparison · enharmonic spelling (`pitch.py` documents that a lone frequency
cannot supply it) · modal keys beyond major and minor.

Two of those deserve their reasons restated, because they are the ones most
likely to be re-proposed:

**Key in the AI payload.** `services/ai/` is given measurements and returns
prose; the audio prompt forbids timbre labels and scores. Handing it a key
invites musical advice ("try singing in A instead") that nothing in this system
can support. Deferred until the prompt rules for it are written first.

**Key as a progress series.** `services/progress/sources.py` extracts scalars by
JSON path from the stored document and cannot call a Python function. A
key-over-time chart would force persistence, a migration and a version field that
the storage design in §8 exists to avoid. That is the one trigger to revisit §8.

---

## 6. Candidate A — input, algorithm, outputs, confidence

### Input

**No audio is analysed.** The input is the **stored pitch timeline of a completed
audio analysis** — the `pitch_points` already persisted in the `audio_analyses`
JSONB document by Step 7I.

| Question | Answer |
| --- | --- |
| What is read | `tuple[PitchPoint, ...]` from a completed `AudioAnalysis` |
| Which component provides it | `audio_analysis/analyzer.py` → `postgres_repository.py` |
| Uploaded recording? | Only indirectly — decoded once, in Step 7I |
| Reference song? | No. None exists |
| Formats, duration | Nothing is decoded. Inherited: ≤ 300 s, so ≤ ~12 931 points |

Two alternatives were considered and rejected:

**A second decode pass** would decode every recording twice and add a fourth copy
of the JSON-document write discipline `architecture.md` already calls "deliberate
and temporary" — its own orchestrator, staleness sweep, partial unique index and
idempotency rules — to measure something derivable from data already on disk.

**Spectral chroma inside the existing pass** would be nearly free in CPU terms
and is the textbook approach. It is rejected because it exists to recover
*harmony* from polyphony and there is none here: on one unaccompanied voice its
extra information is the singer's own harmonics, and the third harmonic lands a
fifth above the fundamental, polluting a pitch class nobody sang. Revisit **only**
if a polyphonic input ever exists in this product.

Reading the timeline instead inherits, free, every guard that has tests behind
it: the clarity gate, octave-outlier rejection, note conversion, and the
`INSUFFICIENT_PITCH_SIGNAL` refusal. `notes.py` is the precedent, down to the
docstring: *"It decodes nothing, detects nothing and re-measures nothing."*

**When the input is unavailable** — three states that must stay distinct:

| Situation | Behaviour |
| --- | --- |
| Recording unknown, or another owner's | `404` `RECORDING_NOT_FOUND` — one answer for both |
| Exists, never analysed, or pending / failed | `404` `AUDIO_ANALYSIS_NOT_FOUND` |
| Completed, timeline present, evidence insufficient | `200`, `key: null`, with a stated reason |

The third is **not an error**. It is the same shape as
`INSUFFICIENT_PITCH_SIGNAL`: a normal answer meaning "the signal did not support
this", rendered as *not measured*.

### Algorithm

```
tuple[PitchPoint, ...] ──▶ pitch-class profile (12 floats, sum 1)
                                    │
                                    ▼
                      24 correlations (12 tonics × 2 modes)
                                    │
                                    ▼
                        KeyEstimate | tonic = None
```

No IO, no numpy, no provider, **no new dependency**.

**Step 1 — the profile.** For each point, add one hop of duration to
`midi_note % 12`, then divide by the total. Hop-weighted for the reason
`notes.py` gives: frames overlap, so charging each its full length multiplies
every duration. Deliberately **not** weighted by amplitude or confidence — either
would create a second definition of "how much of this note was sung" alongside
`notes.py`'s.

**Step 2 — the key.** Pearson-correlate the profile against 24 rotated key
profiles and take the highest. **Temperley's revised Kostka–Payne weights are the
primary candidate**, being corpus-derived rather than probe-tone-derived, which
is the closer match to "which pitch classes did this melody use". Krumhansl–
Schmuckler is the documented alternative, and the implementation **must run both
across the §9 fixtures and record which won and by how much**, as
`docs/audio-analysis.md` records the clarity-threshold measurement. Neither is
adopted on reputation.

**Step 3 — confidence.** The table in §4 is the specification here, and three
rules follow from it directly:

1. **Confidence is a margin, never a raw correlation.** Noise scores +0.428 and a
   hum scores +0.684; any threshold on raw *r* reports a key for a hum.
2. **The margin is over the next-best candidate of any kind**, not the next-best
   *different tonic*. The hum is the proof: 0.248 over a different tonic,
   0.000 over the next candidate, because C major and C minor fit one note
   equally well.
3. **A margin alone is not enough.** Two pitch classes clear it. Two independent
   **evidence gates** are required before any key is reported: a minimum number
   of **distinct pitch classes** present above a small share of voiced time, and
   a minimum total **voiced duration**. Both thresholds must be chosen by
   sweeping the §9 fixtures and **recorded with their measurements**, exactly as
   the 0.80 clarity threshold was. **Do not hard-code a guessed number.**

### Outputs

`KeyEstimate` — `tonic` (pitch-class name or `null`), `mode` (`major` | `minor`),
`confidence` (the margin), `alternative` (the runner-up as the same shape).
Alongside it: the twelve `PitchClassShare` values, `distinct_pitch_classes`,
`voiced_seconds`, and `method` naming the profile set used. When `tonic` is
`null`, an `unmeasured_reason` of `TOO_FEW_PITCH_CLASSES`,
`TOO_LITTLE_VOICED_TIME` or `AMBIGUOUS`.

### Known failure cases, to ship in the docs

- **No harmony is visible.** This is a *melodic* key estimate. A melody sung over
  chords in another key is read as the melody's key.
- **Modulation.** One estimate per recording; a modulating recording gets an
  average that may be neither, and usually falls below the gate.
- **Relative major / minor** share seven pitch classes and separate only by
  emphasis. Melodies emphasising neither are ambiguous by construction.
- **Modal melodies** are forced into the nearest major or minor.
- **Non-12-TET intonation** is folded to the nearest equal-tempered pitch class —
  the same caveat `limitations.md` already records for pitch accuracy.
- **Short or sparse recordings** — handled by the gates, which is why they exist.
- **Speech.** Most speech fails upstream with `INSUFFICIENT_PITCH_SIGNAL` and
  never arrives. A monotone hum does arrive, and gate 3 is what stops it becoming
  "C major".

---

## 7. Candidate A — storage

**Nothing new is persisted. No migration. No table. No column. No index.**

The key is **derived on read**, the precedent `AudioAnalysisService.notes` sets:
*"Derived from the stored pitch timeline on read rather than persisted alongside
it: it is a pure function of points that are already on disk, and storing it too
would be a second copy to keep consistent."*

| Property | Consequence |
| --- | --- |
| No migration | Nothing to review, checksum or roll back |
| No new ownership surface | Nothing new can leak, because nothing new is stored |
| **Every existing completed analysis is answerable** | No backfill, no re-analysis endpoint, no `?refresh`, no version field |
| `null` is unambiguous | Always "measured, insufficient evidence" — never "analysed before this existed" |

The last one avoided a real trap. Had the key been computed inside
`SignalAudioAnalyzer` and stored, `POST …/audio-analysis` returns an
already-completed analysis unchanged (`orchestration/audio_analysis.py:337`), so
**no existing recording could ever have gained a key** — and `null` would have
meant two different things with no way to tell them apart, in a codebase whose
stated discipline is that `null` is a gap and never a zero.

Ownership is enforced where it already is: in the SQL `WHERE` clause of the
existing recording read, via `AudioAnalysisService.current()` → `_require_owned()`.
Phase 8 adds no query, so it can weaken no predicate. Results are neither
immutable nor replaceable because they are not stored; every read recomputes from
the same timeline and returns the same answer.

---

## 8. Candidate A — API

### Existing endpoints, unchanged

`GET|DELETE /api/v1/identity` · `POST /api/v1/identity/credentials` ·
`DELETE /api/v1/identity/credentials/{id}` · `POST /api/v1/recordings` ·
`GET /api/v1/recordings` · `GET /api/v1/recordings/{id}` ·
`GET /api/v1/recordings/progress` · `GET /api/v1/recordings/compare` ·
`POST|GET /api/v1/recordings/{id}/analysis` ·
`POST|GET /api/v1/recordings/{id}/audio-analysis` ·
`GET /api/v1/recordings/{id}/audio-analysis/pitch` ·
`GET /api/v1/recordings/{id}/audio-analysis/notes` ·
`POST|GET /api/v1/recordings/{id}/audio-analysis/feedback` ·
`GET /api/v1/config` · `GET /health` · `GET /api/v1/health`

### Proposed — one new endpoint

#### `GET /api/v1/recordings/{recording_id}/audio-analysis/key`

No body, no query parameters. Identity via `X-VocalLens-Owner`, as everywhere.

**200 OK**, measured:

```json
{
  "key": {
    "tonic": "G",
    "mode": "major",
    "confidence": 0.246,
    "alternative": { "tonic": "D", "mode": "major", "confidence": 0.198 }
  },
  "pitch_classes": [
    { "pitch_class": 0, "name": "C",  "percentage_of_voiced_time": 4.1 },
    { "pitch_class": 1, "name": "C#", "percentage_of_voiced_time": 0.0 }
  ],
  "distinct_pitch_classes": 7,
  "voiced_seconds": 5.84,
  "method": "temperley"
}
```

**200 OK**, evidence insufficient — identical shape, `key` is `null`:

```json
{
  "key": null,
  "unmeasured_reason": "AMBIGUOUS",
  "pitch_classes": [ "…" ],
  "distinct_pitch_classes": 2,
  "voiced_seconds": 5.84,
  "method": "temperley"
}
```

`unmeasured_reason` is a **reason, not an error code**: never in the error
envelope, never an HTTP status, not added to `ErrorCode`. The measurements are
returned alongside it either way, so a reader can see *why* the answer is "not
measured" rather than being asked to trust it.

| Situation | Status | Code |
| --- | --- | --- |
| Measured, or measured and inconclusive | `200` | — |
| Recording unknown, or another owner's | `404` | `RECORDING_NOT_FOUND` |
| No completed audio analysis | `404` | `AUDIO_ANALYSIS_NOT_FOUND` |

**No new error code.** Both already exist and already mean exactly this.

**Ownership** — identical to `/notes`: the owner is in the SQL `WHERE` clause, so
another owner's recording is never selected rather than selected and filtered.
Somebody else's recording answers `404`, indistinguishable from one that does not
exist. No `owner_id`, credential, hash or internal identifier appears in the
response.

**Idempotency** — safe, idempotent, side-effect free. Repeating it returns
byte-identical output; it writes nothing, so a retry cannot double anything.

**Synchronous** — no background task, status field or polling. §10 justifies it.

**Rate limiting** — a read, so **not** charged against the costly-request limit,
consistent with `/notes`, `/pitch`, comparison and progress.

**Rejected alternative:** adding `key` to the `GET …/audio-analysis` summary. It
saves one request, and is rejected because `summary` mirrors the stored
`AudioMetrics` document; mixing a derived-on-read value into it blurs the
stored/derived boundary `/notes` was given its own path to keep clean.

---

## 9. Candidate A — frontend

No new page, no new route. One card in the existing audio-analysis result
section, one call in `lib/api.ts`, one hand-mirrored type in `types/api.ts`.

| State | Trigger | What is shown |
| --- | --- | --- |
| **Loading** | request in flight | The section's existing loading treatment. Never a skeleton key label |
| **Measured** | `key` present | Tonic + mode, the confidence with its definition attached, the runner-up, and the pitch-class evidence |
| **Low confidence** | confidence in the lowest reported band | The same card with the weakness stated in words — never hidden, never rounded up |
| **Not measured** | `key: null` | "Not measured", the reason in plain language, and the pitch-class table anyway |
| **Unavailable** | `404 AUDIO_ANALYSIS_NOT_FOUND` | "This recording's audio has not been analysed yet" — the treatment `/notes` already uses |
| **Error** | any other failure | Handled inline via `lib/analysis-errors.ts`, as today. It must **not** reach `app/error.tsx` — a handled API failure arriving at a boundary is a bug in the panel (Step 10.4) |

A completed analysis with an empty timeline cannot occur — the analyzer raises
`INSUFFICIENT_PITCH_SIGNAL` instead — so no separate empty state exists, and none
must be invented.

Copy rules, testable:

- The label is **"Estimated key of what was sung in this recording"**, never "the
  key of the song". There is no song.
- Confidence carries its definition ("margin over the next-best candidate"),
  never a percentage of correctness and never a grade.
- Colour is never the only cue, matching the pitch meter's rule.
- No transposition suggestion, no "you should sing in…", no difficulty claim.

---

## 10. Candidate A — fixtures, validation and performance

### There is no real-world reference dataset

Stated plainly: **this repository contains no annotated real-world music, no
key-labelled corpus, and no recording of a human being singing anything.** Every
audio fixture is synthesised — `harmonic_samples`, `noise_samples`,
`silence_samples` in `tests/fixtures.py`.

**Synthetic fixtures cannot validate human singing, and no test may claim they
do.** What they can prove: that the algorithm implements the algorithm, that it
is transposition-invariant, that it is deterministic, and — most valuably — that
it refuses to answer when it should. Validating against real singing requires a
labelled corpus with its own licensing questions, and acquiring one is not part
of Phase 8.

### The test plan

**Already passing, and must not regress** (they are the foundation everything
here stands on):

| # | Test | Where |
| --- | --- | --- |
| 1 | `test_440_hz_is_a4` — the conversion | `test_audio_pitch_math.py:34` |
| 1 | `test_440_hz_is_detected_as_a4` — within 5 cents of 440 | `test_audio_detector.py:53` |
| 1 | `test_a_held_a4_is_measured_as_a4` — end to end through a real WAV | `test_audio_analyzer.py:63` |
| 3 | Deterministic pitch extraction — same file, same numbers, and a full-signal round trip at four sample rates | `test_audio_detector.py`, `test_audio_analyzer.py` |
| 3 | The same reference cases asserted independently in the browser implementation | `frontend/tests/pitch.test.ts` |

**New, deterministic** — constructed `PitchPoint` tuples, microseconds, no audio:

| # | Fixture | Assertion |
| --- | --- | --- |
| 4 | Pitch-class folding | `A3`, `A4`, `A5` all fold to `A`; shares sum to 100% within `PERCENTAGE_TOLERANCE` |
| 5 | Tonic/dominant-weighted major melody, all 12 tonics | exact tonic and mode, 12/12 |
| 5 | The same in minor, all 12 tonics | exact tonic and mode, 12/12 |
| 2 | Transposition invariance, *n* = 1…11 | tonic shifts by exactly *n*; mode identical; confidence identical to 1e-9 |
| 5 | Determinism | byte-identical output across runs, including tie-break order |

**New, adversarial — the ones that matter.** Every one must return `tonic: null`,
and each is drawn from a measurement in §4 rather than imagined:

| # | Fixture | Measured behaviour without gates | Required answer |
| --- | --- | --- | --- |
| 7 | One pitch class held throughout (a hum) | **C major at r = 0.684** | `null`, `TOO_FEW_PITCH_CLASSES` |
| 8 | Two pitch classes only | **C major at r = 0.831** | `null`, `TOO_FEW_PITCH_CLASSES` |
| 9 | Random weights across all 12 | **G minor at r = 0.428** | `null`, `AMBIGUOUS` |
| 10 | Chromatic wander, all 12 near-equal | G♯ minor at r = 0.393 | `null`, `AMBIGUOUS` |
| 10 | Uniform profile | ties at r = 0.000 | `null`, `AMBIGUOUS` |
| 6 | Very short timeline | — | `null`, `TOO_LITTLE_VOICED_TIME` |
| 6 | Empty timeline | — | unreachable via the endpoint; the pure function returns `null` |

**False negatives** are covered by the symmetric requirement: every fixture in
the deterministic table must *not* return `null`. A gate tuned until nothing
passes has broken the feature, and the two tables together are what catch that.

**Audio, end to end** — few, because they are slow and the deterministic set
covers the arithmetic. Real WAVs via `write_signal_wav`, real decoder, real
analyzer:

| Fixture | Assertion |
| --- | --- |
| Synthesised C-major arpeggio, tonic emphasised | resolves to C major end to end |
| The same arpeggio transposed to F♯ | resolves to F♯ major |
| White noise | fails upstream with `INSUFFICIENT_PITCH_SIGNAL`; the endpoint answers `404` and the estimator is never reached |

**Mutation**, in the manner of Steps 10.2–10.6. Each must make a named test fail,
and the script must be run and its output recorded: reverse the major/minor
profiles; drop the distinct-pitch-class gate; drop the voiced-time gate; change
the margin from *next-best candidate* to *next-best different tonic* (the hum
fixture catches this one); remove the hop weighting; remove the tie-break; return
the second-best candidate.

### Performance

Measured on this machine against the **existing** code, with a throwaway
prototype for the new part:

| Operation | Points | Wall clock |
| --- | --- | --- |
| `summarise_notes` — the existing aggregation of the same timeline | 12 931 | **3.51 ms** |
| Prototype pitch-class fold + all 24 correlations | 12 931 | **0.87 ms** |

12 931 points is the longest recording this product accepts — 300 s at the
default hop. The proposed work is roughly a quarter of an aggregation the product
already performs synchronously on every `/notes` request.

| Question | Answer |
| --- | --- |
| Maximum acceptable time | **< 5 ms** at 12 931 points, asserted with a generous ceiling so it fails on an algorithmic regression, not machine noise |
| Memory | 12 floats plus 24 correlations. The timeline is already in memory |
| Synchronous or background | **Synchronous.** A background task, status field and polling client for 0.87 ms of arithmetic is infrastructure with no measurement behind it |
| Caching | **None.** A deterministic function of a row already read |
| Redis, Celery, workers, queues | **None**, now or as follow-up. No measurement would justify one |

The honest caveat: the dominant cost is **loading the analysis document**, not
the arithmetic — the same JSONB read `/notes` already performs. If that becomes
the problem the fix is the one progress already uses, and it is not a Phase 8
concern.

---

## 11. Candidate A — security, ownership, providers

| Question | Answer |
| --- | --- |
| New persisted objects | **None** |
| Owner relationship | Unchanged: `owners → recordings → audio_analyses`, cascade at both levels |
| Cross-owner access | Owner in the SQL `WHERE` clause of the existing read, via `_require_owned`. No new query, so no predicate can be weakened |
| What another owner sees | `404`, identical to a recording that does not exist |
| Deletion | Unchanged. A derived value has nothing to delete and cannot survive its source |
| Retention | Unchanged. Step 10.6's predicate is "owns no recordings"; this creates no row |
| Audio or reference files stored | **None.** Nothing is decoded, written or uploaded |
| Identity seam | Untouched. The route takes `owner_id` from the resolver dependency |
| SQL | No new SQL. Nothing interpolated |
| Exposed identifiers | `owner_id`, credential ids, hashes and internal state appear nowhere |
| New privacy risk | **One, small:** the pitch-class profile is a more compact description of the singing than the note breakdown `/notes` already returns on the same terms. It reveals nothing `/notes` does not |
| New cost risk | None. No provider, model, billable call or disk write |
| Logging | Event name + `extra`. Log the recording id and the outcome; **never** the owner id, client address, or a key |

**External and billable providers: none.** No new dependency, no API key, no
environment variable, no configuration. The two gate thresholds and the profile
choice are **constants with recorded measurements**, like
`DEFAULT_CLARITY_THRESHOLD` — nothing needs to vary them per deployment, and a
setting would be a knob nobody can calibrate. Deepgram and Anthropic are
untouched; key is never sent to a model, so Phase 8 cannot increase model spend.
There is no external call that can fail.

---

## 12. Candidate A — limitations, done, and slices

### What it will not solve

To be carried into `docs/limitations.md` when the phase lands:

- **It does not analyse songs.** There is no song. It reports the key implied by
  what one person sang into one microphone.
- **It cannot hear harmony.** A melody sung over chords in another key is read as
  the melody's key.
- **It is not a claim that you sang in that key correctly**, or well.
- **It cannot separate relative major from relative minor** when the melody
  emphasises neither. It says so rather than guessing.
- **It assumes equal temperament**, like every other pitch measurement here.
- **It reports one key per recording.** Music that modulates gets an average, and
  usually gets "not measured".
- **It is not validated against real singing.** Every fixture is synthetic.
- **It gives no tempo, beat, melody transcription, transposition or
  compatibility judgement.**
- **It has no opinion.** No model sees it, no advice comes from it, and no field
  in the response could hold a score.

### Definition of done

**Backend** — `services/audio_analysis/key.py` as pure functions (no IO, numpy,
decoder or provider import); frozen models beside `NoteSummary`;
`AudioAnalysisService.key()` mirroring `.notes()`; both profile sets raced and
the loser recorded; both thresholds chosen from the fixture sweep with their
measurements written down; ruff, ruff format and mypy strict clean.

**Database** — **no migration.** `git diff` touches nothing in
`app/db/migrations/`, and the endpoint issues no new SQL.

**API** — the endpoint of §8; schema in `app/schemas/`; both `404`s; **no new
error code**; `docs/api.md` moved out of "Planned".

**Frontend** — type, client call, card; all six states of §9; the pitch-class
evidence shown in every state where it exists, including `null`; copy passing the
§9 rules; lint, typecheck and build clean.

**Tests** — every fixture in §10; the transposition property across 12 tonics and
both modes; the ownership test in `test_ownership_api.py`; the performance
assertion; the mutation script run and its output recorded; **no existing test
deleted, skipped, loosened or re-parametrised.**

**Browser** — measured state (a synthesised melody shows tonic, mode, confidence
and evidence); not-measured state (a hum shows a reason, **and no key label
anywhere on the page**); unavailable state (a recording with no analysis shows
the unavailable treatment, not an error); console clean in all three;
`app/error.tsx` not reached; mobile width and both colour schemes.

**Security** — no `owner_id`, credential id, hash or internal state in the
response; no new SQL and nothing interpolated; identity seam untouched; deletion
and retention verified unchanged by their existing suites.

**Performance** — < 5 ms at 12 931 points, measured and recorded; no Redis,
queue, worker, cache or background task added.

**Documentation** — `audio-analysis.md` gains a "Musical key" section with the
algorithm, the profile set and the one that lost, both thresholds with their
measurements, and what didn't work; `limitations.md` gains the section above;
`architecture.md` gains a feature-allocation row with its "Not" column filled and
its "Phase 8 has not started" line corrected; `roadmap.md` and `README.md`
updated; this file marked superseded, not deleted.

### Implementation slices

| # | Purpose | Files | Tests | Browser | Acceptance | Depends on |
| --- | --- | --- | --- | --- | --- | --- |
| 1 ✅ | **Delivered.** Pure domain: profile + estimator | `audio_analysis/key.py`, `models.py`, `tests/test_audio_key.py` | 94 tests. Every deterministic and adversarial fixture; both profile sets raced; the threshold sweep run and recorded above | — | Met: 24/24 tonics both modes, transposition invariance to 1e-9, every adversarial fixture `null` with the right reason, thresholds justified by recorded measurements. 14 mutations run — 13 caught, 1 confirmed equivalent and documented as such in the code | — |
| 2 ✅ | **Delivered.** `AudioAnalysisService.key()` | `orchestration/audio_analysis.py`, `test_audio_analysis_orchestration.py` | `None` for pending/failed/absent; `RECORDING_NOT_FOUND` for another owner; estimate for a completed one, driven by the existing stub analyzer | — | No new repository method, no new SQL, ownership passes through a substituted resolver | 1 |
| 3 ✅ | **Delivered.** The endpoint | `routes/audio_analysis.py`, `schemas/audio_analysis.py`, `test_audio_analysis_api.py`, `test_ownership_api.py`, `docs/api.md` | Both `200` shapes; both `404`s; another owner gets the same `404`; response asserted free of `owner_id`; asserted not to consume costly-request quota | `curl` through the running stack | `docs/api.md` documents the `null` shape and `unmeasured_reason`; no `ErrorCode` member added | 2 |
| 4 ✅ | **Delivered.** The UI | `types/api.ts`, `lib/api.ts`, `lib/audio-analysis-metrics.ts`, `hooks/use-audio-analysis.ts`, `components/audio-analysis/musical-key-card.tsx`, `tests/audio-analysis.test.ts` | 50 tests (16 new). A `null` key returns no label at all, the weak band is pinned to the measured fixtures, and no key string reads as a grade | Measured, low-confidence and not-measured at 390 px and 1280 px in both schemes — 12 runs, no console output, no horizontal overflow — plus the unavailable/error state forced separately. Loading is the state each run passes through before it settles | Met. Every state reached deliberately; a forced `404` on the key call alone is handled inline and `app/error.tsx` is not reached | 3 |
| 5 ✅ | **Delivered.** Performance and mutation | `tests/test_audio_key.py`, `orchestration/audio_analysis.py`, `routes/audio_analysis.py`, a mutation script kept outside the repository | The ceiling derived from `max_audio_duration_seconds` and `HOP_SECONDS`, not written down: 1.35 ms at 12 931 points, under half `summarise_notes`, 7 216 bytes peak, sub-quadratic at 4× | — | Met. 21 mutations: 20 caught by a named test, 1 confirmed equivalent. One survived — the margin redefined over the next *different tonic* — because every adversarial fixture is stopped by the pitch-class gate before the correlation is reached. `AMBIGUOUS_MODE` closes it and the run was repeated. The endpoint also stopped loading the analysis document twice | 1–3 |
| 6 ✅ | **Delivered.** Documentation | the six files above | — | — | Met. `audio-analysis.md` carries the algorithm, the profile race, all four thresholds with their measurements, what didn't work and the cost; `limitations.md` has the section in the shipped voice; `architecture.md` has its row and its corrections; `README.md` no longer lists shipped features as unbuilt; this file superseded, not deleted | 1–5 |

**Slices 7–9 are §13**, the live in-browser readout. They are an extension
after this phase, not part of the definition of done above.

---

## 13. Extension — live key estimation (Slices 7–9)

> **Not part of the agreed Phase 8 scope, and deliberately numbered after it.**
> The decision in §4 covered one uploaded recording. This is a second pipeline in
> a second runtime, so folding it into Slices 4–6 would have widened an agreed
> scope silently. **Phase 8 closed at Slice 6 without it**, and §12's definition
> of done neither includes nor depends on anything below.
>
> **Slices 7–9 are now built.** This section is kept as written, with the two
> places the measurements contradicted it marked in place rather than edited
> away — the commitment rules below, and the flicker fixture that set them.

### What it is

The key readout, live, while somebody sings — the estimate updating as evidence
accumulates instead of arriving only after an upload. It extends **Live Vocal
Practice (Steps 7H/7J)**, which already runs a full pitch pipeline in the page:
`lib/pitch-detector.ts` detects, `lib/pitch-stream.ts` smooths,
`lib/live-practice.ts` aggregates, and `hooks/use-live-stats.ts` publishes a
snapshot twice a second.

### Input

`LivePitchSample` frames, from the stream that already exists. Nothing new is
captured, nothing is uploaded, and **microphone audio still never leaves the
page** — which is the rule that decides the whole design below.

**Every voiced frame counts, not only held ones.** The backend folds every point
in the stored timeline; `_sustained` and `trackRange` apply to the *range* and
to nothing else. Reusing `LiveStatsAccumulator.trackRange`'s held-pitch rule here
would silently give the live key a different definition from the uploaded one,
which is the exact failure §2 of this document exists to prevent.

### Three problems the backend version does not have

**1. It needs a second implementation of the same mathematics.** The estimator
cannot run on the server without sending audio there, so the profiles, the
correlation and the gates must exist in TypeScript. That is not new ground: the
musical conversions are already implemented twice on purpose
(`audio_analysis/pitch.py` and `lib/pitch.ts`), and `docs/audio-analysis.md`
records the rule — *"the implementations are separate on purpose and the
mathematics is not"*, with the same reference cases asserted on both sides.

The same discipline applies, and it is a hard requirement rather than a nicety:

- the profile weights, `MIN_DISTINCT_PITCH_CLASSES`, `MIN_VOICED_SECONDS`,
  `MIN_KEY_CONFIDENCE` and `MIN_PITCH_CLASS_SHARE` must be the same numbers;
- a shared table of reference cases must be asserted in **both**
  `backend/tests/test_audio_key.py` and `frontend/tests/`, so the two cannot
  drift without a test failing. **`AMBIGUOUS_MODE` must be one of them**: Slice
  5's mutation run found that redefining the margin over the next *different
  tonic* survived every other fixture, because the pitch-class gate stops them
  before the correlation is reached. That fixture is the only thing pinning the
  definition of confidence, and a second implementation is exactly where it
  would be got wrong again;
- the twelve-key and transposition-invariance properties must hold in both.

**2. Two numbers of the same kind, from two pipelines.** This is the real risk.
A live "G major" followed by a backend "not measured" is a contradiction the user
can see, and it will happen: the browser gates at clarity 0.90 over a 2048-sample
window with median-of-5 smoothing, while the backend gates at 0.80 over 0.0929 s
with octave-outlier rejection. Different frames survive, so different pitch-class
profiles come out.

The repository already has the answer and it is not "make them agree":

> **Their numbers are not comparable and are never presented side by side.**
> — `docs/audio-analysis.md`, the status banner

So the live key is labelled as part of the **live recording estimate**, lives in
the practice card, and is never rendered next to the analysis result. The two
must not be reconciled, averaged, or cross-checked, and neither validates the
other. A test should assert that no component renders both.

**3. Flicker.** A label that changes every 500 ms as evidence trickles in is
worse than no label. The backend estimates once over a finished recording and has
no equivalent. See the commitment rules below.

### Algorithm

The fold is incremental — one array increment per voiced frame — and the estimate
runs on the existing 500 ms publish tick, never per frame:

```
LivePitchSample ──▶ counts[midi mod 12] += 1        (per frame, O(1))
                          │
                          ▼
              24 correlations over 12 values        (twice a second)
                          │
                          ▼
              gates ──▶ commitment ──▶ label | "Not enough yet"
```

**The profile is cumulative over the session, not a rolling window.** The
consistency figure uses the last 128 frames because "how in tune am I *now*" is a
question about now. A key is a property of a passage: four seconds of singing
rarely contains five distinct pitch classes, so a windowed key would answer "not
enough yet" almost always. Session-cumulative also matches the session range,
which is already cumulative.

The consequence is stated rather than hidden: **a session that modulates reports
the average of both keys**, and the longer the session runs the less responsive
the estimate becomes. A "reset" control, or a per-take reset on stop/start, was
left open here.

> **Answered by what was already there.** A per-take reset needed no control and
> no decision: `hooks/use-live-stats.ts` keys its accumulator on `sessionId`,
> which `record-panel.tsx` bumps on every `start`, so a new take begins with
> `EMPTY_STATS` — no key, and none of the previous take's pitch classes. A test
> asserts it with a *different* key rather than with an empty session, because
> a session that answers nothing cannot tell whether the counts were cleared.
> A mid-take reset control was **not** added: nothing in the repository asked
> for one, and a button that silently changes what a displayed measurement was
> folded from is a feature that needs a reason first.

### Commitment rules — the new problem

The gates from §6 apply unchanged, and they already do most of the work: nothing
is shown until five distinct pitch classes and a second of voiced time exist, so
the readout stays at "Not enough yet" through the noisy opening of a session
rather than cycling through wrong answers.

Two rules on top, both of which **must be chosen by measurement against recorded
practice sessions, not picked**:

- **Hysteresis.** Once a key is shown, replacing it requires the new candidate to
  lead by more than the incumbent by some margin — not merely to lead. Without
  this the label alternates between relative major and minor near the boundary.
- **Dwell.** A candidate must hold for a minimum number of consecutive ticks
  before it is displayed at all.

Neither exists in the backend and neither may be invented at implementation time
with a plausible-looking constant. The fixture in §"Fixtures" below is what sets
them.

> **What the measurement found — one rule, not two.**
>
> The dwell was set as required: the boundary fixture moves the raw per-tick
> label **14 times in 20 seconds**, and sweeping the dwell across four such
> sessions (differing in how many consecutive ticks each third holds the floor)
> gives 4 ticks as the smallest value holding all of them at or below three
> changes, costing 3.0 s before a clear melody is labelled instead of 1.5 s.
>
> **Hysteresis was measured to be unreachable, and was not implemented.** A
> margin below `MIN_KEY_CONFIDENCE` cannot fire: an answered key leads the
> *second* candidate by at least the gate, and an incumbent is never above the
> second, so the winner already leads it by at least the gate. Every margin from
> 0 to 0.05 produced identical readouts on every fixture, and the smallest lead
> an answered key held over any other candidate across 200 ticks of unstable
> material was 0.1375. The relative major/minor alternation the rule was written
> for does not reach the display at all: it scores 0.002–0.04 there and the
> confidence gate refuses it outright. A margin *above* the gate would fire, but
> only to delay changes no fixture produces.
>
> The paragraph above is left standing because it is what a reader would
> reasonably predict, and because the prediction being wrong is the finding. A
> test asserts the invariant, so if `MIN_KEY_CONFIDENCE` is ever lowered far
> enough for hysteresis to matter, the reason it was left out fails first.

### Outputs

Three fields added to `LiveStats`, published on the existing tick:

| Field | Meaning |
| --- | --- |
| `keyTonic: string \| null` | Pitch class, or `null` while the evidence gates are unmet |
| `keyMode: "major" \| "minor" \| null` | |
| `keyConfidence: number \| null` | The same margin the backend reports |
| `keyUnmeasuredReason` (as built) | A fourth field, added in Slice 8. The card has to say *what it is waiting for*, and the three refusal reasons are the vocabulary the uploaded analysis already uses for exactly that. `null` once a key is displayed |

`null` is **"Not enough yet"**, never 0% and never a blank label — the rule
`consistency` already follows.

### Storage, API, network

**None, none and none.** No endpoint, no request, no persistence, no telemetry.
The live key is computed, displayed, and discarded when the session ends. That is
what keeps the "microphone audio never leaves the page" guarantee intact, and it
means this extension adds no ownership surface, no cost and no privacy risk
whatsoever.

### Frontend contract

One block in the existing practice card (`components/record/live-stats-card.tsx`
or a sibling), beside consistency and session range.

| State | Shown |
| --- | --- |
| Gates unmet | "Not enough yet" plus what it is waiting for — the same treatment consistency uses |
| Committed | Tonic + mode, labelled as a live estimate, with the margin available but not headline |
| Changed | The new key, after hysteresis and dwell. Never a flicker |
| Not recording | Nothing. No stale key from a finished session |

Copy rules: it is **"Key you seem to be singing in"**, never "the key of the
song"; it is never placed beside the uploaded analysis's key; and it never
implies the key was right, because there is still no reference melody.

### Fixtures and validation

The browser suite is `node --test` over plain TypeScript modules, which is enough
because the accumulator is a class a test can drive with scripted samples — the
existing pattern.

1. **Parity with the backend.** A shared table of pitch-class profiles with
   expected tonic, mode and margin, asserted in both suites. This is the one that
   stops the two implementations drifting.
2. **All 24 keys, and transposition invariance**, as §10 requires of the backend.
3. **Every adversarial fixture from §10** — hum, two classes, three-class
   arpeggio, chromatic wander, uniform, random — driven through the accumulator
   as *frame sequences*, each returning `null`.
4. **Accumulation over time.** A scripted session must report `null` early and
   commit only once the gates pass; the tick at which it commits is asserted.
5. **Flicker.** A session scripted to sit on the major/minor boundary must not
   change its displayed key more than a bounded number of times. This is the
   fixture that sets hysteresis and dwell, and the constants are recorded with
   its measurements.
6. **Browser verification.** Sing into it. A held hum shows "Not enough yet"; a
   melody commits to a key; stopping clears it.

**No fixture here validates the estimator against human singing** — the same
statement §10 makes, for the same reason.

### Performance

Measured in Node, the target runtime, before any of this was planned:

| Operation | Planned (measured before) | As built |
| --- | --- | --- |
| One frame folded into the histogram | **0.00006 ms** | **0.00012 ms** |
| 24 correlations over 12 values (per tick) | **0.0081 ms** | **0.0049 ms** |
| Total, at 30 frames/s plus 2 estimates/s | **0.018 ms per second** | **0.013 ms per second** |
| Share of one 500 ms publish budget | **0.003 %** | **0.002 %** |

The right-hand column is the shipped implementation, best of seven runs of
200 000 folds and 20 000 estimates. The suite asserts ceilings roughly 80× and
200× the measurements — deliberately loose, because what a test can usefully
defend here is the *shape* of the cost, not the speed of one machine.

The cost is independent of session length, because the fold is incremental and
the correlation is always over twelve numbers. Performance is not a constraint
here and no optimisation is warranted; the render budget rule still applies —
this runs on the 500 ms tick, not per frame, and its output goes through
`LiveStats` like everything else.

### Limitations

Everything in §12 applies, plus:

- **It is a different measurement from the uploaded key** and may disagree with
  it. Neither is wrong; they are different pipelines over different frames.
- **It reports one key for the whole session**, so modulation averages.
- **It gets less responsive the longer a session runs**, by construction.
- **It is browser-local and unverifiable.** Nothing is stored, so a disagreement
  cannot be investigated after the fact.

### Slices

| # | Purpose | Files | Tests | Browser | Acceptance | Depends on |
| --- | --- | --- | --- | --- | --- | --- |
| 7 ✅ | **Delivered.** The estimator in TypeScript | `frontend/lib/live-key.ts`, `fixtures/key-parity.json`, `frontend/tests/live-key.test.ts`, `backend/tests/test_audio_key.py` | 59 in the browser suite; 26 new on the backend, which asserts the same table | — | Met. All fifteen shared verdicts identical and every margin within 1e-9; all 24 keys, transposition invariance, every adversarial fixture refused; constants copied from `key.py` and pinned on both sides. `AMBIGUOUS_MODE` is in the table, so the definition of the margin is asserted in both runtimes | Slices 1–6 |
| 8 ✅ | **Delivered.** Accumulation and commitment | `frontend/lib/live-practice.ts`, `frontend/lib/live-key.ts` (`LiveKeyTracker`) | Scripted sessions; the boundary fixture, run as a sweep rather than quoted | — | Met, with one deviation recorded above: the dwell is 4 ticks, set by the sweep; **hysteresis is not implemented**, because the measurement showed it cannot fire below the confidence gate. `null` until the gates pass, ≤3 changes on every boundary fixture, every voiced frame counted and not only held ones | 7 |
| 9 ✅ | **Delivered.** The readout | `components/record/live-stats-card.tsx`, `lib/live-practice.ts` | Presentation under `node --test`: `null` renders no tonic, half a key is not a label, a margin without a key is nothing, and a structural sweep of `components/` and `app/` | Chromium with a synthesised voice on the fake capture device: melody → C major within ~4 s, hum → "Not enough yet" throughout, stop → cleared. 390 px and 1280 px, both colour schemes, no horizontal overflow | Met. The only console errors are the three database-backed endpoints failing in a container with no PostgreSQL; the live key makes no request at all | 8 |

### What this extension does **not** add

BPM · beat tracking · melody transcription · reference-song input · vocal
separation · transposition · compatibility · any network request · any stored
field · key in AI feedback, comparison or progress. The Phase 9 dependency stays
blocked exactly as §4 records it.

## 14. Candidate B — melody note events

Recorded at enough depth to choose between the candidates. **Not fully specified;
it needs one short design pass if it is selected.**

**The gap it closes.** "If I sing A4 → B4 → C5, can the system show that
sequence?" Today: the frames exist and the graph draws them, but `notes.py`
returns a histogram **sorted by duration, not by time**, and no endpoint returns
an ordered sequence. A note-event sequence is the missing piece, and *melody* is
named in the roadmap row.

**Why 10.7 was wrong to defer it.** See Correction 2. There is no new threshold:
the analyzer's `_sustained()` rule — ≥ 5 consecutive frames within 1 semitone,
`MIN_RANGE_FRAMES` and `RANGE_CONTINUITY_SEMITONES` at `analyzer.py:104-106` — is
documented, tested, and exists to answer exactly "is this a note somebody sang?"

**Sketch.** Input: the same stored timeline. Output: an ordered
`tuple[NoteEvent, ...]` of `note_name`, `midi_note`, `start_seconds`,
`end_seconds`, `frame_count`, `average_cents`. Derived on read, like `/notes`, so
the storage, ownership, deletion and retention analysis in §7 and §11 carries over
unchanged. One endpoint, `GET …/audio-analysis/melody`.

**The one design task before it is buildable.** `_sustained()` lives in
`analyzer.py` and uses numpy; `notes.py` deliberately avoids importing numpy *"so
aggregating a timeline does not drag numpy and a decoder in behind it"*. The rule
must therefore be re-expressed in pure Python, and the constants moved to
`models.py` and imported from there — the precedent `IN_TUNE_CENTS` already sets,
with the comment saying why. Re-stating the numbers in two places is the drift
this codebase explicitly guards against.

**What it is not.** Not musical transcription, not rhythm, not note *values*
(crotchets and quavers need a tempo, which is deferred), not a melody extracted
from a song, and not a claim that any note was correct.

---

## 15. Open questions

Beyond the decision gate in §4, these need a product answer rather than an
implementation choice:

1. **Is a weak measurement worth shipping?** On unaccompanied voice, key
   estimation will often answer "not measured" — correctly. A feature whose
   honest answer is frequently "no" may still be worth having.
2. **Relative major / minor: pick one, or show both?** The specification returns
   `null` when they cannot be separated. "G major or E minor" is more informative
   and less decisive. Both are defensible; the choice is editorial.
3. ~~**How is low confidence presented?**~~ **Answered in Slice 4: shown, with
   the weakness stated in words.** §9 assumed this and the measurements support
   it. `WEAK_KEY_CONFIDENCE = 0.19` is presentational only — it is the bottom of
   the band a *weighted* melody reaches (0.205–0.262), and it puts the bare
   unweighted scale (0.146, measured at 0.147 end to end) on the weak side, which
   is what this document already required of that fixture. Withholding below a
   second bar was rejected for the reason the decision gate gives: the feature's
   value is its honest under-answering, and hiding a thin answer would replace a
   stated weakness with a silence the reader cannot interrogate.
4. **Does Phase 9 have a future at all in this product?** It needs a reference
   song's range. Nothing in this repository provides one, nothing plans to, and
   §8 of the Step 10.8 brief forbids inventing one. Until that is answered,
   Phase 9 is not merely unbuilt — it is unbuildable, and both Phase 8 candidates
   stand alone rather than leading anywhere.
