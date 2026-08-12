# Phase 8 — specification

> **Status: specification only. None of this is implemented.**
>
> Nothing described below exists in the repository. No table, column, endpoint,
> service, schema, component or test named here has been written. This document
> is the contract to build against; it is not a description of behaviour. Every
> statement about what the repository *currently* does was verified by reading
> the code, and is marked as evidence where it matters.
>
> Written after the Step 10.7 audit. Phase 10 remains incomplete, and this
> document does not change that.

## Why this document exists

The roadmap's entire specification of Phase 8 is one table cell:

> | 8 | Song analyser: key, BPM, melody/range estimation, limitations messaging | Planned |

That is not enough to build from, and taken literally it is not buildable at
all. Four of its five nouns turn out, on inspection, to describe an input this
product does not have, work that is already delivered, or work the roadmap
itself assigns to Phase 9. This document says which is which, on evidence, and
specifies the part that remains.

## The audit, first

Searched across the whole repository (excluding `node_modules`, `.next`,
`.venv`, `.git`, `__pycache__`, `package-lock.json`, `tsconfig.tsbuildinfo`)
for: `bpm`, `tempo`, `beat_track`, `beat detection`, `key_detection`, `chroma`,
`transpose`, `melody`, `song analysis`, `reference audio`, `compatibility`,
`onset`, `librosa`.

**No Phase 8 implementation exists, hidden, partial or otherwise.** Every hit
is prose, and every hit says the same thing:

| Location | What it is |
| --- | --- |
| `docs/roadmap.md:16` | The one-line scope above |
| `docs/roadmap.md:77` | "Phase 8 has not started … verified by search, not assumed" |
| `docs/architecture.md:625` | "**Phase 8 has not started** — no song analysis, key detection, BPM, melody extraction or transposition exists" |
| `docs/limitations.md:364` | "Songs and mixed audio (Phase 8+)" — pitch detection on a full mix is less reliable |
| `docs/limitations.md:371` | "Song compatibility (Phase 9+)" — range overlap is not an objective statement |
| `README.md:53` | "Not implemented yet: vocal/instrument separation, song melody extraction, song key estimation, song compatibility, transpose recommendation" |
| `requirements.txt:24`, `analyzer.py:16`, `detector.py:29` | librosa is *deliberately absent*, with reasons |
| `audio-analysis-result.tsx:113`, `limitations.md:64,93,107,140` | "there is no reference melody to compare against" |

There is no `chroma`, no `beat`, no `key_detection` and no `bpm` symbol anywhere
in `backend/`, `frontend/` or `scripts/`. `tempo` matches only the word
"temporary".

### Three findings that shape everything below

**1. There is no song.** The only input this system accepts is an uploaded
recording: `.wav` or `.mp3` (`services/audio/validation.py:31`), decided by
content and not by extension, ≤ `MAX_AUDIO_SIZE_MB` (50) and ≤
`MAX_AUDIO_DURATION_SECONDS` (300). There is no reference-track upload, no song
catalogue, no external music metadata provider, no vocal/instrument separation
and no second audio input of any kind. A "song analyser" has nothing to analyse.
What the repository actually holds is **one person's voice, usually
unaccompanied**, which is a different signal with different properties.

**2. "range estimation" is already delivered.** `VocalRange` — lowest and
highest *held* pitch, as frequency, note and semitone span — shipped in Step 7I
and is documented in `docs/audio-analysis.md`. Phase 8 must not re-implement or
"improve" it; a second range definition in the same product is the category
error the architecture table exists to prevent.

**3. "transposition" and "compatibility" are Phase 9, by the roadmap's own
table.** Row 9 reads "Song compatibility: range overlap, difficulty, transpose
suggestions". They are named here only to be excluded.

What is left of row 8 that is real, unbuilt, and buildable against the input
this repository actually has: **the musical key implied by what was sung**, and
the limitations messaging that must travel with it.

---

## A. Input source

**Phase 8 analyses no audio.**

This is the central design decision and everything downstream follows from it.
The input is the **stored pitch timeline of a completed audio analysis** — the
`pitch_points` already persisted in the `audio_analyses` JSONB document by Step
7I.

| Question | Answer |
| --- | --- |
| What is analysed | `tuple[PitchPoint, ...]` from a completed `AudioAnalysis` |
| Which component provides it | `services/audio_analysis/analyzer.py` → `postgres_repository.py` |
| Which artefact holds it | `audio_analyses.document -> 'pitch_points'` |
| Uploaded recording? | Only indirectly — it was decoded once, in Step 7I |
| Extracted vocal track? | No. No separation exists and none is proposed |
| Reference song? | No. No such input exists in this repository |
| Formats | Not applicable; nothing is decoded |
| Duration constraints | Inherited from upload: ≤ 300 s, so ≤ ~12 931 points at the default hop |

### Why not the audio

Two alternatives were considered and both are rejected on repository evidence.

**Rejected: a second decode pass.** A new analyzer that re-reads the file would
decode every recording twice, add a fourth copy of the JSON-document write
discipline that `docs/architecture.md` already calls "deliberate and temporary",
and need its own orchestrator, staleness sweep, partial unique index and
idempotency rules. All of that to measure something derivable from data already
on disk.

