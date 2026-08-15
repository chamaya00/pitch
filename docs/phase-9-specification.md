# Phase 9 — specification

> **Status: blocked, and deliberately so.**
>
> This file is an audit and a specification for **unbuilt** work. Nothing in it
> is implemented, and no part of it should be read as a description of the
> running system. Where it and the shipped documents disagree,
> [api.md](api.md), [audio-analysis.md](audio-analysis.md),
> [architecture.md](architecture.md) and [limitations.md](limitations.md) are
> right.
>
> Phase 9 cannot start. Not for want of engineering: **the product has never
> said where a reference song comes from**, and every part of the phase —
> storage, API, algorithm, tests, cost — changes shape depending on the answer.
> The exact question is in [§3](#3-the-blocker-stated-exactly). Four answers are
> analysed in [§4](#4-the-four-input-models). **None is chosen here.** Choosing
> one would be an engineer inventing a requirement, which is the failure mode
> this file exists to avoid.

## Revision history

| Step | What happened |
| --- | --- |
| Phase 9 audit | This file. Re-audited the repository after Phase 8 closed, confirmed the roadmap's Phase 9 row against source, established that no part of it exists, named the blocking product decision, and specified everything that can be specified without answering it. Two stale claims found elsewhere in the docs and corrected in the same commit. |

---

## 1. What the audit found

Every answer below is from source, not from prose. The searches excluded
`node_modules/`, `.next/` and `.venv/`. TODO comments and documentation were not
counted as implementation.

### 1.1 Reference input — none of it exists

| Asked | In the repository | Evidence |
| --- | --- | --- |
| Reference-song upload | **No** | One upload route exists — `POST /api/v1/recordings` (`routes/recordings.py:32`). It takes one file, stores it as a `Recording`, and nothing in `Recording` distinguishes a performance from a reference |
| Reference-audio storage | **No** | `RecordingStorage` (`services/audio/storage.py`) has one directory, one naming rule and one id space. There is no second kind of stored audio |
| Song catalogue | **No** | No table, no model, no service, no route. `migrations/` holds three files: initial schema, credentials, owner activity |
| External song provider | **No** | `services/ai/` has exactly two provider roles — speech-to-text and feedback. No music-metadata client exists, and `requirements.txt` contains no such SDK |
| Reference recording selection | **No** | Comparison (`GET /recordings/compare`) takes two of the caller's *own* recordings and compares measurements. Neither side is a reference; the endpoint refuses to say which is better |
| Vocal stem / isolated vocal | **No** | No separation code, no model weights, no dependency. `stem` in this codebase is a filename stem in `services/audio/validation.py` |

Keyword sweep, whole repository: `song` appears in source **only as a test
upload filename** (`song.mp3`, `song.mp3.exe` — the format-mismatch fixtures).
`reference` never once means a reference recording: it is A4 = 440 Hz, a
loudness reference level, a database foreign key, a test's expected value, or
the phrase *"there is no reference melody"*. `compatibility`, `range_overlap`,
`reference_song`, `song_id`, `song_key`, `song_range`, `catalogue`, `catalog`,
`Spotify`, `YouTube`, `Deezer`, `MusicBrainz`, `Demucs` and `vocal separation`
return **zero** hits anywhere. `transpose` and `transposition` hit **only in the
key test suite** — `transposed()` in `tests/test_audio_key.py` rotates a
synthetic pitch-class weight map to assert that the estimator moves the tonic by
exactly that many semitones and changes nothing else. It is a fixture helper
proving rotation equivariance, not a transposition feature, and no application
module imports it. `melody` appears only in comments and copy stating that there
isn't one.

### 1.2 Reference metadata — none of it is stored

Not title, not artist, not key, not range, not melody, not BPM, not a source
URL, not an external id. The `recordings` table stores what a decoder can read
from an uploaded file — filename, format, duration, sample rate, channels,
size, bit depth — plus the domain document. There is no field anywhere that
could hold a fact about a piece of music that exists outside this system.

### 1.3 Compatibility — no code calculates any of it

No range overlap, no key compatibility, no difficulty, no suitability, no
transposition recommendation. The nearest thing in the codebase is
`services/comparison/`, which is a *different* operation: it places two of one
owner's recordings side by side, reports deltas in stated units, and marks three
of its seven metrics `neutral` because their difference has no better end. It
compares measurements to measurements. It has nothing to compare a measurement
*to*.

### 1.4 Transposition — no code calculates any of it

No target key, no semitone shift, no shifted range, no recommendation. The only
semitone arithmetic in the system is `VocalRange.semitone_span` (the distance
between the extremes of one recording) and the MIDI conversion in
`audio_analysis/pitch.py`.

### 1.5 What *is* built, and is directly usable by Phase 9

This is the other half of the audit, and it is the good news: the singer's side
is finished.

| Available | Where | Shape |
| --- | --- | --- |
| Per-frame pitch timeline | `audio_analyses.document` | `PitchPoint`: timestamp, Hz, MIDI note, note name, cents, confidence. Voiced frames only; a gap is a real gap |
| Detected vocal range | `AudioMetrics.pitch` | `VocalRange`: lowest/highest Hz, lowest/highest note, `semitone_span` |
| Note aggregation | `audio_analysis/notes.py` | `NoteSummary` per semitone: duration, share of voiced time, mean cents, in-tune ratio. **Sorted by duration — a histogram, not a sequence** |
| Musical key | `audio_analysis/key.py` | `KeyEstimate` with tonic, mode, confidence and twelve pitch-class shares; refuses rather than guesses |
| Pitch stability, loudness, spectrum | `AudioMetrics` | Measured, with `None` where the signal did not support it |
| Owner scoping | `IdentityResolver` + SQL | Every read filters by owner; no route knows how identity is established |

What is **not** available on the singer's side: an ordered note-event sequence.
`notes.py` returns a histogram. Anything in Phase 9 that needs *melody* rather
than *pitch content* needs that gap closed first, and closing it is Candidate B
of [phase-8-specification.md §13](phase-8-specification.md), still unbuilt.

---

## 2. The concepts, kept apart

Phase 9's roadmap row is five words long and collapses six distinct things.
They are separated here because most of the design questions below are really
questions about which one is meant.

| Concept | What it is | Example | State |
| --- | --- | --- | --- |
| **Pitch** | The frequency sounding at one moment, read against 12-TET with A4 = 440 Hz | `441.3 Hz → A4, +5 cents` | **Built** |
| **Pitch timeline** | The ordered sequence of voiced frames | `A4 → B4 → C5 → B4`, at 43 frames/second | **Built**, stored, served |
| **Note event** | A grouped region of the timeline that is one sung note, with a start, an end and a pitch | `C5 from 1.84 s for 0.42 s` | **Not built.** `notes.py` aggregates by note, not by time |
| **Vocal range** | The lowest and highest pitch *this recording contained* | `G2 – C5, 29 semitones` | **Built.** Not a physiological maximum, and never presented as one |
| **Musical key** | The tonal centre implied by the pitch-class distribution | `C major, confidence 0.31` | **Built**, and refuses rather than guesses |
| **Reference song** | The external musical material a recording would be compared *against* | the original of the song being sung | **Does not exist, and cannot be invented** |
| **Compatibility** | A product-level comparison between the singer's measured capability and some property of a reference song | "this sits four semitones above your top note" | **Phase 9.** Needs a reference |
| **Transposition** | Shifting a reference by *n* semitones so it sits differently against a voice | `down 3 semitones` | **Phase 9.** Needs a reference |

The last three are one dependency chain, not three features:
**no reference → no compatibility → no transposition.** Phase 9 is blocked at
the first link, and no amount of work on the second or third moves it.

---

## 3. The blocker, stated exactly

> **Phase 9 cannot be implemented until the product decides where a reference
> song comes from.**

Concretely, and each of these changes the build:

1. **Origin.** Does the user supply the reference, or does the system already
   hold it, or does a third party serve it?
2. **Upload.** If the user supplies it, do they upload *audio*, or type *facts*?
3. **Catalogue.** If the system holds it, who puts songs in, and under what
   right to hold them?
4. **Metadata only.** Is a reference allowed to be nothing but numbers a user
   typed — no audio at all?
5. **Full mix or isolated vocal.** If audio, is it the released recording (voice
   plus instruments plus harmonies) or an isolated vocal? These are different
   signal-processing problems with different reliability, and the product's own
   rule — *"never present an estimate as a measurement"* — makes the difference
   user-visible.
6. **Ownership and storage.** Does reference audio belong to the owner who
   supplied it, is it shared, and how long is it kept?
7. **Permitted processing.** May the system decode, analyse, store, and
   redistribute the reference audio it is given?

Question 7 is not a legal footnote, it is an architectural input. If reference
audio may be analysed but not stored, the design is "analyse on receipt, keep
the derived numbers, discard the bytes" — no audio table, no retention policy,
no deletion story for the file, and a re-analysis that cannot be repeated
without a re-upload. If it may be stored, all four of those come back.

**This file does not answer any of the seven.** Section 4 is the analysis that
should inform the answer.

---

## 4. The four input models

Pros, cons, architectural impact and what each leaves unresolved. Presented in
the order they are usually proposed, which is not an order of preference.

### Option A — the user uploads reference audio

The user supplies a second audio file: the song they are trying to sing.

**UX.** Familiar, and it reuses a flow the product already has. The user is
already uploading; this is a second dropzone. It is also the flow with the
highest friction — the user must *have* the file.

**Storage.** A second class of stored audio, at up to `MAX_AUDIO_SIZE_MB` each.
Either a `reference_songs` table alongside `recordings`, or a discriminator on
`recordings` — the second is cheaper and worse, because every existing
owner-scoped query, the history screen, comparison and progress would then have
to learn to exclude a kind of row they currently cannot encounter.

**Ownership.** Clean, and it inherits the model already in place: the uploader
is the owner, cascade deletion already works, `DELETE /identity` already removes
audio from disk.

**File limits.** A song is *longer* than a practice take. The current ceiling is
300 seconds, which covers most songs and not all. Raising it raises the cost of
every analysis in the system, not just this one, because the limit is global.

**Copyright.** The unavoidable one. The user uploads a commercial recording to a
server that stores it. This is a product and legal decision, not an engineering
one, and it does not go away by not being written down.

**Processing.** A released recording is a full mix. The pitch detector is
monophonic and validated on unaccompanied voice; on a mix it will track whatever
is loudest and periodic, which is frequently a bass line or a synth pad, not the
singer. Any range or key derived this way is substantially less reliable than
the same number derived from a solo take — and this product's core rule is that
a number shown is a number measured. Either the reliability difference is stated
everywhere the result appears, or vocal separation becomes a prerequisite, which
is a far larger phase than Phase 9.

**Testing.** Fixtures must be synthetic (see [§11](#11-testing-and-fixtures)),
which means the mix problem is exactly the part the test suite *cannot* validate.

**Unresolved:** the copyright question; whether the duration ceiling moves;
whether a full mix is accepted at all, or only an isolated vocal the user
already has.

### Option B — an internal song catalogue

The system holds a set of songs; the user picks one.

**UX.** The best of the four, and the only one where the user's first action is
a search box rather than a file picker.

**Ingestion.** Somebody has to put songs in, and there is no admin surface, no
admin role, no ingestion job and no operator concept anywhere in this
repository. The identity model is deliberately *not* an account system — there
is no role a "curator" could hold. This is not a small addition.

**Storage.** A catalogue is not owner-scoped, which makes it the first
non-owner-scoped data in the product. Every existing invariant is "filter by
owner"; a shared table needs a different rule written down and enforced.

**Rights.** Holding and serving commercial recordings is a licensing question
with a much higher bar than a user uploading their own file. A catalogue of
*metadata only* — title, artist, key, range, no audio — is a materially
different proposition and may be the viable form of this option.

**Search.** A new query surface, with its own pagination, ranking and abuse
profile.

**Reference analysis lifecycle.** A catalogue entry's numbers are computed once
and reused by everyone, which is cheap. It also means a change to the analyser
silently changes what every user is compared against unless the analysis is
versioned — the same problem `AnalysisSettings` already solves for recordings,
applied to a shared table.

**Unresolved:** who curates; the rights question; whether it is a catalogue of
audio or of numbers; how a shared table's correctness is guaranteed when every
other table's is guaranteed by owner scoping.

### Option C — an external music provider

A third-party API supplies the song and its metadata.

**Dependency.** The product currently works with **no credentials at all** for
audio analysis — that is a stated property, and it is what makes the whole audio
half testable and deployable without accounts. A required provider for Phase 9
removes it for this feature.

**Credentials and architecture.** `services/ai/` already establishes the pattern
a fourth provider role would follow: a protocol, a mock, a real adapter, error
translation at the boundary, configuration-driven selection, and no fallback
from real to mock. That part is cheap and well understood.

**Rate limits and availability.** A new class of runtime failure, and it lands
on the *comparison* path rather than on an optional prose path. The existing
provider failures degrade an analysis to "numbers without prose"; this one would
degrade a compatibility result to nothing.

**Licensing and data restrictions.** Most music APIs forbid storing their
metadata beyond a cache window, and none of the common ones return a vocal
range. Some return a key and a tempo — *as their own estimates*, which this
product would then be presenting as fact. That is a direct conflict with the
one rule in `CLAUDE.md`: numbers come from deterministic analysis in this
repository, and a number this system did not compute is not this system's
measurement. It could be shown as a *third-party claim*, clearly attributed —
that is a product decision.

**Unresolved:** which provider, and whether any of them return what Phase 9
actually needs; whether attributing an external estimate is acceptable
presentation; what the feature does when the provider is down.

### Option D — the user supplies metadata only

No reference audio anywhere. The user types, or picks: this song's lowest note
is X, its highest note is Y, its key is Z.

**Is it technically sufficient?** For the *range* half of Phase 9 — **yes, and
completely.** Range overlap, the gap at the top, the gap at the bottom, and the
semitone shift that closes them are arithmetic on four numbers, all of which
would be present. It needs no audio, no decoder, no separation, no provider, no
background work and no new dependency, and it is the only option whose entire
computation is unit-testable with no fixture files at all.

For the *melody* half — no. Melody match, tessitura, phrase difficulty and
"which notes will you struggle with" are all impossible without a melody, and
metadata does not carry one.

**The cost is honesty about the input.** The numbers the user typed were not
measured by this system, and the result is therefore an arithmetic consequence
of an unverified input. Everywhere the result appears it must say so — which is
the same discipline the product already applies to `is_mock` provenance and to
`voiced_ratio`, so the pattern exists.

**Where the numbers come from is not solved by choosing this option**, only
deferred to the user. Most people do not know a song's vocal range. In practice
this option tends to become Option B-with-metadata-only, with a small
curated table of songs and their published ranges — which is a different
decision, with the rights question much reduced.

**Unresolved:** whether a user-entered range is an acceptable input for a
product whose central rule is that numbers are measured; whether it ships with a
seeded table of song ranges, and if so, sourced from where.

### The one thing all four share

Whichever is chosen, the singer's side is already built and needs no work:
detected range, pitch timeline, and key are stored, owner-scoped and served
today.

---

## 5. What Phase 9 actually needs

Sorted by whether the phase can be delivered without it. This is deliberately
not "everything a compatibility feature could use".

### Required

| Input | Why | Available today |
| --- | --- | --- |
| The **user's vocal range** | It is the singer's half of every range comparison | **Yes** — `VocalRange`, stored per analysis |
| The **reference's range** (lowest and highest note) | It is the other half. Without it there is no comparison of any kind | **No.** This is the blocker |

That is the whole required list. Two ranges. Everything below is optional to a
first delivery.

### Optional — improves the answer, not needed for one

| Input | What it buys | Available today |
| --- | --- | --- |
| Reference **key** | Lets a transposition be expressed as "from D major to B major" instead of only "down 3 semitones" | **No** |
| User's **key** | Presentation only. It is *not* an input to any range operation | **Yes** — `key.py`. Note that 10.8 established nothing consumes it |
| User's **pitch timeline** | Tessitura: where the voice actually sat, rather than its two extremes. A far better difficulty signal than range | **Yes**, stored |
| Reference **pitch timeline** | The same for the song, and the only way to say "the hard part is the chorus" | **No** |

### Deferred — real features, explicitly not this phase

- **Melody match** — "did you sing the right notes". Needs an ordered note-event
  sequence on both sides; neither exists. This is the single largest thing
  people assume "compatibility" means, and it is out of scope.
- **Song sections** — verse, chorus, bridge. Needs structural segmentation,
  which is a phase of its own.
- **Reference vocal track / separation** — a research-grade dependency
  (`Demucs` or equivalent, with model weights and a GPU-shaped cost profile) for
  a product that currently decodes with `soundfile` and computes with `numpy`.
- **BPM and beat** — deferred in Phase 8 on three grounds, all still true:
  unaccompanied voice has no percussion, nothing in Phase 9 consumes a tempo,
  and no fixture here has a ground-truth tempo.

### Unknown — cannot be classified until §3 is answered

- Whether reference **audio** is an input at all, or only numbers.
- Whether a reference is analysed **once and shared**, or **per owner**.
- Whether a **full mix** is acceptable input, and if so how its lower
  reliability is stated.
- Whether the reference's range is **measured** by this system or **asserted**
  by a user or a third party — which decides whether the output is a
  measurement or an arithmetic consequence of an assertion.

---

## 6. Compatibility semantics

"Compatibility" is not one thing, and the word is doing a lot of work in a
five-word roadmap row. What it could mean, and what each would cost:

| Interpretation | Computable from | Verdict |
| --- | --- | --- |
| **Range overlap** — how much of the song's range falls inside the singer's | two ranges | The honest core of the feature. Arithmetic, exactly stated, no threshold |
| **Upper-range gap** — how far above the singer's top note the song's top note sits | two ranges | Same. This is usually the number the user actually wants |
| **Lower-range gap** — the same at the bottom | two ranges | Same |
| **Required transposition** — the shift that brings the song inside the range | two ranges | Arithmetic. See [§7](#7-transposition-semantics) |
| **Key suitability** — whether the song's key "suits" the voice | two keys | **Not a measurement.** Absent a range, a key says nothing about whether a voice can sing something. Two songs in C major can differ by an octave |
| **Melody match** — did the singer hit the song's notes | two note-event sequences | Deferred. Neither sequence exists |
| **Tessitura difficulty** — where the melody *sits*, not where it peaks | reference timeline + user timeline | Optional. Musically the best difficulty signal, and needs a reference timeline |

### On a single compatibility percentage

The roadmap says "compatibility", and the obvious implementation is one number
from 0 to 100. **That is a product decision, and it is flagged here as one**
rather than taken.

The case against, from principles this repository already enforces: a composite
score has to weight incommensurable things — how many semitones the top is out
by, against how many the bottom is out by, against how much of the middle
overlaps — and there is no measurement anywhere that sets those weights. A
number produced that way looks like a measurement and is a preference. The
codebase has refused this before, deliberately and in writing: comparison marks
three metrics `neutral` rather than summing them, progress draws no trend line,
and no type in the system has a field that could hold an overall figure.
`limitations.md` already carries the caveat that would have to accompany one:
*"a compatibility score … is not an objective statement about whether someone
can sing a song."*

There is a version with no such problem: **report the components.** Overlap in
semitones and as a percentage of the song's range; the gap at each end in
semitones; and the shift that closes them. Every one of those is arithmetic on
measured or asserted inputs, and each can be shown with its unit and its
provenance. If the product wants a headline figure on top of them, that is a
decision to record in this file before it is built — not a default.

**No AI produces any of these numbers.** They are arithmetic over stored values,
in the pattern `comparison/` and `progress/` already follow: those services take
no provider, so there is no object in their graph through which a model could
reach the result. Phase 9 should be built the same way. A model may later
*explain* a compatibility result in prose; it may never compute one.

---

## 7. Transposition semantics

### What is being transposed

Not the audio. Nothing in this product renders, resynthesises or pitch-shifts
sound, and adding that is not Phase 9.

What is transposed is the **reference's pitch framework**: its range, and
therefore the key it would be performed in. The output is a number of semitones
and its consequences — "down 3 semitones; the song would then sit E2–A4, inside
your range; in B major rather than D major".

### The computation, given the inputs

Trivial, once the inputs exist. Shifting the reference range down until its top
note sits at or below the singer's top note gives the minimum downward shift;
the same at the bottom gives the maximum. If both constraints can be satisfied
at once there is a *window* of workable shifts, and the recommendation is a
choice within it — which is itself a product decision, because "keep it as close
to the original as possible" and "centre it in the singer's range" are different
answers and neither is more correct.

If the constraints cannot both be satisfied — the song's range is *wider* than
the singer's detected range — there is no shift that fits, and the honest output
is that fact plus the size of the shortfall, not a best-effort suggestion.

### What it requires

- The reference's range. **Missing.**
- The singer's range. **Present.**
- A key on the reference, *only* to name the resulting key. The shift itself
  does not need one, and neither does the singer's key: 10.8 established that
  Phase 9 is a range operation, and that finding is unchanged by this audit.

### What it must not claim

That the transposed key is the right one to perform in. Register transitions,
breath demands, vowel placement and the arrangement's own constraints are all
outside what this system measures, and a semitone count is not musical advice.

**No transposition code is written in this phase.** This section specifies what
it would need.

---

## 8. Storage and ownership impact

Analysis only. **No migration is written, and the schema is unchanged.** The
existing shape — `owners` → `recordings` → `speech_analyses` / `audio_analyses`,
with the domain object in `JSONB` and indexed projections beside it — is the
pattern any of these would follow.

### Under Option A or B-with-audio

- **`reference_songs`** — id, owner (nullable only if the catalogue is shared),
  title, artist, source, created_at, and a `JSONB` document for the domain
  object. Indexed on `(owner_id, created_at DESC)`, the same access pattern
  `recordings` has.
- **`reference_analyses`** — the derived numbers, keyed by reference, in the
  shape `audio_analyses` already uses: status, error_code, created_at, document.
  Separate from the song for the same reason analyses are separate from
  recordings — a re-analysis is a new row, not an overwrite.
- **Stored audio** — a second directory under `storage_root`, or a second
  id space in the same one. `RecordingStorage`'s rules (server-generated names,
  containment check, atomic write, restrictive mode) apply unchanged and should
  be reused rather than reimplemented.
- **Deletion** — reference rows cascade from `owners` like everything else, and
  `OwnerDeletionService` must delete reference *files* too. It currently reads
  recording ids, removes those files, then removes the owner row; a reference
  file left behind would make `DELETE /identity` a false statement, which is
  exactly the failure that service was written to prevent.
- **Retention** — `IDENTITY_RETENTION_DAYS` reclaims only identities that own
  **no recordings**. An identity that owns a reference song but no recordings
  would today be reclaimable, which is wrong. That predicate needs extending
  before any reference table exists.

### Under Option D (metadata only)

Materially smaller: one table, no audio, no analysis lifecycle, no storage
directory, no file deletion, no retention change beyond the ownership predicate.
The reference is a handful of numbers with a name attached.

### Compatibility results

Two options, and it is a real choice:

- **Derived on read** — computed from the two stored analyses on request, in the
  way `key.py` and `notes.py` are, and `comparison/` and `progress/` are. No
  table, nothing to invalidate, every past pairing answerable, and re-analysing
  either side changes the answer immediately. This is the pattern the codebase
  uses everywhere and the cost is a few floating-point operations.
- **Persisted** — a `compatibility_results` table. Needed only if the
  computation becomes expensive (it is not, under any option here) or if a
  historical record of *what the answer was on a given day* is a product
  requirement. Nobody has asked for one.

**Recommendation, on the existing pattern rather than on preference: derive on
read.** It is the only one of the two that cannot go stale.

### Ownership, under every option

- A reference a user supplied is **theirs**: same owner scoping, same 404-for-
  somebody-else's, enforced in SQL, no client-provided owner id.
- A **shared catalogue** is the exception, and is the first data in the product
  that is not owner-scoped. If Option B is chosen, that rule needs stating
  explicitly, because "filter by owner" is currently a universal invariant and a
  universal invariant with one silent exception is how leaks happen.
- A compatibility result **inherits the stricter of its two inputs**. If either
  side is owner-scoped, the result is.

---

## 9. API design draft

**Draft. Nothing here is implemented, and the request shapes are contingent on
[§3](#3-the-blocker-stated-exactly).** Paths follow the existing conventions:
`/api/v1`, owner from `X-VocalLens-Owner`, the error envelope in
[api.md](api.md), a `404` for somebody else's id identical to a `404` for one
that never existed.

### Creating a reference — **shape unresolved**

The method and path are stable across the options; the body is not.

| Option | Method + path | Request |
| --- | --- | --- |
| A | `POST /api/v1/references` | `multipart/form-data`, one `file` field plus title/artist. Same validation pipeline as upload |
| B | — | No creation endpoint for users. A catalogue is read-only to them; ingestion is an operator surface that does not exist |
| C | `POST /api/v1/references` | JSON: a provider id. The server fetches the metadata |
| D | `POST /api/v1/references` | JSON: `{title, artist, lowest_note, highest_note, key?}` — all asserted, none measured |

`201` with the created reference. Errors reuse the upload codes under Option A
(`UNSUPPORTED_FORMAT`, `FORMAT_MISMATCH`, `FILE_TOO_LARGE`, `AUDIO_TOO_LONG`,
`CORRUPTED_AUDIO`) and are plain `VALIDATION_ERROR` under D.

**Idempotency:** unresolved. Under A, re-uploading the same bytes could return
the existing reference (content hash) or create a second one. The recording
upload creates a new row every time; consistency argues for the same, cost
argues against.

### Reading references

- `GET /api/v1/references` — the caller's own, newest first, `limit` bounded the
  way recording history is. Under Option B this is a **search** endpoint over a
  shared table, with a query parameter and a different abuse profile.
- `GET /api/v1/references/{id}` — one, if it is theirs.
- `DELETE /api/v1/references/{id}` — theirs to remove, including its stored
  audio. Never rate-limited, for the reason deletion is never rate-limited
  today.

### Analysing a reference — **only under A or B-with-audio**

`POST /api/v1/references/{id}/analysis` → `202`, then `GET` the same path to
poll. This mirrors `POST /recordings/{id}/audio-analysis` exactly, including
that a *failed* analysis is a `200` carrying `status: "failed"` and an
`error_code`, never an HTTP error.

**Asynchronous?** Yes, and for the same reason the existing audio analysis is:
decoding and pitch-detecting a multi-minute file is not something to do inside a
request. It reuses FastAPI `BackgroundTasks` and the partial-unique-index
constraint that already makes "one analysis in flight" true across workers.
Under Option D there is nothing to analyse and this endpoint does not exist.

### Compatibility

```
GET /api/v1/recordings/{recording_id}/compatibility?reference_id={id}
```

Recording-scoped, so ownership is checked on a path segment the way every other
recording route does it, and the reference id is checked in the same query.

**200 OK** — shape drafted, contents contingent:

```jsonc
{
  "comparable": true,
  "recording": { "lowest_note": "…", "highest_note": "…", "semitone_span": 0 },
  "reference": { "lowest_note": "…", "highest_note": "…", "semitone_span": 0,
                 "source": "measured | asserted" },
  "overlap": { "semitones": 0, "percent_of_reference_range": 0.0 },
  "gap": { "above_top_note_semitones": 0, "below_bottom_note_semitones": 0 },
  "transpose": { "possible": true, "semitones": 0, "window": [0, 0] },
  "caveats": ["…"]
}
```

**A refusal is a `200`**, following comparison: a recording with no completed
analysis, an analysis with no reliable pitch, or a reference with no range comes
back `comparable: false` with a per-side status saying which. `404` is reserved
for an id that is not the caller's.

`reference.source` is not decoration. It is the difference between a number this
system measured and a number somebody typed, and under Options C and D it is
always `asserted`.

**Unresolved in this draft:** whether a headline score field exists at all
([§6](#6-compatibility-semantics)); whether `transpose` is part of this response
or a separate endpoint; what `window` means when both constraints cannot be met;
whether compatibility is keyed on a recording or on an *analysis*.

### What is deliberately absent from this draft

No endpoint that returns a melody, a section list, a separated vocal, a tempo, or
a rendered transposed audio file. None of those exist and none is in scope.

---

## 10. Frontend design draft

**Draft. Nothing here is implemented.** The minimum, and no more than the
minimum.

### The flow

```
recording analysed  →  choose a reference  →  (reference processed)  →  result
```

The reference step is only reachable **after** a recording has a completed audio
analysis with a detected range. Offering it earlier offers a comparison the
system cannot make.

### Where it lives

Below the existing audio-analysis results, as one card — the same placement the
musical-key card took relative to the note breakdown, and for the same reason:
it is derived from a measurement already on the screen.

**Not a dashboard.** The comparison being conceptually complex is not a reason
to build a new surface; it is a reason to show fewer numbers, better labelled.

### States, all of which must exist before it ships

- **No reference chosen** — the empty state, explaining what a reference is and
  what will be compared. Under Option A this is a dropzone; under B a search
  field; under D a short form.
- **Reference processing** — only under A or B-with-audio. Polls the analysis
  endpoint, with the stage in a single polite live region, the way
  `AnalysisPanel` already does it.
- **Result** — the components of [§6](#6-compatibility-semantics), each with its
  unit, plus the transposition. Numbers this system measured and numbers the
  user asserted must be visually distinguishable; a mixed row of both, styled
  identically, is exactly the presentation this product's rules forbid.
- **Insufficient data** — the recording has no reliable pitch, or the reference
  has no range. Says which side and why. This is a `200` from the API, so it is
  a normal state, not an error state.
- **Not comparable** — the song's range is wider than the detected range, so no
  shift fits. States the shortfall.
- **Error** — reference upload rejected, analysis failed, or the request failed.
  Handled inline by the panel that made the call, like every other API failure
  here; it must not reach the route error boundary.

### Caveats, on screen and not only in the docs

Three, minimum, and they are not fine print:

- The detected range is **what this recording contained** — not the singer's
  maximum. Every existing presentation of range already says this.
- Range overlap is not a statement about whether someone can sing a song.
  Tessitura, breath, register transitions and technique are not measured.
- Under Options C and D, the reference's numbers were **not measured by this
  system**. Under A with a full mix, they were measured under conditions the
  detector is not validated for.

---

## 11. Testing and fixtures

### There is no real-world validation dataset

**A real-world annotated dataset is not present in this repository**, and Phase 9
must not claim one. Every existing audio fixture is synthesised in
`tests/fixtures.py`. No copyrighted song will be downloaded, and no synthetic
fixture validates behaviour on a real commercial recording — it validates the
arithmetic and the boundaries, which is a smaller and honest claim.

### Deterministic fixtures, all synthetic

The range and transposition arithmetic is pure and needs no audio at all:

| Fixture | Asserts |
| --- | --- |
| Reference range strictly inside the singer's | Full overlap, both gaps zero, shift of 0 |
| Reference range strictly outside, above | Zero overlap, top gap exact, downward shift exact |
| Identical ranges | 100% overlap, no shift, and no off-by-one at either end |
| Reference exactly one semitone above at the top | The boundary. That it reports 1, not 0 and not 2 |
| Reference exactly at the boundary, inclusive | Whether "reaches your top note" counts as fitting — a stated decision, pinned by a test |
| Reference *wider* than the singer's range | `possible: false`, with the shortfall, and **no best-effort suggestion** |
| Shift window with more than one workable value | The documented choice rule, whichever it is |
| No reference | Refusal, `200`, per-side status |
| Recording with no completed analysis | Refusal, per-side status, not a crash |
| Recording analysed but with no reliable pitch | Refusal naming that side |

Under an option with reference *audio*, additionally:

| Fixture | Asserts |
| --- | --- |
| Synthetic melody of known range and key | The analysis recovers both, at the tolerance the existing pitch tests use |
| Noisy reference | Refusal rather than a confident wrong range |
| Instrumental / no voice | Refusal |
| Polyphonic mix (voice plus a synthesised accompaniment) | **The known failure.** The value of this fixture is to *document* the unreliability, not to prove it works |

### What the suite cannot establish

That a compatibility result is correct for a real song sung by a real person.
There is no ground truth in this repository for that, and no test written here
should be described as if there were.

### Method

Mirrors Phase 8: pure domain first, tested without a database; then the service
against the repository contract; then the API; then a mutation run over the
arithmetic, because boundary conditions are exactly what mutation testing
catches and this feature is almost entirely boundary conditions.

---

## 12. Performance and infrastructure

Estimated from the architecture that exists, not from a wish list.

**Maximum reference duration.** Today's global ceiling is 300 seconds
(`MAX_AUDIO_DURATION_SECONDS`). Songs are typically 180–300, so most fit and
some do not. Raising it is a global change affecting every analysis; the
alternative is a separate ceiling for references, which is a new setting and a
second number to keep true.

**Processing cost.** Known, from the existing analyser: decode plus pitch
detection at a 0.0929 s frame and a 0.0232 s hop, ≈43 frames per second, so a
300-second reference is ~12 900 frames — the same order as the ceiling already
measured for the key fold (12 931 points, 1.35 ms). The expensive part is
decode and detection, not the arithmetic, and it is the cost the product already
pays per recording. **A reference is one more analysis of the size already
supported.**

**Compatibility itself is free.** Range arithmetic on four numbers. It does not
need a background job, a queue, a cache or a persisted result.

**Background work.** Needed only for reference *analysis*, and only under an
audio-bearing option — and the existing mechanism (FastAPI `BackgroundTasks`
plus a partial unique index for "one in flight") already covers it. Under
Option D no background work exists at all.

**Storage.** Under Option A, one more audio file per reference, up to
`MAX_AUDIO_SIZE_MB`. If users reference the same popular songs repeatedly, that
is duplicated bytes — an argument for content-addressed storage or for Option B,
not an argument for new infrastructure.

**Is PostgreSQL sufficient?** Yes, under every option. The tables are small, the
access patterns are the ones already indexed, and JSONB-plus-projections is the
established shape.

**Is the filesystem sufficient?** Yes, under every option. `RecordingStorage`
already handles atomic writes, containment, server-generated names and
restrictive modes.

**Therefore: no Redis, no Celery, no Kubernetes, no external provider, no GPU.**
Nothing in this specification demonstrates a need for any of them. If a future
scope adds vocal separation, that changes — separation is the one thing here
with a genuinely different cost profile — and it is explicitly out of scope.

---

## 13. Security and ownership

Everything Phase 9 adds inherits the model already enforced, and nothing in it
justifies an exception.

- **Owner-scoped reference data.** A reference belongs to the owner who supplied
  it. Somebody else's id answers `404`, identical to one that never existed.
  Enforced in the SQL, not in the route.
- **Owner-scoped compatibility results.** A result inherits the stricter of its
  two inputs. Both ids are checked in the same owner-filtered query — never one
  checked and the other trusted.
- **`IdentityResolver` unchanged.** Phase 9 adds no new way to establish who
  somebody is, and no route learns how identity works.
- **No client-provided owner id.** No request body or query parameter in
  [§9](#9-api-design-draft) names an owner, and none may be added.
- **Parameterised SQL**, without exception.
- **Secure file handling.** Under an audio option, references reuse the existing
  pipeline: content sniffing rather than trusting the extension, server-
  generated names, the containment check, bounded chunked reads, atomic writes,
  and a decoder that never sees a client-supplied path.
- **Credential secrecy.** Under Option C, the provider key is environment-only,
  absent from `public_config()`, absent from logs, and the provider's own error
  text never reaches a client — the boundary `services/ai/errors.py` already
  establishes.

**Who can see a reference song:** its owner only, under A, C and D. Under B a
catalogue is shared by design, which is the one place this model would gain an
exception, and it must be written down rather than assumed.

**Who can delete it:** its owner. `DELETE /identity` must remove reference rows
*and* reference files, or it becomes a false statement — see
[§8](#8-storage-and-ownership-impact).

**Does reference audio belong to the owner:** as data, yes. As *copyright*, no —
which is [§3](#3-the-blocker-stated-exactly) question 7 and not an engineering
question.

**One new rate-limit surface.** Reference creation is costly under Option A
(a decode) and under Option C (a paid third-party call), so both belong on the
existing per-owner costly-request limit. Reading a compatibility result is a
read and should not be limited, consistent with every other read here.

---

## 14. Limitations to ship with it

Written now so they are not written later under pressure:

- Range overlap is **not** a statement about whether someone can sing a song.
  Tessitura, breath demands, register transitions and technique are not measured
  by this system and are not claimed.
- The singer's range is **what one recording contained** — bounded by what was
  performed, by the microphone and by the room. A comparison against it inherits
  every one of those bounds.
- A transposition figure is **arithmetic, not musical advice.** It says a shift
  exists; it does not say the result is singable or that it should be performed
  that way.
- Under Options C and D the reference's numbers were **not measured here**, and
  the result is an arithmetic consequence of an unverified input.
- Under Option A with a full mix, the reference's numbers were measured under
  conditions the pitch detector is **not validated for**, and are substantially
  less reliable than the same numbers from an unaccompanied voice.
- **No fixture validates this against real songs.** All are synthetic.
- Nothing here says anything about vocal health, ability or potential, and no
  wording in the feature may imply otherwise.

---

## 15. Definition of done — preliminary

Preliminary because items 1 and 2 are contingent on a decision nobody has taken.
It cannot be finalised until [§3](#3-the-blocker-stated-exactly) is answered.

1. **The input model is decided and recorded in this file**, with the reasoning
   and the date — the way Phase 8's decision gate was recorded.
2. A reference can be supplied through the chosen model, owner-scoped, and the
   whole existing product still works unchanged.
3. A recording with a detected range and a reference with a range produce, over
   the real stack: overlap in semitones and as a share of the reference's range,
   the gap at each end, and the shift that closes it — every one of them
   arithmetic over stored values, and every one reachable back to
   `services/` code.
4. Every refusal path returns a `200` with a per-side status: no analysis, no
   reliable pitch, no reference range, range too wide to fit. None of them is an
   HTTP error, and none of them crashes.
5. `reference.source` distinguishes measured from asserted, and the UI shows the
   distinction rather than only carrying it in the payload.
6. **No model computes any number in the feature.** The service takes no
   provider, so there is no object in its graph through which one could.
7. Boundary fixtures pass: identical ranges, one semitone out at each end,
   reference wider than the singer's range, and every refusal path — plus a
   mutation run over the arithmetic.
8. Ownership: `DELETE /identity` removes reference rows and reference files;
   the retention predicate accounts for owners who hold references; a second
   owner's ids answer `404` on every new path.
9. Documentation: `api.md` gains the real contracts, `limitations.md` gains
   [§14](#14-limitations-to-ship-with-it), `architecture.md` gains the
   feature-allocation rows, and this file is marked superseded rather than
   deleted.
10. `./scripts/check.sh` passes, PostgreSQL suites included.

---

## 16. Unresolved product decisions

These block Phase 9. They are questions, and they are deliberately not answered
here.

1. **Where does a reference song come from?** Option A, B, C or D of
   [§4](#4-the-four-input-models). *This is the blocker. Every question below
   is downstream of it.*
2. If audio: **full mix or isolated vocal only**, and if a full mix is accepted,
   how is its lower reliability made visible?
3. **May reference audio be stored**, or only analysed and discarded?
4. **Is there a headline compatibility score**, or only the components
   ([§6](#6-compatibility-semantics))? If a score, what sets its weights?
5. **Which shift is recommended** when a window of workable transpositions
   exists — closest to the original, or centred in the singer's range?
6. Does a reference whose range exactly *reaches* the singer's top note count as
   fitting? A one-semitone convention, and it needs stating before it is tested.
7. Does the duration ceiling move for references, or do songs over 300 seconds
   fall outside the product?
8. Under Option B: **who curates the catalogue**, and under what right is the
   material held?

---

## 17. Deliberately not in Phase 9

Recorded so that a later reader does not mistake absence for oversight:

- Vocal separation, stems, `Demucs` or any equivalent.
- Melody extraction, melody matching, and note-event sequences on either side.
- Song sections, structure detection, verse/chorus labelling.
- Tempo, beat tracking, rhythmic comparison.
- Audio rendering: no pitch-shifted playback, no resynthesis, no transposed
  audio file.
- A song catalogue *as a side effect* of some other option — if a catalogue is
  built, it is because it was chosen.
- Any AI-produced number. A model may explain a compatibility result in prose
  once one exists; it may never compute one.
- Any "can you sing this" verdict, ranking, grade or level. No type in this
  system has a field that could hold one, and Phase 9 does not add the first.
