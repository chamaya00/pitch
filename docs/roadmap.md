# Roadmap

Phases are built in order. A phase is done only when implementation, UI, API,
tests, error states, lint, type checks and documentation are all in place.

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Project foundation: structure, config, health check, homepage, docs | ✅ Complete |
| 1 | Audio upload: UI, endpoint, validation, storage, metadata | ✅ Complete |
| 2 | Pitch engine: preprocessing, detection, confidence filter, Hz→MIDI→note→cents | ✅ Complete |
| 3 | Analysis dashboard: summary cards, range, accuracy, pitch timeline, note distribution | ✅ Complete |
| 4 | Microphone recording in the browser | ✅ Complete |
| 5 | Advanced metrics: RMS, peak, spectral features | ✅ Complete |
| 6 | Claude integration: structured payload, service, feedback UI | ✅ Complete |
| 7 | User history: users, recordings, analyses, comparison, progress chart | ✅ Complete |
| 8 | Melodic key estimation (scope resolved in 10.8) | ✅ Complete — domain (1), service (2), API (3), UI (4), performance and mutation (5), documentation (6). [phase-8-specification.md](phase-8-specification.md) |
| 9 | Song compatibility: range overlap, difficulty, transpose suggestions | **Blocked** — audited, specified, and waiting on one product decision: where a reference song comes from. Nothing built. [phase-9-specification.md](phase-9-specification.md) |
| 10 | Production polish: auth, security hardening, error pages, performance, deployment | Started — identity portability and deletion (7P), credentials attached to the owner (10.2), rate limiting (10.3), error boundaries (10.4), edge proxy (10.5), identity retention (10.6), Content-Security-Policy (10.9), a rate-limit counter every worker shares (10.10), reads that stop paying for the pitch timeline (10.11), one decompression per row rather than one per expression (10.12), a history page that says it is a page (10.13), the reads that do not scale and the one that stopped needing to (10.14), the fields a fold reads rather than the frames (10.15), the same fix on the read that does it twice (10.16), the reads across several workers and the connection budget they share (10.17), a hundred times the data and the last decompression in the progress query (10.18), the last read that grows with a history and the count that was answering a different question (10.19), the checks nobody ran (10.20) |

## Phase 0 — delivered

- Monorepo layout: `frontend/`, `backend/`, `docs/`, `scripts/`
- FastAPI app factory, CORS, JSON structured logging, cached env-backed settings
- `GET /health` and `GET /api/v1/health`
- Next.js 16 App Router homepage, dark-first theme tokens, typed API client,
  live API status indicator
- `.env.example`, `.gitignore`, Dockerfiles, `docker-compose.yml`
- Backend: 6 pytest tests, ruff (lint + format), mypy strict
- Frontend: eslint, `tsc --noEmit`, production build
- `scripts/check.sh` runs every check
- Documentation: architecture, API, audio analysis, AI layer, limitations

Deliberately **not** in Phase 0: any audio handling, database models or LLM
calls. Dependencies for those land with the phase that uses them.

## Phase 1 — definition of done

A user can upload a valid audio file and see its filename, duration, file size
and an audio player. Invalid files (wrong type, too large, too long, corrupted)
fail with a clear message driven by the documented error codes.

## Phase 7 — where it stands

Built through Step 7M:

- Upload, validation, storage and metadata (7A)
- Provider protocols and the mock adapters behind them (7B)
- Deepgram and Claude adapters, selected by configuration (7C–7D)
- Speech analysis: transcript, metrics, feedback, background execution (7E)
- The analysis experience in the browser (7F)
- Microphone recording, WAV encoding, explicit upload (7G–7H)
- Live pitch detection in the browser, and Live Vocal Practice (7H, 7J)
- Deterministic backend audio analysis: pitch, range, stability, loudness,
  spectrum (7I)
- Note breakdown from the stored pitch timeline (7K)
- AI interpretation of the audio measurements (7L)
- PostgreSQL as the source of truth, anonymous owners, recording history (7M)
- Recording comparison: two owned recordings side by side, with deterministic
  deltas, explicit units and measurable condition caveats (7N)
- Progress tracking: an owner's measurements over time, with per-point
  measured/unmeasured/ineligible states, an accessible chart and table, and no
  trend line (7O)

Phase 7 is complete. What it deliberately does **not** contain, in any of its
three user-facing features:

- an overall figure, a level, a grade or a ranking — no type in the system has a
  field that could hold one;
- a claim that anyone's singing improved;
- an AI judgement of comparison or progress. Both are deterministic arithmetic
  over stored measurements, and neither service takes a provider.

Four of the seven measurements have no desirable direction, and the three that
do are defined against equal temperament rather than against singing. See
[api.md](api.md) and [limitations.md](limitations.md).

**Phase 8 had not started at the end of Phase 7.** Nothing about song analysis,
key, BPM, melody extraction or transposition existed — verified by search, not
assumed, and re-verified by the Step 10.7 audit. Musical key was built afterwards,
in 10.8; song analysis, BPM, melody extraction and transposition still do not
exist.

## Phase 8 — how it was scoped (10.7)

The roadmap row above was one table cell, and Step 10.7 turned it into
[phase-8-specification.md](phase-8-specification.md) without building any of it.
The audit that produced it found that four of the row's five nouns do not
survive contact with the repository:

- **"Song analyser" has no song.** The only input this product accepts is one
  uploaded recording of one voice. There is no reference track, no catalogue, no
  vocal separation and no second audio input anywhere.
- **"range estimation" already shipped** in Step 7I. A second range definition
  would be a category error, not a feature.
- **"transposition" and "compatibility" are Phase 9**, by row 9 of the table
  above.
- **BPM and beat tracking are deferred** on three grounds: unaccompanied voice
  has no percussion, nothing in Phase 9 consumes a tempo, and no fixture in this
  repository has a ground-truth tempo to validate one against.

10.7 scoped what was left to **the musical key implied by what was sung**,
derived on read from the pitch timeline Step 7I already stores — no new table,
no migration, no dependency, no provider and no background work. That
specification is complete and is still in the file.

Its most load-bearing finding is a measurement: correlating a *random*
pitch-class profile against the standard key profiles scores **+0.428**, and a
single held note scores **+0.684 for C major**. A key estimator built the obvious
way reports a confident key for a hum. Confidence is therefore defined as a
margin over the next-best candidate of any kind, with two independent evidence
gates and the adversarial fixtures — hum, two notes, noise, chromatic wander — a
required part of the suite.

## Phase 8 — how the scope was decided (10.8)

Step 10.8 re-audited to resolve the question 10.7 left open, and **two of 10.7's
load-bearing claims did not survive re-inspection**:

- **Phase 9 does not consume a musical key.** `limitations.md:373` already said
  so: *"A compatibility score compares a detected range against an estimated song
  range."* Phase 9 is a range operation. Transposing a song to fit a singer uses
  the song's range and the singer's range; the singer's own key is not an input
  at any point. That claim was the *only* thing separating key from BPM, which
  10.7 deferred partly because nothing consumes it — so the same ground now
  applies to key.
- **Note events were deferred for a contradiction that does not exist.** 10.7
  said they need a minimum-duration threshold that `notes.py` refuses. `notes.py`
  refuses a *new, arbitrary* threshold on a share-of-time table; the analyzer
  already has a tested held-pitch rule (`MIN_RANGE_FRAMES`,
  `RANGE_CONTINUITY_SEMITONES`) that exists to answer "is this a note somebody
  sang?" Note events built on it introduce no new threshold, and are now recorded
  as the alternative candidate scope.