**Rejected (for now): spectral chroma inside the existing pass.** Folding the
magnitude spectrum `features.py:96` already computes into 12 pitch classes is
nearly free in CPU terms and is the textbook approach. It is deferred anyway,
for a reason specific to this input: spectral chroma exists to recover *harmony*
from a polyphonic mix, and there is no polyphonic mix here. On one unaccompanied
voice its extra information is the singer's own harmonics — the third harmonic
lands a fifth above the fundamental and pollutes a pitch class nobody sang.
Meanwhile it would need its own DSP path, its own thresholds and its own
validation, in a codebase whose rule is that there is **one pitch detector and
everything downstream aggregates it**. See D3 in section B for the condition
that reopens this.

Deriving from the timeline instead means Phase 8 inherits, for free, every guard
that has tests behind it: the clarity gate, the octave-outlier rejection, the
note conversion, and the `INSUFFICIENT_PITCH_SIGNAL` refusal. `notes.py` is the
existing precedent for exactly this, down to the docstring: *"It decodes
nothing, detects nothing and re-measures nothing."*

### When the input is unavailable

Three distinct states, and they must stay distinct:

| Situation | Behaviour |
| --- | --- |
| Recording unknown, or belongs to another owner | `404` `RECORDING_NOT_FOUND` — one answer for both |
| Recording exists, never analysed, or analysis pending/failed | `404` `AUDIO_ANALYSIS_NOT_FOUND` |
| Analysis completed, timeline present, evidence insufficient | `200` with `key: null` and a stated reason |

The third is **not an error**. It is the same shape of outcome as
`INSUFFICIENT_PITCH_SIGNAL`: a normal answer meaning "the signal did not support
this", which the UI renders as *not measured* rather than as a failure.

---

## B. Analysis features, classified

Each capability the task named, classified with the evidence for the
classification. Nothing is included because it is conventional, and nothing is
excluded because it is hard.

### Required

**R1 — Pitch-class profile.** Twelve numbers: the share of voiced time spent on
each pitch class (C, C♯, … B), folded from `PitchPoint.midi_note` modulo 12 and
weighted by hop duration, normalised to sum to 1.

*Evidence:* it is the only thing a key can honestly be computed from here, it is
a pure aggregation of an already-validated timeline, and it is a measurement in
its own right that a reader can check the key label against. `notes.py` computes
the same aggregation at semitone resolution and is trusted; this is that
aggregation folded to twelve.

**R2 — Key estimate.** Tonic, mode (major/minor), a measured confidence, and the
runner-up candidate. `tonic: null` whenever the evidence gates in section C are
not cleared.

*Evidence:* it is the one noun in the roadmap row that is neither already built
nor assigned to Phase 9, and it is the only Phase 8 output with a downstream
consumer — Phase 9's "transpose suggestions" needs a key to transpose from.

**R3 — Limitations messaging.** Named explicitly in the roadmap row. Not
decoration: the measurement is weak by construction (see section L) and a bare
label "G major" with no qualification would be the exact failure
`CLAUDE.md` forbids — a label derived from numbers with no validated method
behind it.

### Optional

**O1 — Rendering the pitch-class profile as a chart rather than a table.** The
profile must be *shown* either way, so a reader can see the evidence behind the
label; whether it is a bar chart or a table is presentation. Build the table
first; the chart only if it costs nothing.

That is the whole optional list, and its thinness is deliberate. Everything else
that looked optional turned out to be either required or deferrable on evidence.

### Explicitly deferred

**D1 — BPM / tempo.** Deferred, on three independent grounds:

1. *The input is wrong for it.* Unaccompanied voice has no percussion; onsets
   are soft and often absent between slurred notes. Tempo from onset strength on
   this signal is an estimate of an estimate.
2. *Nothing consumes it.* Phase 9 is range overlap, difficulty and transpose
   suggestions. None of the three needs a tempo. Key does have a consumer;
   tempo has none anywhere in the roadmap.
3. *It cannot be validated here.* No fixture in this repository has a
   ground-truth tempo, and a synthetic click track would validate the algorithm
   against an input the product never receives — which is proving the code runs,
   not proving the feature works.

**D2 — Beat tracking and downbeats.** Strictly harder than D1 and with the same
three objections plus a fourth: no consumer exists even hypothetically.

**D3 — Spectral chroma.** Deferred with a stated condition for revisiting: **the
day a polyphonic input exists in this product** — a reference track, an
accompaniment, or an uploaded song. On monophonic voice it adds harmonic
pollution and a second DSP path for no information the timeline lacks.

**D4 — Melody as note events.** A sequence of note events (onset, duration,
pitch) is a pure aggregation of the timeline and looks like an easy win. It is
deferred because building it requires a **minimum-duration threshold** to decide
what counts as an event, and `notes.py` documents refusing exactly that
threshold as *"the one place the feature could have quietly lied"*. Introducing
it in a neighbouring module would put two contradictory rules about short notes
in one codebase. If note events are wanted, that contradiction is the first
thing to resolve, and it is a product decision (Unknown 5).

**D5 — Transposition.** Phase 9, per `docs/roadmap.md:17`.

**D6 — Song compatibility.** Phase 9, per `docs/roadmap.md:17` and
`docs/limitations.md:371`.

**D7 — Vocal/instrument separation, reference-song upload, song catalogue.** No
such input exists; adding one is a phase of its own, not a step inside this one.

**D8 — Key in the AI feedback payload.** `services/ai/` is given measurements
and returns prose, and the audio prompt currently forbids timbre labels and
scores. Feeding it a key would invite musical advice ("try singing in A instead")
that nothing in this system can support. Deferred until the prompt rules for it
are written down first.

