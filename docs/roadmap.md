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
| 8 | Song analyser: key, BPM, melody/range estimation, limitations messaging | Specified, not started — [phase-8-specification.md](phase-8-specification.md) |
| 9 | Song compatibility: range overlap, difficulty, transpose suggestions | Planned |
| 10 | Production polish: auth, security hardening, error pages, performance, deployment | Started — identity portability and deletion (7P), credentials attached to the owner (10.2), rate limiting (10.3), error boundaries (10.4), edge proxy (10.5), identity retention (10.6) |

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

**Phase 8 has not started.** Nothing about song analysis, key, BPM, melody
extraction or transposition exists — verified by search, not assumed, and
re-verified by the Step 10.7 audit.

## Phase 8 — specified in 10.7, not built

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

## Phase 8 — blocked by a product decision (10.8)

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

So Phase 8 has **two candidate scopes and no evidence to choose between them**:
musical key (fully specified, no consumer, a *label* rather than a measurement,
and unvalidatable against real singing here) or melody note events (closes the
one gap in "can it show A4 → B4 → C5?", reuses an existing rule, needs one short
design pass). Choosing is a product decision, and an engineer choosing would be
inventing a requirement.

What the 10.8 audit established about the product as it stands: instantaneous
pitch, the pitch timeline, lowest/highest pitch and vocal range are all **already
built and tested** — A4 is detected as A4 within 5 cents, at three layers. There
is **no musical key, no ordered note sequence, no tempo, and categorically no
reference song**: `song` appears in the codebase only as a test upload filename,
and `reference` never once means a reference recording.

**Phase 8 is not started, and must not be started until the gate in
[phase-8-specification.md](phase-8-specification.md#4-the-decision-gate) is
answered.**

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

**Phase 10 is not complete.** What 10.2 deliberately did *not* build: passwords,
email, OAuth, sessions, password reset, email verification, MFA, account
recovery, rate limiting, email delivery and account merging. Passwords were
considered and rejected for this slice — adding them while deferring reset,
verification and rate limiting would make the system *less* safe than 128 random
bits, not more. 10.3 added rate limiting but **not** the rest: still outstanding
in Phase 10 are TLS termination (still an external responsibility — the proxy
speaks HTTP and claims no HSTS), a Content-Security-Policy, a shared rate-limit counter for multi-worker
deployments, performance work, and every credential feature 10.2 deferred. Error pages landed
in 10.4; the proxy, the internal network and the edge body cap landed in 10.5;
retention of *empty* identities landed in 10.6, and retention of identities that
hold recordings remains unspecified and unbuilt.

The step's success criterion was not "a login works". It was that a credential
can resolve to an already-existing owner **without changing ownership**, while
the entire existing owner-scoped product continues to work unchanged.