So Phase 8 had **two candidate scopes and no evidence to choose between them**:
musical key (fully specified, no consumer, a *label* rather than a measurement,
and unvalidatable against real singing here) or melody note events (closes the
one gap in "can it show A4 → B4 → C5?", reuses an existing rule, needs one short
design pass). Choosing was a product decision, and an engineer choosing would
have been inventing a requirement.

**The decision was taken: musical key only.** Note events, BPM, beat tracking,
melody transcription, reference-song input, vocal separation, transposition and
compatibility are all out of scope, and key is not fed to AI feedback, comparison
or progress. Phase 9 stands as a *blocked product dependency*: how a reference
song is supplied is its own decision, and nothing here invents one.

What the 10.8 audit established about the product as it stands: instantaneous
pitch, the pitch timeline, lowest/highest pitch and vocal range are all **already
built and tested** — A4 is detected as A4 within 5 cents, at three layers. There
is **no musical key, no ordered note sequence, no tempo, and categorically no
reference song**: `song` appears in the codebase only as a test upload filename,
and `reference` never once means a reference recording.

### Phase 8 — what is built

- **Slice 1** — `audio_analysis/key.py`: a pitch-class profile folded from the
  stored timeline, and the key those twelve numbers best fit. Every threshold set
  by a recorded sweep rather than picked; Temperley chosen over Krumhansl–Kessler
  because it separates a sung melody from random weights by ~12× rather than ~3×.
- **Slice 2** — `AudioAnalysisService.key()`: the same owner-scoped `current()`
  read `notes()` uses, handing the stored timeline to the estimator. Derived on
  read, so nothing is persisted, no migration exists, and every analysis ever
  completed is answerable.
- **Slice 3** — `GET /recordings/{id}/audio-analysis/key`. A `200` carries the
  key or a null key with the reason it could not be established, and the twelve
  pitch-class shares either way; a recording with no *completed* analysis is a
  different thing and answers `404`. No new error code, no schema change, no
  migration.
- **Slice 4** — the card, below the note breakdown it shares a timeline with.
  All six states of the specification, the pitch-class evidence shown in every
  one of them — including the state with no key — and one presentational
  threshold, `WEAK_KEY_CONFIDENCE = 0.19`, which decides whether an answered key
  is shown with its weakness stated in words and never whether it is shown at
  all. Verified end to end through the real stack: a synthesised C major melody
  reads *C major* at 0.310; a bare unweighted scale reads *C major* at 0.147 with
  A minor — its own relative minor — as the runner-up, and is labelled as thin
  evidence; a hum reads *Not measured*, `TOO_FEW_PITCH_CLASSES`, with its one
  pitch class at 100% shown anyway.

- **Slice 5** — the performance ceiling and the mutation run. The endpoint stopped
  loading the analysis document twice: `key_of()` folds a record the caller
  already holds, which halves the expensive part of the read and closes the
  window in which two loads could straddle a re-analysis. The ceiling is derived
  from `max_audio_duration_seconds` and `HOP_SECONDS` rather than written down —
  12 931 points, 1.35 ms, under half what `summarise_notes` costs over the same
  timeline, and 7 216 bytes of peak allocation because the fold copies nothing.
  21 mutations: 20 caught by a named test, 1 confirmed equivalent. **One
  survived** — redefining confidence as the margin over the next *different
  tonic*, the alternative `key.py` argues against at length — because every
  adversarial fixture was stopped by the pitch-class gate before the correlation
  was reached, so none could see how the margin was defined. `AMBIGUOUS_MODE`
  closes it, and the mutation was re-run.
- **Slice 6** — the documentation sweep. `audio-analysis.md` carries the
  algorithm, the profile race and the one that lost, all four thresholds with the
  measurements that set them, what didn't work and the cost; `limitations.md`
  gains the musical-key section; `architecture.md` gains its feature-allocation
  row and its stale claims corrected; `README.md` stops listing shipped features
  as unbuilt; and the specification is marked superseded rather than deleted.

**Phase 8 is complete.** Its definition of done is met, and what it deliberately
does not contain — tempo, beats, melody transcription, note events, a reference
song, transposition and compatibility — is unchanged and still out of scope.

## Phase 9 — audited, specified, blocked

Phase 9 was audited after Phase 8 closed, in the same way 10.7 audited Phase 8:
inspect the repository first, and write nothing that the source does not support.
The result is [phase-9-specification.md](phase-9-specification.md). **No Phase 9
code was written, and none should be until the decision below is taken.**

What the audit established, from source rather than from prose:

- **None of Phase 9 exists.** No reference upload, no reference storage, no
  catalogue, no external provider, no reference selection, no vocal stem. No
  reference metadata of any kind is stored — not a title, a key, a range, a
  melody, a BPM or a source. No code calculates range overlap, key
  compatibility, difficulty, suitability, a target key or a semitone shift.
  `song` still appears in the codebase only as a test upload filename, and
  `reference` still never means a reference recording.
- **The singer's half is finished.** Detected range, the pitch timeline, the note
  histogram and the musical key are all built, stored and owner-scoped. What is
  missing is the *other* side of the comparison, and only that.
- **The blocker is a product decision, not an engineering one.** Where a
  reference song comes from — user upload, an internal catalogue, an external
  provider, or metadata the user types — changes the storage, the API, the
  algorithm, the test plan, the cost and the copyright position. Four options are
  analysed in the specification with pros, cons and unresolved questions.
  **None is chosen there**, because an engineer choosing would be inventing the
  requirement.
- **Two things Phase 9 must not do by default** are flagged in the specification
  rather than assumed: a single composite compatibility percentage (no
  measurement anywhere sets its weights, and the codebase has refused composite
  figures three times already), and any AI-produced number.

The specification also carries what *can* be settled without the decision: the
concepts kept apart, the required/optional/deferred/unknown input list, the
compatibility and transposition semantics, a storage and ownership impact
analysis, a draft API and frontend, a deterministic fixture plan, the performance
envelope — which needs no Redis, no queue and no new infrastructure — and a
preliminary definition of done.