**D9 — Key as a progress series, and key in comparison.** Progress extracts
scalars by JSON path from the stored document (`services/progress/sources.py`);
a derived-on-read value is not reachable that way, so a key-over-time series
would force persistence — see section D. Comparison is defined as subtraction
over seven metrics with a documented "no better direction" discipline, and a key
change is not a subtraction and has no direction.

---

## C. Algorithm

One required analysis, so one algorithm.

### Input and output

```
tuple[PitchPoint, ...]  ─┐
hop_length, sample_rate ─┴─▶ pitch-class profile (12 floats, sum 1)
                                      │
                                      ▼
                        24 correlations (12 tonics × 2 modes)
                                      │
                                      ▼
                        KeyEstimate | tonic=None
```

No IO. No numpy required (12 values). No provider. No new dependency —
**the required dependency list for Phase 8 is empty.**

### Step 1 — the profile

For every point in the stored timeline, add one hop of duration to
`midi_note % 12`, then divide by the total. Hop-weighted rather than
frame-counted for the reason `notes.py` already gives: frames overlap, so
charging each frame its full length multiplies every duration. Since every point
carries exactly one hop, this is a count divided by a count — the hop cancels —
but it must be *written* as time so it stays correct if the hop ever varies.

Deliberately **not** weighted by amplitude or confidence. Both would introduce a
second definition of "how much of this note was sung" alongside `notes.py`'s,
for a quantity that is about which pitch classes were used rather than how
loudly.

### Step 2 — the key

Correlate the profile against 24 rotated key profiles — 12 major, 12 minor —
using the Pearson correlation, and take the highest.

**Profile set: Temperley's revised Kostka–Payne weights, as the primary
candidate.** They are derived from a music corpus rather than from probe-tone
listening experiments, which is the closer match to "which pitch classes did
this melody actually use". Krumhansl–Schmuckler's original weights are the
documented alternative, and the implementation **must run both across the
fixture set in section G and record which won and by how much**, in the manner
`docs/audio-analysis.md` records the clarity-threshold measurement. Neither is
adopted on reputation.

### Step 3 — confidence, and this is the part that matters

A raw correlation **does not separate a real key from noise**. Measured during
this specification (`/tmp` scratch, not committed), correlating against the
Krumhansl–Schmuckler profiles:

| Profile fed in | Best candidate | Best *r* | Margin over 2nd | Margin over next *tonic* |
| --- | --- | --- | --- | --- |
| Uniform — every class equal | G♯ minor | +0.000 | 0.000 | 0.000 |
| Random weights | G minor | **+0.428** | 0.040 | 0.040 |
| Chromatic wander, all 12 present | G♯ minor | +0.393 | 0.072 | 0.072 |
| One pitch class — a monotone hum | C major | **+0.684** | **0.000** | 0.248 |
| Two pitch classes — C and G | C major | **+0.831** | 0.079 | 0.308 |
| C major scale, unweighted | C major | +0.756 | 0.044 | 0.044 |
| C major melody, tonic/dominant heavy | C major | +0.966 | **0.246** | 0.246 |
| A minor melody, same seven classes | A minor | +0.919 | **0.276** | 0.276 |

Four things follow, and each one is a requirement:

1. **Random input scores +0.428 and a single held note scores +0.684.** Any
   threshold on raw correlation reports a confident key for a hum. The reported
   confidence is therefore a **margin**, never a raw correlation.
2. **The margin must be over the next-best candidate of any kind**, not over the
   next-best *different tonic*. The monotone hum is the proof: its margin over a
   different tonic is 0.248 — indistinguishable from a real melody — while its
   margin over the next candidate is exactly 0.000, because C major and C minor
   fit one note equally well. The stricter definition catches it; the looser one
   does not.
3. **A margin alone is still not enough.** Two pitch classes score a margin of
   0.079, above chromatic noise. Two independent **evidence gates** are
   therefore required before any key is reported at all:
   - a minimum number of **distinct pitch classes** present above a small share
     of voiced time, and
   - a minimum total **voiced duration**.

   Both thresholds must be chosen by sweeping the section G fixtures and
   recorded with their measurements, exactly as the 0.80 clarity threshold was.
   **Do not hard-code a guessed number.**
4. **A bare, unweighted C major scale is genuinely ambiguous** — margin 0.044,
   barely above random's 0.040 — because it shares all seven pitch classes with
   A minor and emphasises neither tonic. Returning `null` there is *correct*, not
   a miss. This is Unknown 3.

### Quality criteria

Measurable, and all achievable with fixtures that exist or can be synthesised:

| Criterion | Target |
| --- | --- |
| Tonic and mode on tonic/dominant-weighted synthetic melodies in all 12 major and 12 minor keys | 24/24 exact |
| Transposition invariance — the same melody shifted *n* semitones | tonic shifts by exactly *n*, mode and confidence unchanged within 1e-9 |
| False positives on noise, hum, two-class and chromatic fixtures | 0 — every one returns `tonic: null` |
| Determinism | byte-identical output for the same timeline across runs, including tie-breaks |
| Wall clock, longest allowed recording | < 5 ms (see section H) |

### Known failure cases, to be documented in the shipped docs

- **No harmony is visible.** This is a melodic key estimate. A melody accompanied
  by chords that imply a different key will be read as the melody's key.
- **Modulation.** One estimate per recording. A recording that changes key
  produces an average of both, which may be neither, and the margin will usually
  fall below the gate — but not always.
- **Relative major/minor.** They share seven pitch classes and are separated only
  by which degrees are emphasised. Melodies that emphasise neither are ambiguous
  by construction.
- **Modal melodies.** Dorian, Mixolydian and the rest are forced into the nearest
  major or minor, because the profile set contains only those two.
- **Non-12-TET intonation.** Pitch classes come from `nearest_midi`, which
  assumes equal temperament — the same caveat `docs/limitations.md` already
  records for pitch accuracy.
- **Very short or very sparse recordings.** Handled by the evidence gates, and
  the reason they exist.
- **Speech.** Most speech fails upstream with `INSUFFICIENT_PITCH_SIGNAL` and
  never reaches this code. A monotone hum does reach it, and gate 3 is what stops
  it producing "C major".

---

## D. Storage

**Nothing new is persisted. No migration. No table. No column. No index.**

The key is **derived on read** from the stored pitch timeline, which is the
existing precedent for `notes` — see `AudioAnalysisService.notes`: *"Derived
from the stored pitch timeline on read rather than persisted alongside it: it is
a pure function of points that are already on disk, and storing it too would be
a second copy to keep consistent."*

That precedent buys four things here:

| Property | Consequence |
| --- | --- |
| No migration | Nothing to review, checksum, or roll back |
| No new ownership surface | Nothing new can leak, because nothing new is stored |
| **Every existing completed analysis gains a key immediately** | No backfill, no re-analysis endpoint, no `?refresh`, no version field |
| `null` is unambiguous | It always means "measured, insufficient evidence" — never "analysed before this existed" |

The fourth is worth stating plainly, because the alternative was a real trap. Had
the key been computed inside `SignalAudioAnalyzer` and stored in the document,
`POST /audio-analysis` returns an already-completed analysis unchanged
(`orchestration/audio_analysis.py:337`), so **no existing recording could ever
have gained a key** — and `key: null` would have meant two different things with
no way to tell them apart, in a codebase whose stated discipline is that `null`
is a gap and never a zero.

### Entities and ownership, unchanged

```
owners ──1:n──▶ recordings ──1:n──▶ audio_analyses
   │                                      └─ document.pitch_points  ← read here
   └── ON DELETE CASCADE, both levels
```

Ownership is enforced where it already is: in SQL, in the `WHERE` clause of the
recording repository, reached through `AudioAnalysisService.current()` →
`_require_owned()`. Phase 8 adds no query, so it cannot weaken one.

Results are neither immutable nor replaceable: they are **not stored**.
Recomputation is not "allowed", it is the only mode — every read recomputes from
the same stored timeline and therefore returns the same answer.

### The one condition that would force persistence

If a key ever becomes a **progress series**, this decision must be revisited.
`services/progress/sources.py` extracts scalars by JSON path from the document
and cannot call a Python function; a key-over-time chart would require the value
in the document, a migration for the projection, and the version field the
current design avoids. That is deferred (D9) and this is the trigger to reopen it.

---

## E. API contract

### Existing endpoints, unchanged

Every one of these keeps its current request, response, statuses and ownership
behaviour. Phase 8 modifies none of them:

`GET|DELETE /api/v1/identity` · `POST /api/v1/identity/credentials` ·
`DELETE /api/v1/identity/credentials/{id}` · `POST /api/v1/recordings` ·
`GET /api/v1/recordings` ·
`GET /api/v1/recordings/{id}` · `GET /api/v1/recordings/progress` ·
`GET /api/v1/recordings/compare` · `POST|GET /api/v1/recordings/{id}/analysis` ·
`POST|GET /api/v1/recordings/{id}/audio-analysis` ·
`GET /api/v1/recordings/{id}/audio-analysis/pitch` ·
`GET /api/v1/recordings/{id}/audio-analysis/notes` ·
`POST|GET /api/v1/recordings/{id}/audio-analysis/feedback` ·
`GET /api/v1/config` · `GET /health` · `GET /api/v1/health`

### Proposed — one new endpoint

#### `GET /api/v1/recordings/{recording_id}/audio-analysis/key` — Phase 8

**Method** `GET`. **Request** — no body, no query parameters. Identity is
carried by `X-VocalLens-Owner` exactly as everywhere else.

**200 OK**, key measured:

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

**200 OK**, evidence insufficient — the shape is identical and `key` is `null`:

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

`unmeasured_reason` is one of `TOO_FEW_PITCH_CLASSES`, `TOO_LITTLE_VOICED_TIME`,
`AMBIGUOUS`. It is a **reason, not an error code**: it never appears in the error
envelope, is never an HTTP status, and is not added to `ErrorCode`. The
measurements alongside it are always present, so a reader can see *why* the
answer is "not measured" rather than being told to trust it.

**Status codes and errors**

| Situation | Status | Code |
| --- | --- | --- |
| Measured, or measured and inconclusive | `200` | — |
| Recording unknown, or another owner's | `404` | `RECORDING_NOT_FOUND` |
| No completed audio analysis (never run, pending, or failed) | `404` | `AUDIO_ANALYSIS_NOT_FOUND` |

**No new error code is introduced.** Both 404s already exist and already mean
exactly this.

**Ownership.** Identical to `/notes`: the owner is in the SQL `WHERE` clause, so
another owner's recording is never selected rather than selected and filtered.
Somebody else's recording answers `404`, indistinguishable from one that does not
exist. No `owner_id`, credential, hash or internal identifier appears in the
response.

**Idempotency and retry.** Safe, idempotent, side-effect free, cacheable in
principle. Repeating it returns byte-identical output. It writes nothing, so a
retry cannot double anything.

**Synchronous.** No background task, no status field, no polling. Section H is
the justification.