**One question stands between Phase 9 and implementation, and it is
[§16 question 1](phase-9-specification.md#16-unresolved-product-decisions).**

## Phase 10 — where it stands

Step 7P audited the whole repository before choosing what to build next, and
chose Phase 10 over Phase 8 on the evidence: 14 of 16 endpoints were gated on a
bearer key with **no** way to see it, move it, or delete what it owned, and
Phase 8 would have increased the value sitting behind that key.

Delivered in 7P:

- `GET /api/v1/identity` — what this key holds, in counts
- `DELETE /api/v1/identity` — remove every recording, analysis and stored audio
  file, irreversibly
- The recovery key shown in the browser, so it can be saved and pasted on
  another device — the server stores only a hash and cannot replace it
- `IdentityResolver`, the documented seam where a real authentication provider
  will resolve to the **existing** `owner_id`

Delivered in 10.2 — *credentials attached to the existing owner*:

- A `credentials` table. An owner no longer *is* a key: it *has* keys, several
  of them, each named and each revocable. The migration copies every existing
  `token_hash` across and drops the column in the same transaction, so every key
  that worked before resolves to the same owner id afterwards.
- `POST /api/v1/identity/credentials` — another way in, returned once
- `DELETE /api/v1/identity/credentials/{id}` — revoke one, never the last
- `GET /api/v1/identity` now lists the ways in and marks the one in use
- The seam made real: `IdentityResolver` was declared in 10.1 and consumed
  nowhere. The API now depends on the protocol, with `BearerKeyResolver` behind
  it, and `tests/test_resolver.py` drives the whole owner-scoped product through
  a substituted resolver to prove no route knows how identity is established.

Delivered in 10.3 — *bounding anonymous identity creation, and rate limiting*:

- The failure was measured before it was fixed: 60 concurrent requests carrying
  no credential created 60 owners and 60 credentials in 0.47 s (~128 identities
  per second), and an anonymous caller could reach a billable provider call.
- New identities are limited per client address; the guard runs **before** the
  rows exist, so a refusal writes nothing.
- Costly requests — upload, either analysis, feedback, adding a key — are
  limited per *owner*, so a shared address cannot spend somebody else's
  allowance.
- A recognised key never touches the identity limit; reading is never limited;
  deleting an identity and revoking a key are never limited.
- `RATE_LIMITED` / `429` with `Retry-After`, in the existing envelope.

Delivered in 10.4 — *frontend error boundaries*:

- `app/error.tsx` — a render failure in a page now shows a VocalLens failure
  state with a retry and a way home, instead of Next.js's default screen
  (unstyled "Application error: a client-side exception has occurred" in
  production, a stack trace in development).
- `app/not-found.tsx` — a wrong address is presented as a wrong address, not as
  a fault: no alert styling, no danger colour and deliberately no retry, since
  re-requesting a missing address fails identically.
- `app/global-error.tsx` — the last resort when the root layout itself fails.
  It brings its own `<html>`, `<body>`, palette and fonts, so it does not
  depend on the stylesheet, fonts or layout that just broke.
- All three render only fixed copy from `lib/error-presentation.ts`, which is
  handed the error object and discards it. Verified by throwing an error
  carrying a DSN, a filesystem path, a stack trace, an owner id, a bearer key
  and an API key, and asserting none of it reaches the DOM.
- Expected API failures are unchanged: they are still handled inline by the
  panel that made the call, and do not reach these boundaries.

Delivered in 10.5 — *deployment hardening*:

- An nginx proxy is the **only** published port. The API, the web app and
  **PostgreSQL** — which previously published 5432 to the host with a default
  password — are now internal.
- That is what makes `RATE_LIMIT_TRUSTED_PROXIES=1` sound, and it was measured:
  five forged-`X-Forwarded-For` requests sent *directly* to the backend created
  five identities against a limit of two; the same five through the proxy
  created none.
- An edge body cap equal to `MAX_AUDIO_SIZE_MB`, returning the API's own
  `FILE_TOO_LARGE` envelope. An oversized upload is refused in 0.24 ms, reaches
  no application worker, mints no identity and spends no quota.
- `X-Content-Type-Options`, `Referrer-Policy` and `X-Frame-Options` on every
  response. No CSP and no HSTS, both deferred for stated reasons.

Delivered in 10.6 — *identity retention*:

- `owners.last_seen_at` (migration 0003), written when a credential resolves and
  throttled so it is not a write on every read. Backfilled from the newest thing
  each existing owner demonstrably did.
- An identity is reclaimable only if it owns **no recordings** *and* has not been
  seen for `IDENTITY_RETENTION_DAYS` (30 by default, and configurable because
  nothing in this repository specifies a period). Identities that own recordings
  are never reclaimed at any age — that would be a product decision nobody has
  made.
- `python -m app.db.cleanup_identities`, idempotent and safe to repeat, with
  `--dry-run`. No scheduler was added.
- Claim and delete are one transaction under `FOR UPDATE SKIP LOCKED`, so a
  returning user or a second cleanup run cannot lose a race.

Delivered in 10.9 — *a Content-Security-Policy that is real*:

- 10.5 shipped three security headers and deliberately shipped no CSP, writing
  down why in three places: Next.js emits inline scripts, so a policy worth
  having needs a nonce minted per request. This step built the nonce.
  `frontend/proxy.ts` mints 128 bits per request, sets the policy on the
  *request* so Next.js stamps the nonce onto every script element it renders,
  and repeats it on the response.
- The edge still sets none, and that is now a decision rather than a deferral: a
  browser given two `Content-Security-Policy` headers enforces both, and no
  policy in the nginx config can know a nonce minted after it was written.
- `script-src` is nonce-gated with `'strict-dynamic'`, so a `<script src>`
  injected into the markup is refused even pointing at our own origin.
  `object-src`, `base-uri`, `form-action` and `frame-ancestors` are `'none'` —
  four things this product never does. No `upgrade-insecure-requests`, for the
  same reason the proxy claims no HSTS.
- **Pages now render per request**, because a prerendered page is HTML built
  before the nonce existed. Measured with the route left static: Chromium
  refused all ten of the page's own script elements and the HTML sat there
  inert. The cost of the fix was measured too — 2.816 ms → 7.068 ms mean per
  document — and what it gives up is a cached render of an app shell that reads
  nothing and calls nothing.
- `style-src` keeps `'unsafe-inline'`, and the alternative was built before it
  was rejected. Under `style-src 'self' 'nonce-…'` all twenty inline style
  declarations kept working — React writes them through the CSSOM, which CSP
  does not reach — and the thing that broke was `app/global-error.tsx`, the
  last-resort failure page, rendered unstyled.
- Verified through the real stack in Chromium — upload, speech analysis, audio
  measurement, the musical key card, the identity panel, and the microphone path
  separately — with 0 violations, 0 console errors and 0 page errors. Two
  couplings that fail silently everywhere except a browser (the layout's
  `force-dynamic`, and setting the policy on the request as well as the
  response) are asserted from the files that carry them.

Delivered in 10.10 — *a rate-limit counter every worker shares*:

- 10.3 wrote down that its limiter was one process's memory and that several
  workers would multiply the limit. That caveat sat in three files and was never
  measured. It is now, against a real server at a limit of five new identities
  per hour: **1 worker → 5, 2 → 10, 4 → 20, 8 → 34**. The multiplier is not
  approximately the worker count; it *is* the worker count.
- The reason 10.3 gave for rejecting a database-backed counter — it "would add a
  write to every request in order to bound the cost of writes" — **did not
  survive re-inspection of the call sites**. Neither guard runs on every request:
  the identity guard runs only from `before_mint`, immediately before two rows
  are inserted, and the costly guard only on uploading, either analysis, feedback
  and adding a key. Reading is never limited. Every counted request was already
  about to write, which is the same ground 10.6 stood on.
- `RateLimiter`, an async protocol with two implementations — exactly where 10.3
  said a shared counter would belong if one were ever needed. The in-process
  algorithm is untouched and still synchronous and I/O-free.
- The correctness argument is one statement: an `INSERT ... ON CONFLICT DO
  UPDATE` that rolls the window and counts the attempt together, so two workers
  collide on the primary key and the second applies its increment to the row the
  first committed. The obvious alternative was built to see it fail — a
  read-then-write limiter allowed **40 of 40** attempts against a limit of 5,
  where the upsert allowed exactly 5.
- One contract suite runs against both limiters, so a shared counter that
  disagreed with the one it replaces fails the same assertion. Nothing in it
  sleeps: the in-process limiter gets a hand-cranked clock and the shared one has
  its stored window backdated.
- **The default stays in-process**, because for a single-worker deployment it is
  not a compromise — 0.001 ms against 0.9 ms, and exact. `RATE_LIMIT_BACKEND=
  database` is opt-in, and selecting it without `DATABASE_URL` is refused at
  startup rather than falling back to four counters that look like one.
- What it gives up is written down rather than glossed: the shared window is
  anchored to the database server's wall clock, because a monotonic clock is
  meaningless between machines.
- **A bug older than this step surfaced and was fixed.** Making several workers a
  supported shape exposed that `apply_migrations` created `schema_migrations`
  *before* taking its advisory lock, and `CREATE TABLE IF NOT EXISTS` is not
  atomic against a concurrent create. Six workers booting together against a
  fresh schema failed **25 of 30**; with the lock first, 30 of 30 succeed. It
  only ever bit an empty database, which is why no existing test caught it.

Delivered in 10.11 — *the read path stops paying for the pitch timeline*:

- The first performance work in the phase, and the failure was measured before
  it was fixed. One stored analysis of a five-minute recording is **1 596 kB of
  pitch timeline and 1.2 kB of everything else**, and *every* read loaded all of
  it — including the reads that return no pitch point at all.
- The worst of them is the one the browser **polls** while a measurement runs.
  `GET /audio-analysis` returns a range, a stability block and a count; it was
  costing 116.7 ms and 19 MB of peak allocation per poll to do it.
- `AudioAnalysisSummary` is the record without the points, plus a
  `pitch_point_count` so a reader can still tell "measured 12 931 frames" from
  "measured nothing". `AudioAnalysis` is that **plus** the timeline, as a
  subclass — so anything needing only state accepts either, and anything reading
  the points, or writing the document back, asks for the full type. Handing a
  summary to a write is a type error rather than a silently erased timeline.
  The count is derived from the points and a supplied value is discarded, so it
  cannot drift or be forged.
- In SQL: `document - 'pitch_points'` with `jsonb_array_length` beside it, so
  PostgreSQL drops the points before the row crosses the socket. Through the
  real driver, **83.8 ms and 19 151 kB → 1.8 ms and 15 kB**. End to end over
  HTTP: `GET /audio-analysis` 116.7 → 8.4 ms, `GET …/feedback` 120.2 → 8.6 ms.
- `/pitch` and `/key` are **unchanged**, and that is the result rather than a
  gap: they return or fold the points, so they still load them. The split makes
  paying for a timeline a choice a route makes, not a cost every route pays.
- `/notes` halved, 189.0 → 90.2 ms, for a different reason found on the way: it
  loaded its analysis **twice**, once for the ids in its response and once
  through `service.notes`. `notes_of` is now the seam `key_of` was given in
  Phase 8 slice 5, closing the same window — two loads can straddle a
  re-analysis and pair one record's ids with another record's notes.
- Nothing was migrated, nothing is cached, nothing is stored twice, and every
  analysis ever written answers the new reads. The contract suite covers them
  against **both** repository implementations, so a double that quietly kept the
  points fails the same assertion the database passes.

Delivered in 10.12 — *one expression, one decompression*:

- 10.11 fixed the audio-analysis read path and left three things unmeasured. All
  three were measured here, against a real server holding 50 five-minute
  recordings: the history list is **8.7 ms**, the speech read **11.6 ms**, and
  the frontend serves **772 kB** of JavaScript for its one route. None of them is
  the problem. The one nobody had looked at is: `GET /recordings/progress` cost
  **535 ms**, the slowest read in the product by a factor of five, and it is on
  the home page.
- The cause is a property of PostgreSQL that 10.11 assumed the other way round.
  A value too big for its row lives in the TOAST table and is decompressed **once
  per expression that references it**, not once per row. The progress query read
  eleven scalars by path out of `a.document`, so a 30-recording window fetched
  and decompressed the same 253 kB document eleven times. The buffer counts say
  it plainly: 11 834 for eleven scalars, 1 135 for one.
- The fix is which side of the lateral the document is read on. Projecting
  `document->'metrics'` leaves ~600 bytes for the scalars to come off:
  **593 ms → 30 ms** in SQL, **535.1 ms → 39.3 ms** end to end over HTTP, byte
  for byte the same response. No migration, and no metric denormalised — the
  module's own note had concluded that denormalising was the only way out, and
  it was the multiplier that was expensive, not the decompression.
- **The same mistake was in the read the browser polls.** 10.11's summary form
  selected `jsonb_array_length(document->'pitch_points')` beside
  `document - 'pitch_points'` and wrote down that the second reused what the
  first had decompressed. Measured: 1.80 ms for the strip alone, 2.31 ms for the
  count alone, **5.70 ms for the pair** — the count cost more than the read it
  rode on.
- `pitch_point_count` is a column now (migration 0005), written from the same
  object in the same statement as `status` and `feedback_status`. That is a
  schema change, and it earns itself twice: `GET /audio-analysis` 11.9 → 8.8 ms,
  and the documents written before 10.11 — which carry no count and were the
  whole reason the expression existed — are answered by a one-time backfill
  instead of by a decompression on every read.
- A defect was found by the test written for it rather than in production: the
  repository first wrote `analysis.pitch_point_count`, which the model derives
  during *validation*, and `model_copy(update=...)` skips validators. A record
  assembled that way carries a stale count beside a fresh timeline. It counts the
  tuple it is serialising instead, which cannot be stale however the record was
  built.
- Four tests, against a real database. Two count how many times each statement
  reads `document`, because the cost is invisible in the results — a query that
  decompresses a document eleven times returns exactly what one that decompresses
  it once returns. One seeds an analysis through the pre-0005 schema and checks
  the backfill counts its timeline. One follows the column through the write that
  attaches a timeline to a pending record.

Delivered in 10.13 — *a page of history says whether it is the whole history*:

- 10.11 and 10.12 asked how expensive the reads were. This step asked whether
  they were **complete**, starting from the surface 10.12 had sized but never
  profiled — the frontend. In Chromium the home page is healthy: 180 kB of
  JavaScript over the wire, first contentful paint at 88 ms, no console errors.
  Nothing there needed fixing, and nothing there was changed.
- What the browser did reveal was a defect nobody had looked for. With **137
  recordings in the account the list rendered 50**, described them as "Everything
  you have uploaded from this browser, newest first", and announced "50
  recordings" to a screen reader. The API was no better: `count` and `limit`, and
  no field that could say the other 87 existed. Past `limit=200` — the maximum —
  they were unreachable at any URL. No document anywhere recorded that as a
  decision, because it was not one.
- This is the same rule the rest of the product is built on, applied to a list
  instead of a measurement: **do not state what the data does not support.** A
  truncated history presented as complete is the same kind of untruth as a
  measurement that was never taken rendered as a zero.
- `GET /recordings` takes a `cursor` and returns `next_cursor`. Whether more
  exists is *established* — the query asks for one row more than the caller
  wanted, and the extra row's arrival is the answer — rather than inferred from
  `count == limit`, which cannot distinguish an owner with exactly 50 recordings
  from one with 500.
- **Keyset, not offset**, and both reasons were measured on one owner with 5 000
  recordings. Cost: the 81st page of 50 is 0.127 ms reading 51 rows by keyset
  against 2.705 ms reading 4 050 by offset, and over HTTP page 100 (5.7 ms) costs
  what page 1 does (6.5 ms). Correctness: an upload between two requests cannot
  shift the window, where an offset would begin page two by repeating page one's
  last row.
- The cursor carries `created_at` **and** the recording id, because the tie-break
  is the id and a timestamp-only cursor loses or repeats everything created in
  the same instant. It is opaque but not secret, and it is not a scope: ownership
  stays in the `WHERE` clause, so somebody else's cursor selects a slice of *your*
  recordings and reaches nothing of theirs. A cursor this server did not issue is
  a `VALIDATION_ERROR`, never an empty page.
- The browser says what it is showing — "50 recordings shown. More can be
  loaded.", then "137 recordings." once complete — and **invents no total**,
  because nothing counts one. Paging is a pure fold in `lib/history.ts`, tested
  without React, the arrangement `analysis-runner.ts` already uses for polling.
  A failed "show older" reports beside the loaded list rather than replacing it.
- Verified end to end in Chromium against the real stack: 50 rows on load, 100
  after one click, 137 after two, the button gone, 137 unique filenames, no
  console errors. Nine backend tests, eight frontend ones, and the in-memory
  double pages identically so the contract suite holds both to it.

Delivered in 10.14 — *what concurrency does to a read*:

- Every number in 10.11–10.13 was taken one request at a time, and that was
  written down as the gap. This step took them again with several requests in
  flight, against the same real server: one worker, 30 five-minute recordings,
  each a 1 685 kB document of 12 931 points.
- **Most reads scale, and nothing about them was changed.** The history list, the
  progress query, the identity panel, the speech read and the polled audio
  summary all hold their throughput as concurrency rises — `GET /recordings`
  serves 113 requests a second at c=1 and 154 at c=16.
- **The three reads built on the pitch timeline do not scale at all**: ~7
  requests a second whether one client is asking or sixteen, and p95 at c=16 of
  2.0–2.3 seconds. Sixteen times the latency for the same throughput is the
  signature of work that cannot overlap.
- Profiling one read says where it goes: of ~80 ms, 23 ms is SQL and transfer,
  20 ms is `json.loads`, and **30 ms is pydantic building 12 931 `PitchPoint`
  models**. The folds those points exist for cost 3.3 ms and 1.4 ms. All of it
  runs on the event loop holding the GIL, so it is not one slow request — it is
  every other request in the process waiting.
- That is visible from outside, and it is what a user would feel. While one other
  client had an analysis page open, the poll the browser makes while a
  measurement runs went from **14.2 ms to 301.0 ms**; with three, to **965.1 ms**.
- **`/pitch` was building 12 931 points to return 995.** It has capped its
  response at `max_points` since Step 7I and honoured that by slicing a list.
  PostgreSQL selects the sample now — by ordinality, with the stride computed
  from the stored `pitch_point_count` in the same statement, so the sample and
  the total it is a sample of come from one row.
- PostgreSQL's half does not get cheaper (21.4 ms → 19.1 ms) and that is the
  point: the ~50 ms of parsing and validation leaves the event loop, where it
  serialised everything, and 1 685 kB across the socket becomes 133 kB. End to
  end **172.9 ms → 38.6 ms**, throughput 7.1 → 25.9 requests a second at c=1 and
  8.5 → 42.8 at c=16, p95 at c=16 2 279 ms → 436 ms, and the poll during one open
  dashboard 301.0 ms → 169.6 ms.
- The response is unchanged point for point, asserted by a test that compares the
  sample against the whole timeline sliced. The type discipline from 10.11 holds:
  a `DecimatedTimeline` carries an `AudioAnalysisSummary`, so a record holding 995
  of 12 931 points has no path to a write, and its validator refuses a sample
  whose size does not follow from the count and the stride.
- Twenty-one tests, one contract suite over both repository implementations —
  because "every n-th point" is only well defined if a Python slice and
  `WITH ORDINALITY` start at the same one — plus the SQL-shape test 10.12's
  precedent asks for: two references to `document`, the strip and the expansion,
  and no third.
- **What is left is not waste, and is recorded rather than fixed.** `/notes` and
  `/key` fold every point, so they still load every point, unchanged at ~135 ms
  and ~7 requests a second. ~25 ms of that is the floor for reading the document
  at all; the rest is model construction that only a different input type would
  remove, and a thread would not help because `json.loads` and pydantic both hold
  the GIL. The two options — flat field arrays (15.4 ms in PostgreSQL, and it
  changes the signature of two measurement functions) or storing the derived
  answers (which 10.8 rejected so that every analysis ever completed stays
  answerable) — are analysed in [architecture.md](architecture.md) and neither is
  chosen.

Delivered in 10.15 — *what a fold actually reads*:

- 10.14 measured `/notes` and `/key` at ~135 ms and ~7 requests a second, named
  the two ways out and chose neither. This step took the first — read the fields
  the folds use rather than the frames — and leaves the second, storing the
  derived answers, rejected for the reason 10.8 gave: derive on read and every
  analysis ever completed is answerable.
- Neither read may be given a *sample*, the way the graph is: a note breakdown of
  every thirteenth frame is a breakdown of a different recording. What they need
  not see is the frame. A stored point carries six fields; the note breakdown
  reads `midi_note` and `cents`, and the key reads `midi_note`.
- `PitchFields` is those fields as **one array per field rather than one object
  per frame**, and `TimelineFields` is a record with them attached — the fourth
  form of an audio-analysis read, after the summary (10.11) and the sample
  (10.14). PostgreSQL projects them with two `array_agg`s over one
  `jsonb_array_elements` walk, so the statement touches `document` exactly twice,
  which is 10.12's rule.
- The repository read went **106.2 ms → 27.3 ms** and what crosses the socket
  1 722 kB → 130 kB. PostgreSQL's half is unchanged at ~18 ms; what left is the
  ~85 ms the API process spent building 12 931 models on the event loop.
- End to end over HTTP, measured against the previous build running beside this
  one on the same database: `/notes` **151.2 → 37.3 ms**, `/key` **143.3 →
  38.8 ms**, and at sixteen concurrent readers p95 **2 321.8 → 273.2 ms** and
  **1 784.7 → 246.4 ms**. Throughput is the number that says the work left the
  event loop: `/notes` 8.4 → 64.7 requests a second at c=16, `/key` 10.0 → 71.4.
  With three clients reading a breakdown, the poll the browser makes while a
  measurement runs went from **212.5 ms to 33.0 ms**.
- **The responses are identical, and that was checked rather than reasoned
  about**: both servers were asked for the same recording's notes and key, and
  the JSON matched.
- The folds got cheaper too — 3.3 → 2.0 ms and 1.4 → 0.9 ms — because reading
  `midi_notes[i]` is not attribute access on a pydantic model. Bounds did not go
  with the frames: every note is still checked to be a MIDI number and every
  deviation within ±50 cents, for 0.83 ms across 12 931 frames.
- A note's **name is derived from its number** rather than read: it is needed per
  note, not per frame, and the analyzer writes it with the same function from the
  same integer. Two tests hold that: the two naming entry points agree for all
  128 notes, and every point the real analyzer writes is named by its own number.
- Ten new tests — fifteen as collected, because five of them run against both
  repository implementations. "The fields of a timeline" is only well defined if
  the arrays PostgreSQL aggregates are the values the stored points hold, in the
  order they hold them, so that is asserted rather than assumed. With them: the
  SQL-shape test 10.12's precedent asks for, the two naming properties, and four
  rewritten load counters that can tell a fields read from a whole-document one.
- **What is left was measured, not assumed.** `GET /recordings/compare` is now
  the slowest read in the product — **253.5 ms at c=1, 3 152.9 ms p50 at c=16,
  3.8 requests a second** — because it loads two whole documents and folds two
  note breakdowns from whole frames. Same defect, same fix, one statement away;
  it is recorded rather than done here.

Delivered in 10.16 — *the same fix, on the read that does it twice*:

- 10.15 named its own successor rather than doing it: `GET /recordings/compare`
  was the last read building pitch frames nobody reads, and it built **two**
  recordings' worth. This step is that fix and nothing else — the type, the
  projection and the rule about how often a statement may touch a document all
  come from 10.11 through 10.15. What is new is that the statement reading two
  recordings at once now obeys them.
- A comparison needs each side's identity, its analysis record and its note
  breakdown. Only the breakdown involves the timeline, and it is the read that
  may **not** be given a sample — of every thirteenth frame is a breakdown of a
  different recording — so both sides are still read whole. Whole, as fields:
  the semitone and the deviation from it, two arrays per side.
- At the repository, on two five-minute recordings of 12 931 points each: what
  crosses the socket goes 2 916 kB → **209 kB** and the read 122.1 ms →
  **21.7 ms**, of which SQL and transfer is 44.3 → 18.7 ms. The ~78 ms in
  between is 25 862 `PitchPoint` models no longer built on the event loop to
  read two fields off each. Both statements were timed in one process against
  the same rows.
- End to end, the previous build running beside this one on the same database:
  **137.5 → 36.0 ms** at one client and **2 160.3 → 239.9 ms p50** at sixteen,
  p95 2 577.3 → 304.1 ms, and throughput **7.3 → 63.1 requests a second at
  c=16**. Flat throughput under rising concurrency was the signature of work
  that cannot overlap; it now scales. `/notes` and `GET /audio-analysis` were
  measured on both builds as controls and did not move.
- What a user would feel is somebody else's comparison: with three clients
  holding one open, the poll the browser makes while a measurement runs went
  from **303.6 ms to 35.9 ms**.
- **The response is identical, and that was checked rather than reasoned
  about**: both builds were asked for the same comparison and returned the same
  14 478 bytes — 24 notes, 7 metrics — byte for byte.
- The two aggregates are **shared with the `/notes` read rather than copied**. A
  second copy could order its arrays differently, and a breakdown built from
  deviations belonging to other notes looks entirely reasonable. There is one
  `PITCH_FIELD_AGGREGATES` and a test that both statements use it.
- The latest analysis is **chosen before its timeline is expanded**. Written the
  natural way — the aggregate inside the subquery that orders and limits — a
  re-analysed recording would have had every one of its timelines walked so that
  all but one could be discarded.
- Nine new tests, six of them three contract tests run against both stores,
  because "the fields of a comparison" is only well defined if the arrays
  PostgreSQL aggregates for two recordings are the values the stored points
  hold. With them: the SQL-shape test 10.12's precedent asks for (the analysis
  document read exactly twice, the recording's once), a test that both
  projections are the same aggregates, and one asserting a side of a comparison
  breaks down exactly as the single-recording read does. Both were checked by
  mutation: aggregating the two arrays in different orders, and taking the
  oldest analysis instead of the latest, each fail tests that pass now.
- **What is left was measured, not assumed.** No read stands out any more: the
  four timeline-derived reads sit between 21.5 and 29.8 ms at one client and 64
  to 109 requests a second at sixteen, and the three that never touch a timeline
  are ~6 ms and ~210 to 250. The slowest of the four is now `/pitch`, and what it
  spends its time on is the sample it genuinely returns.

Delivered in 10.17 — *what several workers do to a read*:

- The gap 10.11–10.16 each closed with: every number in them was taken on **one
  worker**, while 10.10 had already made several a supported shape. Taken again
  across 1, 2 and 4 workers on the same rig, at sixteen concurrent clients, the
  reads **scale, and the scaling stops where the cores do**: the second worker
  is worth 1.4×–2.1× on every read, the third and fourth together 1.05×–1.35×
  more, because four API processes and PostgreSQL are then sharing four cores.
  `GET /recordings` 196 → 554 requests a second, `/pitch` 55 → 129, `/notes`
  81 → 149, compare 64 → 98. The poll the browser makes while a measurement
  runs, with three clients holding a note breakdown open: **31.0 ms → 12.2 ms**.
- Nothing about a read was changed to get that, and that is the result: after
  10.15 and 10.16 the reads are PostgreSQL's work plus a small fold, and work
  that has already left the event loop parallelises across processes for free.
- **The defect several workers exposed is in the pool, not in a read.**
  `db/pool.py` opened up to ten connections per process behind a comment saying
  "this is one API process" — true when it was written, false since 10.10. What
  PostgreSQL sees is `workers × 10` against the 97 a default server grants.
  Measured at twelve workers: **97 of 97 taken**, `psql` refused at the socket,
  `too many clients` in the log continuously, and no thirteenth worker or
  `cleanup_identities` run able to connect. The API's own requests did not fail
  — psycopg queues a caller that cannot get a connection — so from outside, one
  application quietly taking the whole database looked like latency.
- The default is **four**, and the sweep says why: below four costs real
  throughput (a pool of 1 halves the comparison read), at four it costs the
  comparison read ~7% at sixteen concurrent clients on a single worker and
  everything else nothing, and at four workers a pool of four and a pool of ten
  are the same throughput on 16 connections against 40. Ten left room for nine
  processes; four leaves room for twenty-four. The same load that took the
  server down serves **82.8 requests a second against 87.4** through the fixed
  default — the ceiling that broke it was never being used.
- `DB_POOL_MAX_SIZE` and `DB_POOL_MIN_SIZE` are configuration, because a
  per-process share of a server-wide limit depends on the deployment. Startup
  logs the arithmetic an operator scaling workers needs and cannot derive from
  the API's own settings — the pool size, the server's `max_connections`, and
  how many processes of this size fit — and refuses a pool too large to fit even
  once, the way 10.10 refuses `RATE_LIMIT_BACKEND=database` with no
  `DATABASE_URL`.
- Connections now say who holds them: `application_name` is `vocallens-api`, or
  `vocallens-maintenance` for `cleanup_identities` and `import_filesystem`,
  which also ask for **one** connection each — they are sequential, and the
  moment they matter is the moment the API's workers are holding everything.
  `pg_stat_activity` on an exhausted server is a list of culprits rather than a
  list of rows.
- Ten new tests. The ceiling the whole step rests on is counted rather than
  assumed — twelve callers against a pool of three must find exactly three
  backends, and "at most three" would have passed while counting nothing. The
  budget is read from the server rather than from a comment, an oversized pool
  is refused *and* releases what it opened, and startup is asserted to build the
  pool the settings describe, because a wrongly sized pool serves requests
  perfectly well. Two existing suites now state their own pool size: they open
  six and eight connections *simultaneously* to make a race a race, which is a
  different requirement from a worker sharing a server.
- **What is left, and it is unchanged by this step**: a deployment that scales
  workers must still move `RATE_LIMIT_BACKEND` to `database` by hand, and one
  left on `memory` still gets a counter per worker. Everything above was
  measured on one host with PostgreSQL sharing the workers' four cores, so where
  the scaling flattens belongs to the rig, not the code.

Delivered in 10.18 — *what a hundred times the data does to a read*:

- The other gap every step from 10.11 to 10.17 declared: the speech pipeline and
  the progress query had never been measured at scale. Measured here against one
  owner holding **5 000 five-minute recordings**, each with a completed audio
  analysis of 12 931 points and a completed speech analysis — a 1 131 MB
  `audio_analyses` table — beside the 50-recording rig 10.12 used.
- **Both are flat, and so is every other read**: the speech read 6.2 → 6.1 ms,
  the history list 6.4 → 6.8 ms, `GET /audio-analysis` 7.3 → 7.7 ms, the
  progress query 28.9 → 28.0 ms. A hundred times the rows changes nothing about
  how many rows a query bounded by an index actually touches.
- **What is not flat is the window, and the window is a documented parameter.**
  `GET /recordings/progress` takes `limit` up to 200, and at 200 it cost
  155.5 ms — 200 rows each decompressing a 253 kB document to read ~600 bytes of
  metrics out of it. That is precisely the one decompression per row 10.12 had
  left, having written that denormalising was "a schema change worth making only
  when a measurement demands it". This is the measurement.
- Migration 0006 makes the projection a stored generated column —
  `metrics JSONB GENERATED ALWAYS AS (document -> 'metrics') STORED` — and the
  lateral selects it. **The statement no longer mentions `document` at all**,
  which is the strongest form of 10.12's rule: not "touch it once per row" but
  "do not touch it". In PostgreSQL, a window of 200: **141.1 ms and 7 127 TOAST
  reads → 1.7 ms and 262**.
- End to end over HTTP, the previous build running beside this one on the same
  database: the default window **28.7 → 7.7 ms** and 95.6 → 161.9 requests a
  second at sixteen clients; `limit=200` **155.5 → 19.4 ms** and 21.6 → 53.4.
  The progress query is no longer the slowest read that never touches a
  timeline — it now sits in the same 6–8 ms band as the history list and the
  speech read. **The response is identical, checked byte for byte** across both
  builds.
- **Generated rather than written, and that is the point.** `pitch_point_count`
  (10.12) is a column the repository fills, honest only because a rule is stated
  in three places and followed — and that rule had already been got wrong once,
  by a `model_copy(update=…)` that skipped the validator deriving it. This column
  has no rule to break: PostgreSQL computes it in the same write, no INSERT
  mentions it, there is no backfill, and an INSERT that tries to supply one is
  refused by the server. Three tests, one per clause.
- Two costs recorded rather than glossed: the migration **rewrites the table**
  (18 s for 1 131 MB, under the startup advisory lock, once), and the column is a
  second copy of ~600 bytes per row (4 MB across 5 000 recordings). It is a
  physical projection the database maintains, not a stored *measurement* —
  nothing derives a number and keeps it, so 10.8's rule that every analysis ever
  completed stays answerable from its document is untouched.
- **What is left was measured, not assumed.** `GET /identity` is now the only
  read whose cost grows with how much one owner holds — 6.1 ms at 50 recordings
  and 12.0 ms at 5 000 — because it answers "what would I lose?" by joining every
  recording to every analysis and sorting the result for three `count(DISTINCT)`s.
  Nine of those twelve milliseconds are the sort. Recorded here rather than
  fixed, the way 10.15 recorded the comparison read.

Delivered in 10.19 — *the last read that grows with a history, and a number that
was answering a different question*:

- 10.18 named `GET /identity` as the only read whose cost grows with what one
  owner holds and recorded it rather than fixing it. This step is that fix, and
  **the defect it turned up on the way matters more than the milliseconds**.
- The identity panel's `ai_feedback` counted *analyses* whose feedback had
  completed. Both places it reaches a reader are sentences about **recordings**:
  "this key holds 5 recordings, 3 measured, 2 with generated feedback", and the
  deletion confirmation's "2 of them carry generated feedback, which cannot be
  recovered". A recording analysed twice with feedback both times made the second
  sentence say that one recording included two of them. Same rule as everywhere
  else in this product — do not state what the data does not support — and the
  same defect 10.13 found in a list. All three counts are counts of recordings
  now, and none can exceed another.
- **The two stores disagreed about it, in two different ways, and nothing had
  asked.** SQL counted feedback runs; the in-memory double counted recordings
  whose *latest* analysis carried feedback. The double was also wrong about
  "measured": a recording analysed yesterday and re-analysed a minute ago read as
  unmeasured while the new run was pending. Two contract tests now hold both
  stores to the same answer on the shape that separates them, and both failed
  before this step — differently.
- The rewrite folds each recording's analyses to two booleans in a lateral, so
  the outer query counts one row per recording and no `DISTINCT` is undoing a
  multiplication the statement just performed. Through a pooled connection
  against an owner holding 5 000 recordings in a database of 201 owners:
  **16.1 ms → 11.7 ms**, and 0.32 → 0.31 ms for an owner holding 25. End to end
  `GET /identity` goes **23.6 → 19.6 ms** at one client, and does not move at
  sixteen, where the endpoint is waiting on other things.
- **`psql` ranked the candidates the other way round, and `psql` was wrong.**
  With the owner written in as a literal, PostgreSQL plans afresh and can use
  that owner's real selectivity; a pooled application connection cannot, because
  psycopg prepares any statement it runs five times and the plan is then chosen
  for a parameter it has not seen. Under a custom plan this lateral is the
  *slowest* of the three candidates; under the prepared plan it is the fastest.
  Timing a query in `psql` and shipping the winner would have picked wrong here
  for the first time in this sequence.
- Preparing is not the problem and turning it off would be a bad trade: measured
  on one connection, every other statement in the product is faster prepared —
  the timeline fields 14.3 → 9.9 ms, the history list 0.73 → 0.54, a progress
  window 1.50 → 1.23 — and only this one is worse (10.5 → 16.6). What made it
  different is that its cost is dominated by how much the owner has, from 25 rows
  to 5 000 in the same database, which is exactly what a plan chosen for an
  unknown parameter cannot know. So the fix is a query whose plan does not depend
  on knowing the owner, not a pool setting.

**Phase 10 is not complete.** What 10.2 deliberately did *not* build: passwords,
email, OAuth, sessions, password reset, email verification, MFA, account
recovery, rate limiting, email delivery and account merging. Passwords were
considered and rejected for this slice — adding them while deferring reset,
verification and rate limiting would make the system *less* safe than 128 random
bits, not more. 10.3 added rate limiting but **not** the rest: still outstanding
in Phase 10 are TLS termination (still an external responsibility — the proxy
speaks HTTP and claims no HSTS) and every credential feature 10.2 deferred.
Performance work **started** in 10.11 and continued in 10.12, which measured the
three things 10.11 left alone — the speech read (11.6 ms), the history list
(8.7 ms) and the frontend bundle (772 kB of JavaScript for one route) — and found
the cost somewhere else, in the progress query. 10.13 profiled the frontend in a
browser (180 kB over the wire, first contentful paint 88 ms, no console errors)
and found nothing worth changing, and measured the history read at 5 000
recordings, where paging is flat with depth. 10.14 took every read under
concurrent load — the gap 10.11–10.13 left — found that only the timeline reads
fail to scale, and fixed the one of the three that was building points it threw
away. 10.15 took the first of the two
options 10.14 left open for `/notes` and `/key` — the fields a fold reads rather
than the frames — which took them from ~135 ms to ~38 ms and from ~8 to ~65
requests a second at c=16, and found the read that was then slowest:
`GET /recordings/compare`, which folded two whole timelines. 10.16 gave that read
the same fix, taking it from 137.5 to 36.0 ms and from 7.3 to 63.1 requests a
second at c=16, after which no read stands out: the four built on a timeline sit
within one band and the three that are not are ~6 ms. 10.17 closed the gap every
one of those steps had declared — they were all measured on one worker — by
taking the same reads across 1, 2 and 4 of them: they scale until the cores run
out, no read needed changing, and what several workers did expose was a pool
sized per process for a deployment that had one, which at twelve workers took
every connection the server had. 10.18 answered the other question those steps
kept leaving — the speech pipeline and the progress query at a hundred times the
data — and found both flat: what was not flat was the progress *window*, whose
maximum spent 155 ms decompressing a document per row for 600 bytes of metrics,
which migration 0006 makes a generated column and 10.12 had said it would take a
measurement to justify. 10.19 took the last read 10.18 had named — the identity
summary, the only one whose cost grows with what one owner holds — and found on
the way that one of its three counts had been counting analyses where every
sentence it is rendered into is about recordings, so a re-analysed recording
could make the deletion warning claim one recording included two of them. The
shared rate-limit counter landed in 10.10, and a deployment scaling workers must
still turn it on by hand. Error pages landed
in 10.4; the proxy, the internal network and the edge body cap landed in 10.5;
retention of *empty* identities landed in 10.6, and retention of identities that
hold recordings remains unspecified and unbuilt. The Content-Security-Policy
landed in 10.9 and covers the web app; API responses still carry none, for the
reason recorded in [limitations.md](limitations.md).

The step's success criterion was not "a login works". It was that a credential
can resolve to an already-existing owner **without changing ownership**, while
the entire existing owner-scoped product continues to work unchanged.

Delivered in 10.20 — *the checks nobody ran*:

- 10.19 closed with "the next step in this repository is a decision, not a
  commit". **That was wrong, and it was wrong in the way this project is meant
  to catch**: it listed what the roadmap already knew was outstanding instead of
  auditing for what nothing had written down. One `ls` would have found it —
  there was no `.github` directory. Nineteen steps of Phase 10 rest on a suite
  that ran when somebody remembered to type one command.
- **A skip is a silent pass, and this one was 185 tests wide.** Without
  `TEST_DATABASE_URL` the suite reports 1 584 passed and 187 skipped, in green.
  Those skips are not a random slice: the PostgreSQL half of the contract suite
  (99), the migrations and statement shapes (52), the shared rate limiter (25),
  the filesystem import (7) and the concurrency tests (2) — which is where
  nearly all of 10.11 to 10.19 lives. A workflow that forgot the variable would
  have looked exactly like one that did not.
- `.github/workflows/checks.yml` runs `scripts/check.sh` — the same command a
  contributor runs, not a second list of checks to keep in step — on every push
  and pull request, against a `postgres:16-alpine` service, with
  `TEST_DATABASE_URL` and `REQUIRE_DATABASE_TESTS=1` set.
- The run now **refuses to be quietly incomplete**: with
  `REQUIRE_DATABASE_TESTS=1` a missing DSN is a `UsageError` before collection
  rather than 185 skips and a success. `test_database.py` had claimed a run
  without a database "is not skipped quietly in CI: a run with no database says
  so in the skip reason" — a reason in a log nothing reads is what quiet means,
  and there was no CI to read it. A checkout with no database still runs its
  1 584 tests exactly as before; the flag is set by whoever starts a run that is
  *meant* to be complete, never sniffed from the environment.
- `tests/test_ci.py` holds the workflow to what makes it worth having — both
  variables set, `scripts/check.sh` invoked rather than restated, the PostgreSQL
  major version `docker-compose.yml` deploys, the Python `backend/Dockerfile`
  runs — the way `test_deployment.py` holds the compose file to an unpublished
  database port. Confirmed by mutation: deleting `REQUIRE_DATABASE_TESTS`,
  dropping to `postgres:15` and inlining the commands each fail exactly one
  test, and no other.
- **The workflow was reproduced before it was committed rather than pushed and
  watched.** Its two conditions that differ from any run this project has done
  were both exercised: a database created *empty*, so the suite's own migrations
  build the schema, and the whole suite run as a **non-root user**, because two
  storage tests skip under root and CI would have been the first thing ever to
  execute them. Result: **1 782 passed, 0 skipped** — thirteen more tests than
  the best run available in this container, and no skips at all.

## Where this leaves the project

**The performance thread that ran from 10.11 to 10.19 is finished**, in the sense
that it can name what it did not find rather than what it has not looked at. Every
read in the product has now been measured at one client and at sixteen, across
one, two and four workers, at fifty recordings and at five thousand, and with a
realistic spread of re-analysed recordings and generated feedback. No read stands
out: the four built on a pitch timeline sit in one band, and the rest are
single-digit milliseconds. Three things that *were* found on the way are worth separating
from the milliseconds, because none of them was a performance defect: a page of
history describing itself as the whole history (10.13), a connection budget sized
for a deployment that no longer existed (10.17), and a count of analyses rendered
into a sentence about recordings (10.19).

**This section said "the next step is a decision, not a commit", and 10.20 is
what that claim was worth.** It was assembled from the outstanding items the
roadmap already listed, which is not an audit — it can only ever find what
somebody has already written down. What it missed was that nothing ran the
checks: no `.github` directory, no automation of `scripts/check.sh`, and a suite
that silently skips its 185 SQL tests when no database is configured. The lesson
is 7P's and 10.7's, and it is now recorded twice: **audit the repository, not the
list of known gaps.** What follows is therefore what remains *after* an audit
that went looking, and it should be read as the best current answer rather than
a closed one.

**What Phase 10 still needs is not blocked on engineering.** Four things remain,
and each is waiting on a decision nobody has taken rather than on work nobody has
done:

| Outstanding | What it is waiting on |
| --- | --- |
| Real credentials — passwords, email, OAuth, sessions, reset, verification, MFA, account merging | A product decision about what an account *is* here. 10.2 rejected passwords for its own slice on the evidence: adding them while deferring reset and verification is worse than 128 random bits, not better. |
| TLS termination and HSTS | A deployment decision. The proxy speaks HTTP and claims no HSTS deliberately; a certificate belongs to whoever operates the deployment, and claiming HSTS from a server that cannot serve TLS would break the site. |
| Retention of identities that **hold recordings** | A product decision. 10.6 reclaims only identities that own nothing, because deleting somebody's recordings after *n* days of absence is a policy, and this repository contains no policy. |
| A Content-Security-Policy on API responses | Recorded in [limitations.md](limitations.md). |

**Phase 9 is in the same position and has been since 10.7**: audited, specified,
and waiting on [one question](phase-9-specification.md#16-unresolved-product-decisions)
— where a reference song comes from. Four options are analysed there and none is
chosen, because an engineer choosing would be inventing the requirement.

The honest summary is that every remaining item on this list is waiting on a
decision. What 10.20 showed is that "this list" and "what is left" are not the
same thing, so the summary worth acting on is the weaker one: **nothing left on
the list can be built without an answer, and the list is only as good as the last
audit.**