**Rate limiting.** It is a read, so it is **not** charged against the costly-
request limit — consistent with `/notes`, `/pitch`, comparison and progress. It
does mint an identity if the key is absent, like every owner-scoped route, and
so passes through the existing new-identity guard unchanged.

### Rejected alternative

*Adding `key` to the `GET …/audio-analysis` summary.* It would save the frontend
one request. It is rejected because `summary` mirrors the stored `AudioMetrics`
document, and mixing a derived-on-read value into it blurs the stored/derived
boundary that `/notes` was given its own path to keep clean. A reader of the
summary should be able to assume everything in it was written by the analyzer.

---

## F. Frontend contract

No new page and no new route. One new card inside the existing audio-analysis
result section (`components/audio-analysis/`), one new call in `lib/api.ts`, one
new type in `types/api.ts` mirroring the schema by hand as the existing ones do.

Six states, all required, all reachable in a browser:

| State | Trigger | What is shown |
| --- | --- | --- |
| **Loading** | request in flight | The section's existing loading treatment; no skeleton key label, ever |
| **Measured** | `key` present | Tonic + mode, the confidence as a measurement with its definition attached, the runner-up, and the pitch-class evidence |
| **Low confidence** | `key` present, confidence in the lowest reported band | The same card, with the weakness stated in words — **never a hidden or silently rounded-up number** |
| **Not measured** | `key: null` | "Not measured", the `unmeasured_reason` in plain language, and the pitch-class table anyway |
| **Unavailable** | `404 AUDIO_ANALYSIS_NOT_FOUND` | "This recording's audio has not been analysed yet" — the same treatment `/notes` already uses |
| **Error** | any other failure | Handled inline by the panel via `lib/analysis-errors.ts`, exactly as today. It must **not** reach `app/error.tsx` — a handled API failure arriving at a boundary is a bug in the panel (Step 10.4) |

Empty state: a completed analysis with an empty timeline cannot occur — the
analyzer raises `INSUFFICIENT_PITCH_SIGNAL` instead — so the endpoint answers
`404` and the "unavailable" row covers it. No separate empty state is specified,
and none must be invented.

Copy rules, non-negotiable and testable:

- The label is **"Estimated key of what was sung in this recording"**, never "the
  key of the song" — there is no song.
- The confidence is presented with its definition ("margin over the next-best
  candidate"), never as a percentage of correctness and never as a grade.
- Colour is never the only cue for confidence, matching the pitch meter's rule.
- No transposition suggestion, no "you should sing in…", no difficulty claim.
  Those are Phase 9 and they are not to be prototyped in copy.

---

## G. Fixtures and validation

This mirrors how Phase 2's detector was validated: **synthetic fixtures whose
ground truth is true by construction**, plus adversarial fixtures whose correct
answer is "no".

### There is no real-world reference dataset

Stated explicitly, as required: **this repository contains no annotated
real-world music, no key-labelled corpus, and no recording of a human being
singing anything.** Every audio fixture in `backend/tests/` is synthesised —
`harmonic_samples`, `noise_samples`, `silence_samples` in `tests/fixtures.py`.
Nothing here validates the estimator against real singing, and no test may claim
to. Acquiring a labelled corpus is out of scope for Phase 8 and would be its own
piece of work with its own licensing questions.

What synthetic fixtures *can* prove: that the algorithm implements the algorithm,
that it is transposition-invariant, that it is deterministic, and — most
valuably — **that it refuses to answer when it should**.

### Deterministic fixtures (built in Python, no audio)

These operate on constructed `PitchPoint` tuples, so they run in microseconds and
test the aggregation and the correlation directly.

| Fixture | Ground truth | Assertion |
| --- | --- | --- |
| Tonic/dominant-weighted major melody, all 12 tonics | that tonic, major | exact tonic and mode, 12/12 |
| Tonic/dominant-weighted minor melody, all 12 tonics | that tonic, minor | exact tonic and mode, 12/12 |
| Any of the above shifted by *n* semitones, *n* = 1…11 | tonic + *n* mod 12 | tonic shifts exactly; confidence identical to 1e-9 |
| Profile summing to 1 | — | the twelve shares sum to 100% within `PERCENTAGE_TOLERANCE` |
| Same timeline, two runs | — | identical output including tie-break order |

### Adversarial fixtures — the ones that matter

Every one of these must return `tonic: null`. They are the false-positive suite,
and they are drawn from the measurements in section C rather than imagined:

| Fixture | Measured behaviour without gates | Required answer |
| --- | --- | --- |
| One pitch class held throughout (a hum) | reports **C major at r = 0.684** | `null`, `TOO_FEW_PITCH_CLASSES` |
| Two pitch classes only | reports **C major at r = 0.831** | `null`, `TOO_FEW_PITCH_CLASSES` |
| Random weights across all 12 | reports **G minor at r = 0.428** | `null`, `AMBIGUOUS` |
| Chromatic wander, all 12 near-equal | reports G♯ minor at r = 0.393 | `null`, `AMBIGUOUS` |
| Uniform profile | ties at r = 0.000 | `null`, `AMBIGUOUS` |
| Very short timeline (a handful of points) | — | `null`, `TOO_LITTLE_VOICED_TIME` |
| Empty timeline | — | unreachable via the endpoint; the pure function returns `null` |

**False negatives** are covered by the symmetric requirement: every fixture in
the first table must *not* return `null`. A gate tuned until nothing passes is a
gate that has broken the feature, and the two tables together are what catch that.

### Audio fixtures (end to end, through the real analyzer)

A small number, because they are slow and the deterministic set already covers
the arithmetic. Written as real WAVs with `write_signal_wav`, decoded by the real
decoder, analysed by the real analyzer, then keyed:

| Fixture | Assertion |
| --- | --- |
| A synthesised C-major arpeggio (C-E-G-C, tonic emphasised) | resolves to C major end to end |
| The same arpeggio transposed to F♯ | resolves to F♯ major |
| White noise | analysis fails upstream with `INSUFFICIENT_PITCH_SIGNAL`; the endpoint answers `404` and never reaches the estimator |

### Proving it works rather than returning plausible values

Three properties, none of which a plausible-but-wrong implementation can fake:

1. **Transposition invariance.** A constant-output stub, a stub that always says
   C major, and an implementation with a rotation bug all fail it.
2. **The adversarial suite.** The measurements above show a naive implementation
   scores 0.43–0.83 on meaningless input. A test suite without these fixtures
   would pass against an estimator that is wrong on every real recording.
3. **Mutation.** In the manner of Steps 10.2–10.6, each of the following must
   make at least one named test fail, and the script must be run and its output
   recorded: reverse the major/minor profiles; drop the distinct-pitch-class
   gate; drop the voiced-time gate; change the margin from *next-best candidate*
   to *next-best different tonic* (the hum fixture is what catches this one);
   remove the hop weighting; remove the tie-break; return the second-best
   candidate instead of the best.

---

## H. Performance

**Measured, on this machine, against the existing code** (scratch script, not
committed):

| Operation | Points | Wall clock |
| --- | --- | --- |
| `summarise_notes` — the existing aggregation of the same timeline | 12 931 | **3.51 ms** |
| Prototype pitch-class fold + all 24 correlations | 12 931 | **0.87 ms** |

12 931 points is the **longest recording this product accepts** — 300 s at the
default 0.0232 s hop. The proposed work is roughly a quarter of an aggregation
the product already performs synchronously on every `/notes` request.

Requirements that follow:

| Question | Answer |
| --- | --- |
| Maximum acceptable processing time | **< 5 ms** at 12 931 points, asserted in a test with a generous ceiling so it fails on an algorithmic regression, not on machine noise |
| Memory | 12 floats plus 24 correlations. The timeline is already in memory, loaded by the existing repository read |
| Synchronous or background | **Synchronous.** A background task, a status field and a polling client for 0.87 ms of arithmetic would be infrastructure with no measurement behind it |
| Duration limits | Inherited: ≤ 300 s. No new limit |
| Caching | **None.** The result is a deterministic function of a row that is already read; a cache would be a second copy to invalidate for under a millisecond of work |
| Redis, Celery, workers, queues | **None. Not now and not as a follow-up** — there is no measurement that would justify one |

The honest caveat: the dominant cost of this endpoint is **loading the analysis
document**, not the arithmetic — the same JSONB read `/notes` already performs,
and `docs/architecture.md` records that a document grows with recording length
(200 owners × 50 two-minute recordings: 676 ms and 18 MB reading documents versus
125 ms and 14 KB extracting scalars). Phase 8 adds one such read per key request.
If that becomes the problem, the fix is the same one progress already uses, and
it is not a Phase 8 concern.

---

## I. Security and ownership

Short, because nothing new is stored and nothing new is spent.

| Question | Answer |
| --- | --- |
| New persisted objects | **None** |
| Owner relationship | Unchanged: `owners → recordings → audio_analyses`, cascade at both levels |
| How cross-owner access is prevented | The owner is in the SQL `WHERE` clause of the existing recording read, reached through `_require_owned`. Phase 8 adds no query and can therefore weaken no predicate |
| What another owner sees | `404`, identical to a recording that does not exist |
| Deletion behaviour | Unchanged. `DELETE /identity` removes files then rows; a derived value has nothing to delete and cannot survive its source |
| Retention behaviour | Unchanged. Step 10.6's predicate is "owns no recordings"; Phase 8 creates no recording and no row, so no identity's eligibility changes |
| Audio or reference files stored | **None.** Nothing is decoded, written or uploaded |
| Identity seam | Untouched. The route takes `owner_id` from the resolver dependency and passes it to a domain service, like every other route |
| SQL | No new SQL. The existing parameterised query is reused; nothing is interpolated |
| Exposed identifiers | `owner_id`, credential ids, hashes and internal state appear nowhere in the response |
| New privacy risk | **One, and it is small:** the pitch-class profile is a slightly more compact description of the singing than the note breakdown already returned at `/notes` on the same ownership terms. It reveals nothing `/notes` does not |
| New cost risk | None. No provider, no model, no billable call, no disk write |
| Logging | Event-name + `extra` as elsewhere. Log the recording id and the outcome; **never** the owner id, the client address, or a key |

---

## J. Billable and external providers

**Phase 8 requires none.**

- No new dependency in `requirements.txt` — the required scope needs 12 floats
  and a correlation.
- No external service, no API key, no credential, no new environment variable.
- No new configuration is proposed. The two gate thresholds and the profile-set
  choice are **constants with recorded measurements**, in the manner of
  `DEFAULT_CLARITY_THRESHOLD`, not settings — nothing in the product needs to
  vary them per deployment, and a setting would be a knob nobody can calibrate.
- Deepgram and Anthropic are untouched. Key is not sent to a model (D8), so
  Phase 8 cannot increase anybody's model spend.
- Failure behaviour: not applicable. There is no external call that can fail.

If a later slice ever needs a provider — a song catalogue, an audio-fingerprint
service — that is a new specification with its own credential handling, cost
controls and failure behaviour, and it is not this one.

---

## K. Definition of done

Executable without interpreting the roadmap. Every box is checkable.

**Backend**

- [ ] `services/audio_analysis/key.py` — pure functions: profile from a timeline,
      key from a profile. No IO, no numpy, no decoder, no provider import.
- [ ] Frozen pydantic models for `KeyEstimate`, `PitchClassShare` and the
      unmeasured reason, in `services/audio_analysis/models.py` beside
      `NoteSummary`.
- [ ] `AudioAnalysisService.key(recording_id, owner_id)` mirroring `.notes()`,
      returning `None` when there is no completed analysis.
- [ ] Both profile sets implemented and compared; the loser and its margin
      recorded in the docs. Thresholds chosen from the fixture sweep, each with
      its measurement written down beside it.
- [ ] `ruff check`, `ruff format --check`, `mypy app` clean.

**Database**

- [ ] **No migration.** `git diff` touches no file in `app/db/migrations/`.
- [ ] Confirmed: the endpoint issues no new SQL, and adds no query to any
      repository.

**API**

- [ ] `GET /api/v1/recordings/{id}/audio-analysis/key` as specified in section E.
- [ ] Response schema in `app/schemas/`, mirroring the domain models.
- [ ] `404 RECORDING_NOT_FOUND` for another owner's recording; `404
      AUDIO_ANALYSIS_NOT_FOUND` with no completed analysis. **No new error code.**
- [ ] `docs/api.md` updated: the endpoint moved out of "Planned" into the
      implemented section, with the null-shape documented.

**Frontend**

- [ ] Type in `types/api.ts`, call in `lib/api.ts`, card in
      `components/audio-analysis/`.
- [ ] All six states from section F implemented.
- [ ] The pitch-class evidence is shown in every state where it exists,
      including when `key` is `null`.
- [ ] Copy passes the section F rules — no "the key of the song", no
      transposition, no difficulty, no grade.
- [ ] `npm run lint`, `npm run typecheck`, `npm run build` clean.

**Tests**

- [ ] Every deterministic fixture in section G, passing.
- [ ] Every adversarial fixture returning `tonic: null` with the right reason.
- [ ] The transposition-invariance property across all 12 tonics and both modes.
- [ ] The audio end-to-end fixtures.
- [ ] The ownership test: owner B gets `404` for owner A's recording — added to
      `tests/test_ownership_api.py` beside the existing per-endpoint cases.
- [ ] The performance assertion at 12 931 points.
- [ ] The mutation script from section G run, its output recorded, and every
      listed mutation shown to fail a named test.
- [ ] **No existing test deleted, skipped, loosened or re-parametrised.**

**Browser verification**

- [ ] Measured state: upload a synthesised melody, analyse it, see the key card
      with a tonic, a mode, a confidence and the pitch-class evidence.
- [ ] Not-measured state: upload a monotone hum, see "Not measured" with a
      reason — **and confirm no key label is rendered anywhere on the page**.
- [ ] Unavailable state: open a recording with no audio analysis, see the
      unavailable treatment, not an error.
- [ ] Confirm the browser console shows no uncaught error in any of the three,
      and that `app/error.tsx` was not reached.
- [ ] Mobile width and both colour schemes.

**Ownership and security**

- [ ] Response contains no `owner_id`, credential id, hash or internal state.
- [ ] No new SQL, and nothing interpolated into any query.
- [ ] Identity resolver seam untouched; the route takes `owner_id` from the
      dependency and nothing else.
- [ ] Deletion and retention behaviour verified unchanged by their existing
      suites.

**Performance**

- [ ] < 5 ms at 12 931 points, measured and recorded.
- [ ] No Redis, no queue, no worker, no cache, no background task added.

**Documentation**

- [ ] `docs/audio-analysis.md` — a "Musical key" section: the algorithm, the
      profile set and the one that lost, both thresholds with their measurements,
      and what didn't work.
- [ ] `docs/limitations.md` — section L below, in the shipped voice.
- [ ] `docs/architecture.md` — a row in the feature-allocation table with its
      "Not" column filled in, and the "Phase 8 has not started" line corrected.
- [ ] `docs/roadmap.md` — Phase 8 status and its delivered/not-delivered list.
- [ ] `README.md` — the "Not implemented yet" line updated to remove only what
      actually shipped.
- [ ] This file marked superseded, not deleted.

---

## L. What Phase 8 will not solve

To be carried into `docs/limitations.md` when the phase lands:

- **It does not analyse songs.** There is no song in this product. It reports the
  key implied by what one person sang into one microphone.
- **It cannot hear harmony.** The estimate comes from a monophonic pitch
  timeline. A melody sung over chords in another key is read as the melody's key.
- **It is not a claim that you sang in that key correctly**, or at all well. It
  is a description of which pitch classes were used and how they were weighted.
- **It cannot distinguish relative major from relative minor** when the melody
  emphasises neither tonic. In that case it says so rather than guessing.
- **It assumes equal temperament**, like every other pitch measurement here.
  Non-Western intonation is folded into the nearest 12-TET pitch class.
- **It reports one key per recording.** Music that modulates gets an average, and
  usually gets "not measured".
- **It is not validated against real singing.** Every fixture behind it is
  synthetic. No annotated corpus exists in this repository.
- **It gives no tempo, no beat, no melody transcription, no transposition and no
  compatibility judgement.** Those are D1–D6, and three of them are Phase 9.
- **It has no opinion.** No model sees it, no advice is generated from it, and
  there is no field in the response that could hold a score.

---

## M. Implementation order

Six slices. Each is independently reviewable, each leaves the suite green, and
each states what the next one may assume.

### Slice 1 — the pure domain

*Purpose.* The profile and the estimator, as pure functions over a timeline.
Nothing else exists yet, and nothing can call them.

*Files.* `backend/app/services/audio_analysis/key.py`,
`models.py` (new frozen models), `backend/tests/test_audio_key.py`.

*Tests.* Every deterministic fixture and every adversarial fixture from section
G. Both profile sets compared; the sweep that sets both thresholds run and its
numbers recorded in the test file's docstring.

*Browser.* None — nothing is reachable.

*Acceptance.* 24/24 tonics, transposition invariance to 1e-9, every adversarial
fixture `null` with the correct reason, deterministic across runs, and the
chosen thresholds justified by recorded measurements rather than asserted.

*Depends on.* Nothing.

### Slice 2 — the service method

*Purpose.* `AudioAnalysisService.key()`, mirroring `.notes()` exactly:
ownership through `current()`, `None` when there is no completed analysis.

*Files.* `services/orchestration/audio_analysis.py`,
`tests/test_audio_analysis_orchestration.py`.

*Tests.* Returns `None` for pending, failed and absent analyses; raises
`RECORDING_NOT_FOUND` for another owner's recording; returns an estimate for a
completed one. Driven with the existing stub analyzer, so no audio is decoded.

*Browser.* None.

*Acceptance.* No new repository method, no new SQL, and the ownership tests pass
against a substituted resolver as `tests/test_resolver.py` already requires.

*Depends on.* Slice 1.

### Slice 3 — the endpoint

*Purpose.* `GET …/audio-analysis/key`, with its schema.

*Files.* `api/v1/routes/audio_analysis.py`, `app/schemas/audio_analysis.py`,
`tests/test_audio_analysis_api.py`, `tests/test_ownership_api.py`, `docs/api.md`.

*Tests.* Both `200` shapes; both `404`s; another owner gets the same `404` as a
missing recording; the response is asserted to contain no `owner_id` and no
credential field; the endpoint is asserted **not** to consume costly-request
quota.

*Browser.* `curl` through the running stack is enough at this slice.

*Acceptance.* `docs/api.md` documents the endpoint including the `key: null`
shape and `unmeasured_reason`; no `ErrorCode` member added.

*Depends on.* Slice 2.

### Slice 4 — the UI

*Purpose.* The key card, all six states.

*Files.* `frontend/types/api.ts`, `lib/api.ts`,
`components/audio-analysis/`, `frontend/tests/`.

*Tests.* The presentation logic as plain TypeScript under `node --test`, as the
existing frontend tests are — including that a `null` key never renders a tonic
anywhere in the output.

*Browser.* All three verification scenarios in section K, at desktop and mobile
width, in both colour schemes, with the console checked.

*Acceptance.* Every state reached deliberately rather than assumed; no handled
API failure reaches `app/error.tsx`.

*Depends on.* Slice 3.

### Slice 5 — performance and mutation

*Purpose.* Prove it is fast and prove the tests bite.

*Files.* `backend/tests/test_audio_key.py`, a mutation script kept outside the
repository.

*Tests.* The 12 931-point ceiling. Every mutation in section G shown to fail a
named test, with the output recorded in the step's report.

*Browser.* None.

*Acceptance.* If any mutation passes, the test that should have caught it is
strengthened and the mutation re-run before the slice is called done.

*Depends on.* Slices 1–3.

### Slice 6 — documentation

*Purpose.* Make the shipped behaviour findable and its limits unavoidable.

*Files.* `docs/audio-analysis.md`, `docs/limitations.md`,
`docs/architecture.md`, `docs/roadmap.md`, `README.md`, this file.

*Acceptance.* Section L is in `limitations.md` in the shipped voice; the
architecture feature table has a row with its "Not" column filled; the "Phase 8
has not started" claims are corrected; this file is marked superseded rather
than deleted, so the reasoning behind the deferrals survives.

*Depends on.* Slices 1–5.

---

## Unknowns that need a product decision

These are not implementation details and must not be resolved by whoever writes
the code.

1. **Is a melodic key estimate what Phase 8 was for at all?** The roadmap says
   "song analyser". There is no song, and there is no plan in this repository for
   acquiring one. If the intent was always to analyse an uploaded backing track
   or a reference recording, then Phase 8 as specified here is the wrong feature
   and the right one starts with an input this product does not accept. **This is
   the decision everything else depends on.**
2. **Is a weak measurement worth shipping?** On unaccompanied voice this will
   answer "not measured" often — correctly. A feature whose honest answer is
   frequently "no" may still be worth having, or may not.
3. **Relative major/minor: pick one, or show both?** They share seven pitch
   classes. The specification currently returns `null` when they cannot be
   separated. Showing "G major or E minor" is more informative and less decisive;
   both are defensible and the choice is editorial.
4. **How is low confidence presented?** Shown with the weakness stated, or
   withheld entirely below a second, higher bar. Section F assumes the former.
5. **Should note events (D4) be built, and if so what resolves the
   minimum-duration contradiction** with `notes.py`'s deliberate refusal of such
   a threshold?
6. **Does Phase 9 still start from here?** Its "transpose suggestions" need a key
   *and* a reference song's key. This delivers the first and not the second, so
   Phase 9 remains blocked on Unknown 1 regardless of what is built here.
