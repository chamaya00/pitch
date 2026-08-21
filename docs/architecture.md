# Architecture

## Overview

```
Browser (Next.js)
      │  HTTPS / JSON
      ▼
FastAPI  (app/api/v1)
      │
      ├─► services/audio     decode, validate, preprocess
      ├─► services/analysis  pitch, range, loudness, spectral  ← all numbers
      └─► services/ai        LLM interpretation of those numbers
      │
      ▼
PostgreSQL (recordings, analyses, pitch points)
```

The hard boundary in this system is between **measurement** and
**interpretation**:

- `services/analysis` produces every numeric value the product reports. It is
  deterministic and unit-testable.
- `services/ai` receives an already-computed, structured payload and returns
  language. It is never asked to compute or estimate a measurement.

## Two analyses, never one

A recording can be analysed twice, independently:

```
                     ┌── Speech analysis ──→ STT → transcript metrics → Claude
Recording / upload ──┤
                     └── Audio analysis ───→ pitch → notes → range → stability
```

They answer different questions. Speech analysis needs a transcript and a
provider; audio analysis needs neither and works on a deployment with no
credentials at all. Either can be re-run without touching the other, and either
can fail without affecting the other.

**Nothing combines them.** There is no "voice score", no combined grade and no
type anywhere that could hold one. A speaking rate and a pitch range are
measurements of different things, and averaging them would produce a number
nobody could act on. The API keeps them at separate paths with separate records;
the UI keeps them in separate sections with separate headings.

The live microphone readout is a *third* thing: a browser-local estimate of the
same kind of quantity the audio analysis measures, taken by a different
algorithm over a shorter window for latency reasons. It is labelled "Live
recording estimate" and is never presented as agreeing with the backend result.
See [audio-analysis.md](audio-analysis.md).

## Backend layout

| Path | Responsibility |
| --- | --- |
| `app/main.py` | App factory, CORS, router mounting, lifespan logging |
| `app/version.py` | Single source of truth for the backend version |
| `app/api/v1/router.py` | Aggregates versioned routers |
| `app/api/v1/routes/` | One module per resource (`health`, `recordings`, `analysis`, `audio_analysis`) |
| `app/core/config.py` | Environment-backed settings (`get_settings`, cached) |
| `app/core/logging.py` | JSON log formatter, stdlib only |
| `app/schemas/` | Pydantic request/response models — the API contract |
| `app/api/owner.py` | Resolving (or minting) the caller's anonymous owner |
| `app/db/pool.py` | The connection pool, and the one place a DSN is read |
| `app/db/migrate.py` | Numbered `.sql` files, applied once, checksum-verified |
| `app/db/migrations/` | The schema, in order |
| `app/db/import_filesystem.py` | One-off import of pre-7M JSON documents |
| `app/services/owners/` | Owner identity: model, credentials, repository, the resolver, deletion |
| `app/services/comparison/` | Comparing two recordings: pure arithmetic, eligibility, the owner-scoped query |
| `app/services/progress/` | Measurements over time: the owner-scoped query, the window, the pure series build |
| `app/services/audio/` | Upload validation, metadata, filesystem storage of the bytes |
| `app/services/analysis/` | Speech domain: transcript, metrics, records |
| `app/services/audio_analysis/` | Audio domain: pitch maths, detector, features, analyzer |
| `app/services/ai/` | Provider adapters behind protocols |
| `app/services/orchestration/` | The two workflows, one module each |
| `app/services/` | Business logic, framework-independent |
| `tests/` | pytest suite mirroring the app layout |

Conventions:

- Routes stay thin: validate input, call a service, shape a response.
- Services never import FastAPI. They take plain data and return plain data,
  which keeps them testable without HTTP.
- Settings are read through `get_settings()` (a `lru_cache`d singleton) and
  injected via `Depends`, so tests can override them.
- Everything user-facing is versioned under `/api/v1`. `/health` is also
  exposed unversioned for infrastructure probes.

## Persistence

PostgreSQL is the source of truth for recordings, speech analyses, audio
analyses and owners. The audio *bytes* stay on disk under `RecordingStorage`: a
database is a poor place for megabytes of WAV, and nothing queries inside them.

**No ORM.** The repositories already map domain objects to storage by hand and
have since Step 7A; an ORM would be a second mapping layer to keep in step with
the pydantic models, for a schema of five tables. What is actually needed —
transactions, constraints, indexes, parameterised queries, pooling — `psycopg`
provides, in async mode so a query never blocks the event loop.

The domain object is stored as a JSONB `document` and is authoritative. The
columns beside it (`status`, `created_at`, `recording_id`, …) exist so queries
and indexes have something to work with, and are written from the same object in
the same statement.

Migrations are numbered `.sql` files plus a table recording which have run. Each
runs inside a transaction with the row that records it, so a migration cannot be
half-applied or applied twice; an applied file whose contents changed is a hard
error rather than a silent skip. They are applied at startup under a PostgreSQL
advisory lock, so several processes booting together apply them once between
them.

### Reading a document without the part nobody asked for (10.11)

One JSONB document per analysis is the right shape — the pitch timeline is
written once, read whole and never queried by predicate, so a table of one row
per frame would be ~13 000 rows per recording serving a query nobody issues. But
it makes every read the size of the *longest* thing in the document. Measured on
a completed analysis of a five-minute recording: **1 596 kB of timeline against
1.2 kB for everything else.**

Most reads want the 1.2 kB. The summary endpoint, the feedback state, the
feedback claim and the staleness sweep all decide from a status, an error code
or the presence of metrics, and none of them returns a single pitch point. They
were loading and validating all 12 931 points anyway — including on the read the
browser **polls** while a measurement runs.

The fix is a second read, and a type that stops it being misapplied:

| Type | Carries | Used by |
| --- | --- | --- |
| `AudioAnalysisSummary` | everything but the timeline, plus `pitch_point_count` | the summary and feedback routes, `start`, the feedback claim, the staleness sweep |
| `AudioAnalysis` (a subclass) | that, plus the points | `/pitch`, `/notes`, `/key`, and **every write** |

Two later steps split the first column further, and both are in the table in
[What a fold actually reads](#what-a-fold-actually-reads-1015): `/pitch` reads a
`DecimatedTimeline` (10.14) and `/notes` and `/key` read `TimelineFields`
(10.15), so of the four reads only **every write** still asks for whole frames.

The direction of the subclassing is the guarantee. Anything needing only state
accepts a summary and is handed either kind; anything reading the points — or
re-serialising the document, which a summary would rewrite *without* them — asks
for the full type, and handing it a summary is a type error rather than a
silently empty timeline. `pitch_point_count` is derived from the points
themselves and a supplied value is discarded, so a summary can still say how
much was measured without anyone maintaining a second copy.

In SQL the summary form selects `document - 'pitch_points'`, so PostgreSQL drops
the points before the row crosses the socket. Through the real driver: **83.8 ms
and 19 151 kB peak allocation → 1.8 ms and 15 kB.** End to end over HTTP,
`GET /audio-analysis` went from 116.7 ms to 8.4 ms and `GET …/feedback` from
120.2 ms to 8.6 ms; `/pitch` and `/key` are unchanged, because they genuinely
need what they load.

`/notes` halved, 189.0 ms → 90.2 ms, for a different reason: it was loading its
analysis **twice**, once for the ids in its response and once through
`service.notes`. `notes_of` is now the seam `key_of` was given in Phase 8 slice
5, and closes the same window — two loads can straddle a re-analysis and pair
one record's ids with another record's notes.

What this is *not*: a cache, a denormalisation, or a schema change. Nothing was
migrated, nothing is stored twice, and every analysis ever written answers the
new reads.

### A big document is decompressed once per expression (10.12)

The split above left one claim untested, and Step 10.12 tested it. The summary
form originally selected `jsonb_array_length(document->'pitch_points')` beside
`document - 'pitch_points'`, on the reasoning that the second expression read a
document PostgreSQL had already decompressed for the first. It does not. A
value too big to sit in the row lives in the TOAST table, and PostgreSQL fetches
and decompresses it **once per expression that references it** — not once per
row. Measured on the same five-minute recording:

| Selected | Time | Buffers |
| --- | --- | --- |
| `document - 'pitch_points'` | 1.80 ms | 37 |
| `jsonb_array_length(document->'pitch_points')` | 2.31 ms | 37 |
| both, as 10.11 shipped them | 5.70 ms | 109 |

The buffer count is the mechanism made visible: each reference reads the whole
toasted value again. This has one consequence per site.

**The summary read gets a column.** `pitch_point_count` (migration 0005) sits
beside the document, written from the same object in the same statement as
`status` and `feedback_status`, and backfilled once for the documents written
before 10.11 — which carry no count of their own and were the reason the
expression existed at all. `GET /audio-analysis` 11.9 ms → 8.8 ms. This *is* a
schema change, and it is the narrow kind the layout already uses: a column that
projects one value out of the authoritative document so a query need not open it.

**The progress query stops multiplying.** It read eleven scalars by path out of
`a.document`, so a 30-recording window decompressed each document eleven times.
Projecting `document->'metrics'` inside the lateral pays once and leaves ~600
bytes for the scalars to come off: **593 ms and 11 834 buffers → 30 ms and
1 172**, and `GET /recordings/progress?limit=30` **535.1 ms → 39.3 ms** end to
end, byte for byte the same response. No migration, and no metric denormalised.

Both are pinned by tests that count how many times a statement reads `document`,
because the cost is invisible in the results: a query that decompresses a
document eleven times returns exactly what one that decompresses it once returns.

### A page of history says whether it is the whole history (10.13)

Two steps of read-path work asked how *expensive* the answers were. This one
asked whether they were **complete**, and one of them was not. Measured against a
real server: an owner with 137 recordings asked for their history, was sent 50,
and got no field in the response that could say the other 87 existed. The browser
rendered those 50 under the words "Everything you have uploaded from this
browser" and announced "50 recordings" to a screen reader. Past `limit=200` — the
maximum — the older ones were unreachable at any URL. Nothing documented that; it
was a gap, not a decision.

The response gains `next_cursor` and the endpoint takes a `cursor`.

**Whether more exists is established, not inferred.** The query asks for
`limit + 1` rows; the extra one is dropped and its arrival is the answer.
Comparing `count` against `limit` cannot answer the question — an owner with
exactly 50 recordings and an owner with 500 both return 50 — and that guess is
what the contract suite's "exactly a page's worth" test exists to catch.

**Keyset, not offset**, for two reasons that were both measured on one owner with
5 000 recordings:

| Asking for the 81st page of 50 | Time | Rows read |
| --- | --- | --- |
| `(created_at, id) < (…)` | 0.127 ms | 51 |
| `OFFSET 4000` | 2.705 ms | 4 050 |

Offset's work grows with depth; keyset's does not — over HTTP, page 1 is 6.5 ms,
page 25 is 6.6 ms and page 100 is 5.7 ms. And an upload between two requests
cannot shift the window, where an offset would push row 50 down to 51 and begin
page two by repeating it.

**The cursor carries the query's whole ordering**, `created_at` *and* the
recording id, because `recordings_owner_created_idx` orders by the first and the
tie-break is the second; a cursor holding only a timestamp loses or repeats every
recording created in the same instant.

**It is opaque, not secret, and it is not a scope.** Ownership stays in the
`WHERE` clause, so a cursor from somebody else's history selects a different
slice of *your* recordings and reaches nothing of theirs. A cursor this server
did not issue is a `VALIDATION_ERROR` rather than an empty page: answering "you
have no more recordings" to a damaged bookmark would be the same untruth the step
removes.

On the client, what is loaded and whether more exists are two separate facts in
`lib/history.ts`, folded by a pure function and tested without React — the
arrangement `analysis-runner.ts` uses for polling. The screen states both and
invents no total, because nothing counts one.

### What concurrency does to a read (10.14)

Every number in 10.11–10.13 was taken one request at a time, and the roadmap said
so. Step 10.14 took them again with several requests in flight — against the same
real server, one worker, 30 five-minute recordings, each a 1 685 kB document of
12 931 points. Two things came out of it, and only one was a surprise.

**Most reads scale.** The history list, the progress query, the identity panel,
the speech read and the polled audio summary all hold their throughput as
concurrency rises: `GET /recordings` serves 113 requests a second at c=1 and 154
at c=16, and the rest behave the same way. Nothing there needed changing and
nothing there was changed.

**The three reads built on the pitch timeline do not scale at all.**

| Read (c=1 → c=16) | p50 | p95 at c=16 | requests/second |
| --- | --- | --- | --- |
| `GET …/audio-analysis` (polled) | 12.0 ms | 219 ms | 83 → 114 |
| `GET …/audio-analysis/pitch` | 172.9 ms | 2 279 ms | 7.1 → 8.5 |
| `GET …/audio-analysis/notes` | 134.7 ms | 2 016 ms | 7.5 → 9.3 |
| `GET …/audio-analysis/key` | 136.5 ms | 2 088 ms | 7.3 → 8.6 |

Sixteen concurrent readers get sixteen times the latency and the same
throughput, which is the signature of work that cannot overlap. Profiling one
read says where it goes: of ~80 ms, **23 ms is SQL and transfer, 20 ms is
`json.loads`, and 30 ms is pydantic building 12 931 `PitchPoint` models.** The
folds those points exist for cost 3.3 ms (notes) and 1.4 ms (key). All of it
runs on the event loop holding the GIL, so it is not one slow request — it is
every other request in the process waiting.

That is measurable from the outside, and it is what a user would actually feel.
While one other client had an analysis page open, the poll the browser makes
while a measurement runs went from **14.2 ms to 301.0 ms**; with three, to
**965.1 ms**.

**The fix is to stop materialising a timeline nobody asked for.** `/pitch` has
returned at most `max_points` points since Step 7I — 995 of 12 931 by default —
and it was building all 12 931 to slice the list. PostgreSQL selects the sample
now, by ordinality, with the stride computed from the stored `pitch_point_count`
in the same statement:

| | PostgreSQL | Sent | API process | End to end |
| --- | --- | --- | --- | --- |
| whole document, sliced in Python | 21.4 ms | 1 685 kB | ~50 ms | 172.9 ms |
| every 13th, selected in SQL | 19.1 ms | 133 kB | ~4 ms | 38.6 ms |

**PostgreSQL's half does not get cheaper — that is the point.** It detoasts and
parses the same document either way. What changes is that the ~50 ms leaves the
event loop, where it was serialising every other request, and stays in a backend
process per connection, where two requests can do it at once. Throughput follows:
7.1 → 25.9 requests a second at c=1, 8.5 → 42.8 at c=16, and p95 at c=16 from
2 279 ms to 436 ms. The poll during one open dashboard improves from 301.0 ms to
169.6 ms.

The response is unchanged, point for point — a test asserts the sample equals the
whole timeline sliced — and so is the type discipline 10.11 established: a
`DecimatedTimeline` carries an `AudioAnalysisSummary`, so a record holding 995 of
12 931 points has no path to a write.

**What is left is not waste, and is written down rather than fixed.** `/notes`
and `/key` fold every point, so they still load every point: ~135 ms and ~7
requests a second, unchanged by this step. Roughly 25 ms of that is the floor for
reading a 1 685 kB document at all; the remaining ~50 ms is model construction
that only a different input type would remove, and moving it to a thread would
not help, because `json.loads` and pydantic validation both hold the GIL. Two
options exist — read the two or three fields the folds use as flat arrays
(measured at 15.4 ms in PostgreSQL, and it changes the signature of two
measurement functions), or store the derived answers, which 10.8 rejected on
purpose so that every analysis ever completed stays answerable. Neither is chosen
here.

### What a fold actually reads (10.15)

Step 10.14 left the two aggregations where it found them and named the two ways
out. Step 10.15 took the first: **the fields, not the frames.** The second — 
storing the answers — stays rejected for the reason 10.8 gave, which this step
did nothing to weaken: derive it on read and every analysis ever completed is
answerable; store it and only the ones measured afterwards are.

`/notes` and `/key` are not the graph. The graph returns a sample, so it may read
one; a note breakdown of every thirteenth frame is a breakdown of a different
recording, so both of these must see every frame that was measured. What they
need not see is the *frame*. A stored point carries six fields and between them
they read two:

| Fold | Reads | Ignores |
| --- | --- | --- |
| `summarise_notes` | `midi_note`, `cents` | `timestamp_seconds`, `frequency_hz`, `note_name`, `confidence` |
| `pitch_class_profile` | `midi_note` | the other five |

`PitchFields` is that pair, as two arrays — one array per field rather than one
object per frame — and `TimelineFields` is a record with its fields attached,
which is the fourth form of an audio-analysis read:

| Type | Carries | Used by |
| --- | --- | --- |
| `AudioAnalysisSummary` | no timeline, plus `pitch_point_count` | the summary and feedback routes, `start`, the claim, the sweep |
| `DecimatedTimeline` (10.14) | a summary, plus every `n`-th point | `/pitch` |
| `TimelineFields` (10.15) | a summary, plus two fields of **every** frame | `/notes`, `/key`, `/recordings/compare` (10.16) |
| `AudioAnalysis` | a summary, plus whole frames | **every write**, and nothing else |

The type discipline is the same one 10.11 established and is why none of this can
be misapplied: each of the three read forms carries an `AudioAnalysisSummary`,
and `update` takes an `AudioAnalysis`, so a record read for folding has no path
to a write. `PitchFields` has no timestamps in it at all, so nothing folded from
one can be drawn or returned as points either.

PostgreSQL projects the two fields with `array_agg` over a single
`jsonb_array_elements` expansion — two aggregates, one walk, so the statement
touches `document` exactly twice, which is the 10.12 rule. Measured against a
real server on one worker, on a five-minute recording of 12 931 points stored as
a 1 722 kB document, through the repository methods themselves:

| | Sent | Repository read | of which PostgreSQL |
| --- | --- | --- | --- |
| whole document, frames built | 1 722 kB | 106.2 ms | ~21 ms (10.14) |
| two fields, as arrays | 130 kB | 27.3 ms | 18.3 ms |

PostgreSQL's half is what it always was — it detoasts and parses the same
document either way, as 10.14 found for the graph. What changes is the ~85 ms
the API process no longer spends turning 1 722 kB into 12 931 validated models
on the event loop, in front of every other request.

End to end over HTTP, with the response **byte for byte identical** — asserted
against the previous build, running beside this one against the same database:

| Read (c=1) | before | after | | c=16 p95 | before | after |
| --- | --- | --- | --- | --- | --- | --- |
| `GET …/audio-analysis/notes` | 151.2 ms | **37.3 ms** | | | 2 321.8 ms | **273.2 ms** |
| `GET …/audio-analysis/key` | 143.3 ms | **38.8 ms** | | | 1 784.7 ms | **246.4 ms** |

Throughput is the measurement that says the work left the event loop: `/notes`
goes from 7.9 to 25.6 requests a second at c=1 and **8.4 to 64.7 at c=16**,
`/key` from 7.3 to 25.4 and 10.0 to 71.4. A read that did not scale scales.
`GET /audio-analysis` and `/pitch` are unchanged at every level, which is what a
control is for.

What a user would feel is the poll behind somebody else's page. With three
clients reading a note breakdown, the poll the browser makes while a measurement
runs went from **212.5 ms to 33.0 ms**; idle it is 12.8 ms either way.

Two smaller decisions inside this, both measurable:

- **The folds got cheaper as well as the reads.** `summarise_notes` measured
  3.3 ms over frames and 2.0 ms over fields, and the key 1.4 ms against 0.9 ms:
  reading `point.midi_note` 12 931 times is attribute access on a pydantic model,
  and reading `midi_notes[i]` is not.
- **A note's name is derived, not read.** `summarise_notes` needs a name per
  *note*, not per frame, and the name is a function of the number — the analyzer
  writes it with `note_name_for_midi` from the same integer. Sending 12 931
  strings so the fold can avoid ~40 calls would be paying to carry an answer it
  can compute. The property that makes this sound is asserted twice: the two
  naming entry points agree for all 128 MIDI notes, and every point the real
  analyzer writes is named by its own number.

Bounds did not go with the frames. `PitchFields` checks that every note is a MIDI
number and every deviation within ±50 cents, exactly as `PitchPoint` does, and
that the two arrays are the same length — 0.83 ms for 12 931 frames. The models
were never what made the check possible, only what made it expensive.

**What is left, measured rather than assumed.** `GET /recordings/compare` is now
the slowest read in the product: **253.5 ms at c=1, 3 152.9 ms p50 at c=16 and
3.8 requests a second**, because it loads *two* whole documents and folds both
note breakdowns from whole frames. It is the same defect this step removed from
`/notes`, in the one statement that reads two recordings at once, and it is
untouched here — the fix is now a change to `COMPARISON_SOURCES_SQL` and the
type `ComparisonSource` carries, not a new idea. Also still true: everything has
been measured on **one worker**, and the speech pipeline and the progress query
have not been measured at a hundred times the data. Step 10.16 took the
comparison; the two sentences after it still stand.

### The same fix, on the read that does it twice (10.16)

10.15 ended by naming its own successor, and 10.16 is that and nothing more:
`GET /recordings/compare` was the last read building pitch frames it did not
need, and it built *two* recordings' worth. There is no new idea in this step —
the type, the projection and the rule about how many times a statement may touch
a document all come from 10.11 through 10.15. What is new is that the statement
which reads two recordings at once now obeys them.

A comparison needs three things about each side: the recording's identity, the
analysis record, and a note breakdown. Only the third involves the timeline, and
it is the one read in the product that may *not* be given a sample — a breakdown
of every thirteenth frame is a breakdown of a different recording — so both
sides are still read whole. Whole, as fields:

| | Sent | Repository read | of which SQL and transfer |
| --- | --- | --- | --- |
| two whole documents, frames built | 2 916 kB | 122.1 ms | 44.3 ms |
| two sides, as fields | 209 kB | **21.7 ms** | 18.7 ms |

Measured in one process against the same two rows — a five-minute recording each,
12 931 points, a 1 609 kB document — with the pre-10.16 statement kept beside the
new one so both are timed on the same data rather than across two runs. About
78 ms of that difference is model construction leaving the event loop: 25 862
`PitchPoint` models were being built to read two fields off each.

End to end over HTTP, the previous build running beside this one against the same
database:

| `GET /recordings/compare` | before | after |
| --- | --- | --- |
| p50, one client | 137.5 ms | **36.0 ms** |
| p95, one client | 173.7 ms | **42.1 ms** |
| p50, sixteen clients | 2 160.3 ms | **239.9 ms** |
| p95, sixteen clients | 2 577.3 ms | **304.1 ms** |
| requests a second, sixteen clients | 7.3 | **63.1** |

Throughput is again the measurement that says the work left the event loop: 7.3
requests a second at c=16 against 7.4 at c=1 is the signature of work that cannot
overlap, and 63.1 against 27.9 is the signature of work that can. `/notes` and
`GET /audio-analysis` were measured on both builds as controls and did not move.

What a user would feel is somebody else's comparison. With three clients holding
one open, the poll the browser makes while a measurement runs went from **303.6 ms
to 35.9 ms**; idle it is 6.6 ms before and 8.4 ms after.

**The response is identical, and that was checked rather than reasoned about.**
Both builds were asked for the same comparison of the same two recordings and
returned the same 14 478 bytes — 24 notes and 7 metrics — byte for byte.

Two decisions inside this are worth recording:

- **The aggregates are shared with the `/notes` read, not copied.** Both
  statements project the same two fields, and a copy could have ordered its two
  `array_agg`s differently — which would attribute real deviations to the wrong
  notes and produce a breakdown that looks entirely reasonable. There is one
  `PITCH_FIELD_AGGREGATES`, and a test asserts both statements use it.
- **The latest analysis is chosen before its timeline is expanded.** The
  comparison picks one analysis id per side and expands that row alone. Written
  the natural way — the aggregate inside the subquery that orders and limits —
  it would have walked the timeline of every analysis a re-analysed recording
  has, and thrown all but one away.

**What is left.** No read stands out any more. Measured on the new build at the
same 12 931-point recording, the four timeline-derived reads sit within one band
— compare 29.8 ms, `/key` 21.5 ms, `/notes` 26.4 ms, `/pitch` 24.8 ms at one
client, and 64 to 109 requests a second at sixteen — while the three that never
touch a timeline (`GET /audio-analysis`, the history list, the progress query)
are ~6 ms and ~210 to 250 requests a second. The slowest of the four by
throughput is now `/pitch`, at 64.2 requests a second and 298.9 ms p95 at c=16,
and what it spends its time on is the sample it genuinely returns. Unchanged from
10.14 and 10.15, and not addressed here: everything has been measured on **one
worker** — 10.17 took the same reads across several — and the speech pipeline
and the progress query have not been measured at a hundred times the data.

### What several workers do to a read (10.17)

Every number in 10.11 through 10.16 was taken against **one uvicorn worker**,
and each of those steps said so in its own closing paragraph. 10.10 had already
made several workers a supported shape — it moved the rate-limit counter into a
table for exactly that reason — so the gap was not hypothetical, it was the
shape the previous six steps had never been measured in. This step measures it.

Against the same rig those steps used — 30 five-minute recordings, 12 931 points
each, one PostgreSQL on the same four-core host — every read at sixteen
concurrent clients, in requests a second:

| read | 1 worker | 2 workers | 4 workers |
| --- | --- | --- | --- |
| `GET /recordings` | 196.0 | 414.8 | **553.8** |
| `GET /audio-analysis` | 161.8 | 269.8 | **355.4** |
| `…/key` | 95.7 | 139.1 | **161.7** |
| `GET /recordings/progress` | 95.5 | 132.7 | **135.1** |
| `…/notes` | 81.1 | 133.2 | **148.7** |
| `GET /recordings/compare` | 63.8 | 93.6 | **97.8** |
| `…/pitch` | 55.3 | 103.6 | **128.5** |

**They scale, and the scaling stops where the cores do.** The second worker is
worth 1.4× to 2.1× on every read; the third and fourth together are worth 1.05×
to 1.35× more, because by then four API processes and PostgreSQL are sharing
four cores. Nothing needed changing for that — after 10.15 and 10.16 the reads
are mostly PostgreSQL's work plus a small fold, and work that already left the
event loop parallelises across processes for free. What a user would feel is the
same shape: with three clients holding a note breakdown open, the poll the
browser makes while a measurement runs is 31.0 ms on one worker and **12.2 ms**
on four.

**The defect several workers exposed is not in a read. It is in the pool.**
`db/pool.py` opened up to ten connections with a comment saying "this is one API
process serving a handful of concurrent requests" — written when it was true,
and left alone when 10.10 made it false. Ten is not the number PostgreSQL sees.
`workers × 10` is, and a default server grants 97 (`max_connections` 100, less
three reserved for superusers). Measured, at twelve workers under load:

| | connections held | `psql` can connect | `too many clients` in the log |
| --- | --- | --- | --- |
| pool of ten, twelve workers | **97 of 97** | **no** | yes, continuously |
| pool of four, twelve workers | 21 (ceiling 48) | yes | none |

The API's own requests did *not* fail in the first row, and that is what makes
it worth writing down: psycopg queues a caller that cannot get a connection, so
from the outside the symptom was latency, not an error. What broke was
everything else — a thirteenth worker could not boot, `python -m
app.db.cleanup_identities` could not run, and `psql` was refused at the socket.
The database had been taken over by one application's idle capacity.

The same load through the fixed default serves **82.8 requests a second against
87.4**, which is the whole argument: the ceiling that took the server down was
never being used for anything.

**Four, because four is what one worker can use.** The pool size was swept at
one worker, sixteen concurrent clients, two rounds each:

| pool | `…/notes` | `GET /recordings/compare` |
| --- | --- | --- |
| 10 | 85.0, 83.1 req/s | 66.2, 66.4 req/s |
| 6 | 85.4, 85.8 req/s | 65.5, 64.1 req/s |
| 4 | 84.7, 87.4 req/s | 62.3, 60.2 req/s |
| 2 | 70.1 req/s | 49.2 req/s |
| 1 | 44.8 req/s | 30.8 req/s |

Below four it costs real throughput; at four it costs the comparison read about
7% and everything else nothing, and that 7% is the only thing given up here. It
is given up on a single-worker deployment at sixteen concurrent clients, and it
buys room for twenty-four processes where ten left room for nine. At four
workers even that disappears: a pool of four and a pool of ten are the same
throughput, on 16 connections against 40.

Three things carry the decision so it does not have to be remembered:

- **`DB_POOL_MAX_SIZE` and `DB_POOL_MIN_SIZE` are configuration**, because a
  per-process share of a server-wide limit is exactly the kind of number that
  depends on a deployment. `min_size` above `max_size` is refused at startup
  rather than clamped.
- **The startup log reports the arithmetic**: `database_pool_opened` carries the
  pool size, the server's `max_connections` and `processes_that_fit` — how many
  processes of this size the server it is pointed at has room for. A process
  cannot know how many siblings uvicorn started, so it reports the division
  rather than enforcing it. A pool too large to fit *even once* is impossible
  rather than ambitious, and is refused the way 10.10 refuses
  `RATE_LIMIT_BACKEND=database` with no `DATABASE_URL`.
- **Connections say who holds them.** Every connection sets `application_name`
  — `vocallens-api`, or `vocallens-maintenance` for the one-off jobs — so
  `pg_stat_activity` on a server that has run out is a list of culprits rather
  than a list of rows. The maintenance jobs also ask for **one** connection
  each: they are sequential, and the moment they matter is the moment the API's
  workers are holding everything else.

**What is left.** The rate-limit counter must still be moved to `database` by
hand when a deployment scales, exactly as 10.10 documented — this step changed
nothing about that, and a multi-worker deployment left on `memory` still gets
one counter per worker. The speech pipeline and the progress query at a hundred
times the data were still unmeasured here; 10.18 measured them. And every number
above was taken on **one host**, with PostgreSQL sharing its four cores with the
workers, so the point at which scaling flattens belongs to this rig rather than
to the code.

### What a hundred times the data does to a read (10.18)

Every step from 10.11 to 10.17 closed by naming the same two unmeasured things:
the speech pipeline and the progress query at a hundred times the data. This
step measured them, against one owner holding **5 000 five-minute recordings** —
each with a completed audio analysis of 12 931 points and a completed speech
analysis, a 1 131 MB `audio_analyses` table — beside the 50-recording rig
10.12 used.

**Both are flat, and so is everything else that was worried about:**

| read, one client | 50 recordings | 5 000 recordings |
| --- | --- | --- |
| `GET …/analysis` (the speech read) | 6.2 ms | 6.1 ms |
| `GET /recordings` | 6.4 ms | 6.8 ms |
| `GET …/audio-analysis` | 7.3 ms | 7.7 ms |
| `GET /recordings/progress` | 28.9 ms | 28.0 ms |
| `GET /identity` | 6.1 ms | **12.0 ms** |

Flat is the expected answer and it is worth stating why it is not luck: every
one of those reads is bounded by an index on `(owner_id, …)` or by a primary
key, and 10.13 had already shown the history list flat with *depth* at 5 000
recordings. A hundred times the rows changes nothing about how many of them a
query touches.

**What is not flat is the window, and the window is a parameter.**
`GET /recordings/progress` takes `limit` up to 200, and at 200 it cost
**155.5 ms**. That is not the database being large; it is 200 rows each
decompressing a 253 kB document to read about 600 bytes of metrics out of it.
10.12 had left exactly this and said so in the module and in the migration:

> What remains is one unavoidable decompression per row, which is the cost of
> keeping the metrics in the same document as the timeline. Denormalising them
> into columns would remove that too, and is still a schema change worth making
> only when a measurement demands it — this one did not.

This one does. Migration 0006 adds

```sql
ALTER TABLE audio_analyses
    ADD COLUMN metrics JSONB GENERATED ALWAYS AS (document -> 'metrics') STORED;
```

and the lateral selects that column instead of computing the projection. The
statement no longer mentions `document` at all, which is the strongest form of
the rule 10.12 introduced — not "touch it once per row" but "do not touch it".

| in PostgreSQL, window of 200 | time | TOAST reads |
| --- | --- | --- |
| `document->'metrics'` | 141.1 ms | 7 127 |
| the `metrics` column | **1.7 ms** | **262** |

End to end over HTTP, the previous build running beside this one against the
same 5 000-recording database:

| `GET /recordings/progress` | before | after |
| --- | --- | --- |
| default window (30), one client | 28.7 ms | **7.7 ms** |
| `limit=200`, one client | 155.5 ms | **19.4 ms** |
| default window, p50 at sixteen clients | 152.1 ms | **87.2 ms** |
| default window, requests a second at sixteen | 95.6 | **161.9** |
| `limit=200`, p50 at sixteen clients | 731.5 ms | **303.4 ms** |
| `limit=200`, requests a second at sixteen | 21.6 | **53.4** |

**The response is identical, and that was checked rather than reasoned about**:
both builds were asked for the same 200-recording window and returned the same
bytes.

**Generated, not written.** `pitch_point_count` (10.12) is a column the
repository fills, and it stays honest only because a rule — *write it from the
same object in the same statement* — is stated in three places and followed. It
had already been got wrong once, by a `model_copy(update=…)` that skipped the
validator deriving it. This column has no such rule to break: PostgreSQL
computes it from the document in the same write, no INSERT mentions it, there is
no backfill, and an INSERT that tries to give it a value of its own is rejected
by the server. A test asserts each of those.

Two costs are real and are recorded rather than glossed:

- **The migration rewrites the table.** A generated column cannot be added with
  a default, so every row is rewritten: **18 seconds** for the 1 131 MB table
  here. Migrations run at startup under an advisory lock, so a deployment with a
  large `audio_analyses` should expect one boot to take that long.
- **The column is a second copy of ~600 bytes per row** — 4 MB across 5 000
  recordings, against the 1 131 MB the documents already occupy. It is a
  physical projection maintained by the database, not a denormalised
  *measurement*: nothing derives a number and stores it, so 10.8's rule that
  every analysis ever completed stays answerable from its document is untouched.

**What is left.** `GET /identity` is now the only read whose cost grows with how
much one owner has: 6.1 ms at 50 recordings and 12.0 ms at 5 000. It answers
"what would I lose?" with three counts, and it gets them by joining every
recording to every analysis and sorting the result for three `count(DISTINCT)`s —
9 ms of the 12 is that sort. It is recorded here rather than fixed, exactly as
10.15 recorded the comparison read; 10.19 fixed it, and found that one of the
three counts had been answering a different question from the one the screen
asks.

### The last read that grows with a history, and what `psql` got wrong (10.19)

10.18 ended by naming `GET /identity` as the only read whose cost grows with how
much one owner holds, and recorded it rather than fixing it. This step is that
fix, and the defect it turned up on the way is worth more than the milliseconds.

**The number was wrong.** The identity panel's third count, `ai_feedback`, was
`count(DISTINCT a.id) FILTER (WHERE a.feedback_status = 'completed')` — a count
of *analyses*. Both places it reaches a reader are sentences about recordings:

> This key holds 5 recordings, 3 measured, **2 with generated feedback**.

> This permanently deletes 5 recordings, every measurement taken from them, and
> the stored audio itself. **2 of them** carry generated feedback, which cannot
> be recovered.

A recording may be analysed more than once, and feedback may be generated on
more than one of those runs. One recording with two feedback runs made the first
sentence add a fourth item to a list of three, and the second sentence say that
one recording included two of them. It is the same rule the rest of the product
is built on — do not state what the data does not support — applied to a count
instead of a measurement, and it is the same defect 10.13 found in a list.

All three numbers count **recordings** now, and cannot exceed one another.

**The two stores disagreed, in two different ways, and nothing had asked.** SQL
counted feedback *runs*; the in-memory double counted recordings whose **latest**
analysis carried feedback. The double was wrong about the second number too: a
recording measured yesterday and re-analysed a minute ago read as unmeasured
while the new run was pending, because it looked at the newest analysis instead
of all of them. Two contract tests now hold both stores to the same answer for
the shape that separates them — a recording analysed three times — and both
failed before this step, differently.

**The rewrite, and the measurement that chose it.** Folding each recording's
analyses to two booleans in a lateral leaves the outer query one row per
recording to count, so no `DISTINCT` is undoing a multiplication the statement
just performed. Measured through a pooled connection against an owner holding
5 000 recordings in a database of 201 owners:

| form | 5 000 recordings | 25 recordings |
| --- | --- | --- |
| join, three `count(DISTINCT …)` (before) | 16.1 ms | 0.32 ms |
| **lateral `bool_or`** (after) | **11.7 ms** | 0.31 ms |
| three counts with `EXISTS` | 20.1 ms | 0.71 ms |

End to end over HTTP, both builds against the same database: `GET /identity`
**23.6 → 19.6 ms** at one client. At sixteen concurrent clients it does not move
(128.9 against 128.8 requests a second): the saving is in a PostgreSQL backend,
and under that much concurrency the endpoint is waiting on other things.

**`psql` ranked those three the other way round, and `psql` was wrong.** With
the owner id written into the statement as a literal, PostgreSQL plans the query
afresh every time and can use the owner's real selectivity. A pooled application
connection does not get that: psycopg prepares any statement it runs five times,
and the plan is then chosen once for a parameter it has not seen. Under a custom
plan the lateral is the *slowest* of the three (15.9 ms against 11.5 for the
shipped join); under the prepared plan it is the fastest. Timing a query in
`psql` and shipping the winner is therefore not a valid method here, and this is
the first step in the sequence where it would have produced the wrong answer.

Preparing is not itself the problem, and turning it off would be a bad trade —
measured on the same connection, custom plan against prepared:

| statement | custom | prepared | |
| --- | --- | --- | --- |
| the identity summary | 10.5 ms | 16.6 ms | **1.59× worse** |
| the timeline fields (`/notes`) | 14.3 ms | 9.9 ms | 0.69× |
| the history list | 0.73 ms | 0.54 ms | 0.74× |
| the progress window of 30 | 1.50 ms | 1.23 ms | 0.82× |

Every statement in the product is *faster* prepared except this one, which is
why the fix is a query whose plan does not depend on knowing the owner rather
than a pool setting. What made this one different is that its cost is dominated
by how much the owner has — from 25 rows to 5 000 in the same database — which
is exactly the thing a plan chosen for an unknown parameter cannot know.

### A policy on responses that are data (10.21)

10.20 ended with a table of four outstanding items, each said to be waiting on a
decision. One of them was not: "a Content-Security-Policy on API responses",
whose stated reason was that `limitations.md` recorded its absence. A record is
not a decision, and nothing about this one needs a product answer.

10.5 put three security headers at the edge and 10.9 gave the web app a
nonce-based policy. Responses from `/api/` had neither of their own. What stood
between an API response and a browser treating it as a document was
`X-Content-Type-Options: nosniff` alone — sound, but a single line with nothing
behind it.

Every response the API sends now carries:

```
default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; sandbox
```

Every directive says the same thing a different way: a response that is data has
no business loading, framing, submitting or executing anything. `sandbox` is the
one that matters if the others are bypassed — it applies only when a browser
*does* treat the response as a document, and it then gives that document a
unique origin with no scripts, no forms and no plugins. Unlike the web app's
policy this one is a constant, and for the opposite reason: there is no nonce to
mint, because nothing here is a document with scripts in it.

**Set on the response, in the application, not at the edge.** The policy then
travels with the API to any deployment, is exercised by the test suite rather
than by a proxy nobody runs locally, and reaches responses no route produced —
the `FILE_TOO_LARGE` refusal `MaxBodySizeMiddleware` writes before routing, and
a 404. The middleware is registered last so it runs outermost, which is what
makes that true. nginx still sets no policy of its own, so the two can never
intersect into one nobody designed; `test_deployment.py` has asserted that since
10.9 and now says why twice.

**What it buys was measured, not asserted.** Framing an API response from a page
carrying no policy of its own, in Chromium:

| | iframe |
| --- | --- |
| before | **loaded**, no refusal, no policy header |
| after | **refused** — "an ancestor violates the following Content Security Policy directive: `frame-ancestors 'none'`" |

Through the shipped proxy that was already refused, by `X-Frame-Options: DENY`
from 10.5. What changed is that it no longer depends on the edge: the
application refuses on its own, wherever it is run.

**Verified in a browser, as 10.9's precedent requires.** The real web app was
loaded against an API sending the policy: five API calls, all `200`, **0 CSP
violations, 0 console errors, 0 page errors**, and the page rendered. `/docs`
and `/redoc` were checked for refusals too and had none — they fail to load
Swagger UI and ReDoc *in this sandbox* because its egress proxy blocks
`cdn.jsdelivr.net`, which is a network error rather than a policy one, and the
distinction was checked rather than assumed by listening for
`securitypolicyviolation` and finding zero across all five pages.

The exemptions are read from the application — `app.docs_url`, `app.redoc_url`
and the Swagger OAuth redirect — rather than written into the middleware, so
moving or disabling a documentation UI cannot leave a stale path exempting
nothing.

### Concurrency belongs to the database

Until Step 7M both orchestrators guarded their find-or-create decision with a
process-local `asyncio.Lock`. That could only ever serialise coroutines inside
one interpreter — a second worker process reopened the race, and the lock's own
docstring said so. It is gone. Three database properties replace it:

| Invariant | How it holds |
| --- | --- |
| One analysis per recording at a time | Partial unique index on `recording_id` where the status is non-terminal |
| A stale worker cannot overwrite a finished result | Every `UPDATE` carries the status the caller last read; zero rows affected means it lost |
| Feedback generation happens once | A single conditional `UPDATE ... RETURNING` that moves `feedback_status` and returns the row only to the caller that moved it |

All three hold across processes and machines. The read that precedes an insert
is an optimisation; the index is the guarantee, and the caller that loses the
race is handed the winner's record rather than an error.

### Ownership

Every recording has an owner, and **every repository read is scoped by owner id
in SQL** — another owner's recording is never selected, rather than selected and
filtered. Authorisation that happens in a `WHERE` clause cannot be bypassed by a
client, and no frontend is trusted to hide anything. A recording belonging to
somebody else answers `404`, identical to one that does not exist, because a
different answer would confirm an id is real to somebody with no right to know.

An owner is a row, and the ways in to it are rows in `credentials`. Every one of
them is a server-generated bearer key: no password, no email, no session, no
login. **This is ownership, not authentication**, and it is documented that way
rather than dressed up as more. Keys are stored SHA-256-hashed, which is
appropriate precisely because they are 128-bit random values rather than
human-chosen secrets. See `app/services/owners/credentials.py` for the limits,
stated plainly.

### Credentials belong to an owner (10.2)

Until 10.2 an owner *was* a key — one column, `owners.token_hash`. That meant an
identity could not have a second way in, could not label the ways it had, and
could not revoke one without losing everything it owned.

A credential now **belongs to** an owner. Several per identity, each named, each
revocable, all resolving to the same `owner_id` that already owns the
recordings. The migration copies every existing hash into `credentials` and
drops the column in the same transaction, so every key that worked before works
after — and a dead column that used to be the identity, still carrying a UNIQUE
index, is not left behind to be read as authoritative later.

Three rules the code enforces rather than documents:

- **Resolution is an indexed lookup of a hash.** No credential is ever compared
  in Python, so there is no secret-dependent branch whose timing could leak.
- **The last credential cannot be revoked**, under a row lock on the owner — two
  concurrent revocations cannot strand an identity between them. Somebody who
  wants their data gone deletes the identity, which is honest and different.
- **A credential belonging to somebody else is "not found", not "refused"**, so
  the endpoint cannot be used to discover that an id is real.

### Identity retention (10.6)

Step 10.3 measured the problem and 10.5 did not solve it: an unauthenticated
request mints an owner, so a crawler, a probe or one curious visit leaves two
rows behind. Nothing ever reclaimed one. Four of the five owners in the
development database had never uploaded anything.

**An identity is reclaimable only when both hold: it owns no recordings, and it
has not been seen for the retention period.** The first condition is what makes
this safe rather than a judgement call — deleting an *empty* identity is
invisible to whoever held it, since their next request mints a fresh one and
what they lost was nothing. Deleting an identity that owns recordings destroys
somebody's singing history, and **no retention requirement for that exists
anywhere in this repository**, so it is not done at any age.

**The retention period is configuration, not a constant.** Nothing specifies
one, so `IDENTITY_RETENTION_DAYS` defaults to 30 and that default is recorded as
a choice. A wrong value is cheap for the reason above.

**Age is the wrong signal, so it is not the signal.** `owners.created_at` says
when somebody arrived; reading a history writes nothing, so an identity created
a year ago can be in daily use. Migration `0003` adds `owners.last_seen_at`,
written when a credential resolves and throttled to at most one write per
`IDENTITY_ACTIVITY_THROTTLE_SECONDS` — otherwise recording activity would become
a write on every read, the objection 10.3 raised against a database-backed rate
limiter. Existing rows were backfilled from the newest thing the owner
demonstrably did, because inventing `now()` would make everyone look active and
inventing `created_at` would make long-lived identities look abandoned.

**Concurrency.** The candidate query is advisory; every candidate is re-checked
under `SELECT … FOR UPDATE SKIP LOCKED` in the same transaction that deletes it.
`touch` updates the same row, so a returning user either lands before the claim
— and the re-check refuses — or waits until after the owner is gone. Two cleanup
runs serialise the same way, and `SKIP LOCKED` means the loser moves on. The
"owns no recordings" predicate sits in the same statement as the delete, so this
path cannot remove an owner whose audio is still on disk: the files-before-rows
invariant `OwnerDeletionService` protects is preserved by never being engaged,
and the service asks that service for a second opinion before touching anything.

**It is an operational command, not an endpoint**: `python -m
app.db.cleanup_identities`, alongside `import_filesystem`. A route would be
either unauthenticated — letting a stranger drive deletion — or would require a
caller to name an owner, which no API in this project permits. There is no
scheduler in this repository and this step deliberately did not add one; the
command is idempotent, bounds its work with `--limit`, and exits non-zero if
anything failed.

Measured at 50 000 owners with 45 542 eligible: the candidate query takes
**1.20 ms** using `owners_last_seen_idx` and reads 519 rows; with index scans
disabled it takes **16.19 ms** and reads 47 944 rows plus a sort. The gap grows
with the table, which is the failure this feature exists to prevent.

### Deployment topology (10.5)

```
client ──▶ proxy :80  ──▶ frontend :3000
   (the only published port)  └▶ backend :8000 ──▶ db :5432
```

**The proxy is the only public entry point.** Before 10.5 the compose file
published three ports: the frontend, the API, and **PostgreSQL itself** with a
default password — which put every recording one connection away from anyone who
could reach the host, bypassing every ownership predicate in the API. All three
are now internal; only nginx publishes.

That is not tidiness, it is what makes `RATE_LIMIT_TRUSTED_PROXIES=1` sound.
Measured during this step against the real stack, with a limit of two
identities: five requests sent **directly** to the backend carrying a forged
`X-Forwarded-For` created five identities, while the same five through the proxy
created none. The backend trusts the header because the only thing that can
reach it is the proxy. Republish its port and that stops being true.

The proxy **sets** `X-Forwarded-For` to `$remote_addr` rather than appending
with `$proxy_add_x_forwarded_for`, so nothing the client sent is carried
through. One entry written by one trusted hop is exactly the one entry the
backend is configured to trust.

**Two body caps, deliberately equal.** `client_max_body_size` at the edge and
`MaxBodySizeMiddleware` in the application are both kept at `MAX_AUDIO_SIZE_MB`.
The first attempt made the edge 2 MiB larger so the application would stay the
primary limit — and a 51 MiB upload was then buffered by nginx, forwarded, and
refused mid-stream by the middleware, which nginx reported as **502 Bad
Gateway**. Any gap between the caps is a band of request sizes that gets a worse
answer than before the proxy existed. With them equal there is no gap: nginx
refuses before a byte reaches Python, and returns the API's own
`FILE_TOO_LARGE` envelope so nothing downstream has to learn that some 413s look
different. Rejection costs 0.24 ms and transfers no body.

The application's middleware is untouched and is still the only limit for
anything that reaches it directly — which is what defence in depth means here.

**Headers.** `X-Content-Type-Options`, `Referrer-Policy` and `X-Frame-Options`,
set at server level with `always`. No `add_header` appears in any `location`:
nginx's inheritance is replace-not-merge, and an earlier version of the config
set one header inside the 413 handler and thereby dropped the other two from
that response. The Content-Security-Policy is **not** set here and is not
missing either — it is the web app's, per request, for the reason in
[Content-Security-Policy (10.9)](#content-security-policy-109) below.

**TLS is not provided and is not implied.** The proxy speaks HTTP and advertises
no HSTS, because advertising transport security from a plaintext listener would
be a false claim. Terminating TLS is an external responsibility.

### Rate limiting (10.3)

Added against a measurement, not a worry. Every owner-scoped route mints an
identity when the key is absent or unrecognised, so **an unauthenticated read is
a write**: sixty concurrent requests carrying no credential created sixty owner
rows and sixty credential rows in 0.47 seconds — about 128 identities per second
from one client, with no bound anywhere, no cleanup, and an anonymous path all
the way through to a billable provider call.

Two limits, keyed by two different things, because they answer two different
questions:

- **New identities, per client address.** There is no identity to key this on —
  creating one is the thing being limited. The guard is consulted **before** the
  rows exist, so a refusal writes nothing.
- **Costly requests, per owner.** Uploading, either analysis, feedback, adding a
  key. Keyed by owner so one person on a shared address cannot spend another's
  allowance, and so reading your own history is never charged.

The identity limit is reached through `MintGuard`, a one-method collaborator the
HTTP adapter passes to `BearerKeyResolver`. The resolver learns nothing about
addresses, counters or quotas — it asks "may I?" and writes only if the answer
is yes. It is a *required* constructor argument rather than an optional one with
a permissive default, because a default that allows silently removes the limit
the day somebody adds a second construction site.

`X-Forwarded-For` is **ignored** unless `RATE_LIMIT_TRUSTED_PROXIES` is set. The
header is client-supplied, so honouring it by default would make the limit
bypassable by anyone who read this paragraph — worse than no limit, because it
would look like protection. With *n* trusted proxies the client is *n* hops back
from the right-hand end, which is the only part a proxy you trust actually
wrote.

**What it is not.** It is defence in depth beside a real edge limiter, exactly as
`MaxBodySizeMiddleware` is beside a proxy's body cap, and it is not a defence
against a distributed attacker with many source addresses. Until 10.10 it was
also one process's memory; see below.

### Where the count lives (10.10)

10.3 recorded that the limiter was one process's memory and that "two API
workers have two counters, so the effective limit is multiplied by the worker
count". 10.10 measured it rather than leaving it as prose, against a real server
and a real PostgreSQL, at a limit of five new identities per hour:

| Workers | Identities minted, in-process | With the shared counter |
| --- | --- | --- |
| 1 | 5 — 1.0× | 5 — 1.0× |
| 2 | 10 — 2.0× | 5 — 1.0× |
| 4 | 20 — 4.0× | 5 — 1.0× |
| 8 | 34 — 6.8× | 5 — 1.0× |

The multiplier is not approximately the worker count; it *is* the worker count,
until the traffic stops spreading evenly enough to fill every bucket.

**The 10.3 objection did not survive re-inspection.** A database-backed counter
was rejected then in one sentence — it "would add a write to every request in
order to bound the cost of writes". Neither guard runs on every request.
`guard_new_identity` is reached only from `before_mint`, immediately before an
owner row and a credential row are inserted; the costly guard runs only on
uploading, starting either analysis, asking for feedback and adding a key.
Reading is never limited. Every request that reaches the counter was already
about to write. 10.6 reached the same conclusion from the other side when it
added `owners.last_seen_at`.

**One statement, which is the whole correctness argument.** Reading the count,
deciding whether the window expired, resetting or incrementing it and returning
the result are a single `INSERT ... ON CONFLICT DO UPDATE`. Two workers collide
on the primary key, and the second waits for the first to commit before applying
its increment to the row the first one wrote. A read-then-write limiter passes
every sequential test and fails the concurrent one: measured, it allowed **40 of
40** attempts against a limit of 5, where the upsert allowed exactly 5.

**Both limiters are `RateLimiter`**, and `api/rate_limit.py` depends on the
protocol — which is where 10.3 said a shared counter would belong if one were
ever needed. `tests/test_rate_limit_shared.py` runs one contract against both,
so a shared counter that quietly disagreed with the one it replaces fails the
same assertion.

**The default stays in-process**, because for the bundled deployment it is not a
compromise: `docker-compose.yml` runs a single uvicorn worker, where there is no
second counter to disagree with, and an in-process check costs 0.001 ms against
the 0.9 ms a shared one costs. `RATE_LIMIT_BACKEND=database` is what a
deployment scaling past one worker turns on; selecting it without `DATABASE_URL`
is refused at startup rather than falling back, because an operator who set it
because they run four workers would otherwise get four counters and no
indication the setting did nothing.

**What the shared counter gives up.** It is anchored to the database server's
`now()`, not to `time.monotonic`: a monotonic clock is meaningless between
machines, and the only clock the workers share is the server's. A backwards
wall-clock correction there can extend a window by the size of the correction.
It also costs one statement — 0.935 ms mean on the contended key, against the
0.856 ms the owner-and-credential write it rides with costs — so it roughly
doubles the cost of a request that was already writing.

**A multi-worker first boot was broken, and is fixed.** Making several workers a
supported shape exposed a bug older than this step: `apply_migrations` created
`schema_migrations` *before* taking its advisory lock, and `CREATE TABLE IF NOT
EXISTS` is not atomic against a concurrent create. Six workers starting together
against a fresh schema failed **25 boots out of 30**; the lock needs no table of
its own, so it now goes first, and 30 of 30 succeed. It only ever bit an empty
database, which is why no existing test saw it.

### Frontend error boundaries (10.4)

Three App Router files, all deliberately thin:

| File | Catches | Shell |
| --- | --- | --- |
| `app/error.tsx` | a render failure inside a page | keeps header and footer |
| `app/not-found.tsx` | an address that matches nothing | keeps header and footer |
| `app/global-error.tsx` | the root layout itself failing | replaces everything |

They present a failure and offer a way out. They contain no domain, API,
identity, credential or rate-limit logic, make no requests, and do not log.
**Expected API failures never reach them** — a 404 for a recording, a 429, a
provider being down are still handled by `lib/*-errors.ts` and the panel that
made the call, which sets an error state rather than throwing during render. A
handled failure arriving at a boundary would be a bug in the panel.

**The error object is never rendered.** All three call
`lib/error-presentation.ts`, which takes the error and discards it, returning
fixed copy. An exception message can carry a stack trace, a filesystem path, a
database DSN, a provider key or an owner id; the safest guarantee that none of
it reaches the screen is for the screen never to depend on it. Next's `digest`
is withheld for the same reason the owner id is: a reader who cannot act on an
internal identifier should not be shown one.

`global-error.tsx` is the odd one out on purpose. It replaces the root layout,
so it assumes nothing the layout provides: no Tailwind (those classes and the
design tokens live in `globals.css`, which the layout imports), no `next/font`,
no `next/link`, no site chrome. Its palette is inline, both themes come from a
`prefers-color-scheme` media query, and its "go home" is a plain anchor so
recovery is a real navigation rather than a client-side transition through a
router that may be part of the problem. There are no React providers anywhere
in this codebase to miss, but the rule is written down so the next person adding
one knows this file must keep working without it.

### Content-Security-Policy (10.9)

10.5 shipped three security headers at the edge and deliberately shipped no CSP,
recording the reason in the nginx template, in `architecture.md` and in
`limitations.md`: Next.js emits inline `<script>` elements, so a policy worth
having needs a nonce minted per request, and a speculative one would either be
bypassable or would break the product. 10.9 built the nonce.

**Where it lives, and why not at the edge.** `frontend/proxy.ts` — Next 16's
name for what used to be `middleware.ts`, and nothing to do with nginx — mints
128 bits of randomness per request, sets the policy on the *request* headers so
Next.js stamps the nonce onto every script element it renders, and repeats it on
the response. The edge sets none, and that is not an omission: a browser handed
two `Content-Security-Policy` headers enforces **both**, so a resource must
satisfy each independently, and no policy written in the nginx config can know a
nonce minted after it was written.

**The policy**, with every source traceable to something in this repository:

| Directive | Sources | Because |
| --- | --- | --- |
| `default-src` | `'self'` | the floor |
| `script-src` | `'self'`, the nonce, `'strict-dynamic'` (`'unsafe-eval'` in development only) | the nonce admits Next's two inline scripts; `'strict-dynamic'` admits the chunks *they* load and the AudioWorklet module, and makes the browser ignore `'self'` for scripts, so a `<script src>` injected into the markup is refused even pointing at our own origin |
| `style-src` | `'self'`, `'unsafe-inline'` | the one concession; measured, see below |
| `img-src`, `font-src` | `'self'` | `next/font` self-hosts; the built CSS has no `url(data:…)` and no external origin |
| `media-src` | `'self'`, `blob:` | a recorded take is built in memory and played from an object URL |
| `connect-src` | `'self'`, the configured API origin | in development the API answers on another port; behind the bundled proxy this adds nothing |
| `object-src`, `base-uri`, `form-action`, `frame-ancestors` | `'none'` | four things this product never does |

No `upgrade-insecure-requests`, for the reason the proxy advertises no HSTS: the
deployment speaks HTTP and a policy claiming otherwise would break it. No
`report-uri`, because nothing here collects reports.

**Pages render per request, and that is the cost.** A nonce and the full-route
cache cannot both be had: a prerendered page is HTML built before the nonce
existed. Measured on this application with the route left static — the policy
was served correctly, Chromium refused all ten of the page's own script elements
and the HTML sat there inert. `app/layout.tsx` therefore declares
`dynamic = "force-dynamic"`, and one build of each was benchmarked against the
same machine, one server at a time, 400 requests after 20 warm-ups:

| Build | mean | p50 | p95 |
| --- | --- | --- | --- |
| Static, prerendered, no policy | 2.816 ms | 2.635 ms | 3.737 ms |
| Dynamic, nonce per request | 7.068 ms | 6.458 ms | 10.170 ms |

About 4.3 ms per document, and it buys a strict `script-src`. What is given up
is a cached render of an app shell: every page in this product fetches its
content in the browser, so the prerender it replaces read nothing, called
nothing and produced no data.

**`style-src` keeps `'unsafe-inline'`, and the alternative was measured rather
than assumed.** Built with `style-src 'self' 'nonce-…'` — Next.js's own
suggestion — the outcome was the opposite of the expected one. All twenty inline
`style` declarations the analysis screens render **kept working**, because React
writes them through the CSSOM after hydration and the CSSOM is outside CSP's
reach. What broke was `app/global-error.tsx`, whose `<style>` element raised a
`style-src-elem` violation and did not apply: the last-resort failure page,
unstyled, which is the one page whose entire job is to work when everything else
has not. The concession is bounded by the rest of the policy — injected CSS
cannot execute, and the classic CSS exfiltration channel is a `url()` that
`img-src`, `font-src` and `connect-src` all refuse — so what remains is
defacement of a page an attacker already had to inject HTML into.

**Verified against the running stack, not only in unit tests.** Chromium, real
API, real PostgreSQL: upload → speech analysis → audio measurement → the musical
key card → the identity panel, with 1 canvas, 2 SVGs and 20 inline-styled
elements rendered — **0 violations, 0 console errors, 0 page errors**. The
microphone path was exercised separately (worklet loaded, take recorded, blob
played back) and `audioWorklet.addModule` was called directly to confirm
`'strict-dynamic'` admits it. `npm run dev` was checked too: without
`'unsafe-eval'` every module evaluation raised a `script-src` violation and the
page never hydrated, which is why development relaxes exactly that one source.

**What a CSP does not cover here.** API responses carry no policy of their own.
The bundled deployment routes only `/api/` to the backend and every response it
sends is JSON under `X-Content-Type-Options: nosniff`; a blanket
`default-src 'none'` in the application would also apply to FastAPI's own
`/docs`, which loads Swagger UI from a CDN and is reachable only by someone
already inside the internal network. Left undone deliberately, and recorded in
`limitations.md` rather than left to be discovered.

### The identity seam

Every domain service takes `owner_id: uuid.UUID` and nothing else. No service
knows what a key is, how a header is spelled, or how identity was established —
identity is established in exactly one place, and
`services/owners/identity.py` states that as a protocol rather than leaving it
as a property the next change could quietly lose.

Step 10.1 declared that protocol and consumed it nowhere: the API called the
bearer-key function directly, so the seam was documentation-shaped. 10.2 made it
code-shaped. `BearerKeyResolver` is the one implementation, the API depends on
the protocol, and `tests/test_resolver.py` substitutes a resolver that
establishes identity a completely different way and then drives the whole
owner-scoped product through it.

A password or OAuth resolver is therefore a **second implementation of one
method**, resolving to the *same* `owner_id` that already owns the recordings.
No migration reassigns anything, and nothing in `services/recordings`,
`services/analysis`, `services/audio_analysis`, `services/comparison` or
`services/progress` changes. The schema constraint that used to stand in the way
is gone: `owners` no longer carries a key, so an owner who signs in some other
way needs no column relaxed and no placeholder key invented.

### Portability and deletion (7P)

The key is shown in the browser because the server *cannot* show it: only a hash
is stored. That display is the entire recovery mechanism, which is why the copy
states plainly what happens if it is lost.

Deletion removes the stored audio **before** the rows. The database half is one
`DELETE` — everything cascades from `owners` — but the audio lives on disk, and
removing the rows alone would report success while leaving every recording on
the server. Files first means a crash mid-way leaves the rows and a retry
finishes the job; the reverse would leave orphaned audio nobody can name.

The `X-VocalLens-Owner` header carries it in both directions: inbound to name an
owner, outbound **only** when one is minted. It is named in the CORS
`expose_headers` list — without that the browser receives the token and
withholds it from the page, so every request would mint a new identity and
history would never accumulate, with no error anywhere to explain why.

## Frontend layout

| Path | Responsibility |
| --- | --- |
| `app/` | App Router routes, layout, global styles |
| `components/` | Presentational and container components |
| `components/ui/` | Primitives (`Button`, …) |
| `components/record/` | Microphone recorder and Live Vocal Practice |
| `hooks/` | Client-side state and data-fetching hooks |
| `lib/config.ts` | Public runtime configuration |
| `lib/api.ts` | Typed API client and `ApiError` |
| `lib/pitch*.ts`, `lib/wav.ts` | Browser-side pitch detection and WAV encoding |
| `lib/live-pitch-engine.ts` | The audio graph: microphone, worklet, cleanup |
| `public/pcm-capture-worklet.js` | Audio-thread capture; loaded by URL, not bundled |
| `types/api.ts` | Response types mirroring backend schemas |

Conventions:

- Server Components by default; `"use client"` only where interactivity or
  browser APIs are needed (recording, live status, charts).
- All network access goes through `lib/api.ts` so error handling and the base
  URL stay in one place.
- `types/api.ts` mirrors `app/schemas/` by hand. If the surface grows, generate
  it from the OpenAPI schema rather than letting the two drift.
- Stateful or async logic lives in plain TypeScript modules, not in hooks. The
  polling engine, the pitch stream and the audio graph are all classes or
  factories that a test can drive directly with an injected clock or a scripted
  input; the hooks around them are thin. `node --test` runs them with no test
  framework and no renderer (see "Checks").

### Live audio

Everything about the microphone runs in the browser and is described in full in
[audio-analysis.md](audio-analysis.md). Two rules govern it:

- **Microphone audio never leaves the page while recording.** No provider, no
  model and no VocalLens endpoint receives it. The only thing that can be
  uploaded is the finished WAV, and only on an explicit press.
- **Live pitch and backend analysis are separate products.** They share a
  musical reference and nothing else, so the UI labels the live figures "Live
  recording estimate" and never places them alongside measured analysis.

Live Vocal Practice (`components/record/live-practice-panel.tsx`) is a *reader*
of that stream, not a second detector: meter, consistency, session range and
target-note comparison are all arithmetic over `LivePitchSample` in
`lib/live-practice.ts`. There is one pitch detector in this product.

Per-frame data does not go through React. Pitch frames arrive ~30 times a
second and are delivered to subscribers that write to the DOM or a canvas
directly; re-rendering the tree at that rate to move one number is how a live
display starts dropping frames. React state holds lifecycle, errors and the
finished file, plus a clock that only changes on the whole second.

No realtime backend was added for this: no WebSocket, no Redis, no queue, and
no per-frame storage anywhere.

### Adding a second analysis pipeline

`services/audio_analysis/` and `services/orchestration/audio_analysis.py`
deliberately mirror their speech-analysis counterparts: the same
protocol-first shape, the same `start`/`run` split, the same idempotency rules,
the same staleness sweep and the same atomic-write repository discipline. Two
differences follow from there being no provider involved — the work is CPU-bound
so it runs in a worker thread rather than on the event loop, and there is no
partial success, because there is no optional second provider to lose.

The measurement itself sits behind an `AudioAnalyzer` protocol, so orchestration
imports neither numpy nor a decoder and a test can drive the whole workflow with
a stub in microseconds.

This is now the **third** copy of the JSON-document write discipline
(recordings, analyses, audio analyses). That duplication is deliberate and
temporary: the store is a stopgap, a real database replaces all three, and a
generic document-store abstraction built now would be a layer to delete later.

## Design system

Dark-first, defined as CSS custom properties in `app/globals.css` and exposed to
Tailwind through `@theme inline`. Colours are referenced semantically
(`bg-surface`, `text-muted`, `text-danger`) rather than as raw palette values,
so a theme change is a one-file edit. Light mode is a full second theme via
`prefers-color-scheme`.

## Error handling

Handled failures return a stable envelope so the frontend can react to a code
rather than parse prose:

```json
{
  "status": "failed",
  "error_code": "INSUFFICIENT_PITCH_SIGNAL",
  "message": "We could not detect enough reliable pitch information."
}
```

Audio analysis failing is an expected outcome, not a server crash. The typed
counterpart is `ApiErrorBody` in `frontend/types/api.ts`.

## Logging

`app/core/logging.py` emits one JSON object per line. The log message is an
**event name**, with context passed as `extra`:

```python
logger.info("analysis_completed", extra={"analysis_id": aid, "duration_ms": ms})
```

Planned events: `analysis_started`, `analysis_completed`, `analysis_failed`,
`ai_feedback_generated`.

Never logged: API keys, raw audio, private user data.

## Long-running work

Analysis must not block the HTTP request. Phase 2 will use FastAPI background
tasks with a status field on the analysis record. A queue (Redis + Arq/Celery)
is only introduced if measured processing time makes it necessary — not before.

## Checks

```bash
cd backend && .venv/bin/pytest && .venv/bin/ruff check . && \
  .venv/bin/ruff format --check . && .venv/bin/mypy app
cd frontend && npm run lint && npm run typecheck && npm run build
```

`./scripts/check.sh` runs all of the above.

`mypy` runs in strict mode over `app/`. `ruff` lints and formats both `app/` and
`tests/`.

### The checks nobody ran (10.20)

Everything above had been true since Phase 0, and **nothing invoked any of it**.
There was no `.github` directory. Nineteen steps of Phase 10 rest on a suite
that ran when somebody remembered to type one command.

The second half of the finding is why a workflow alone would not have been
enough. Three runs of the same suite, and what each is worth:

| run | result | what it proves about the SQL |
| --- | --- | --- |
| no database | 1 584 passed, **187 skipped**, green | nothing |
| this container, with a database | 1 769 passed, 2 skipped | everything except two permission tests root cannot exercise |
| CI: not root, with a database | **1 782 passed, 0 skipped** | everything |

185 of those 187 skips are the database gate, and they are not a random 185:
they are the whole PostgreSQL half of the contract suite (99), every migration
and statement-shape assertion (52), the shared rate limiter (25), the filesystem
import (7) and the concurrency tests (2) — which is where nearly all of Steps
10.11 to 10.19 lives. A CI job that forgot `TEST_DATABASE_URL` would look
identical to one that did not: same command, same green tick.

`test_database.py` had claimed such a run "is not skipped quietly in CI: a run
with no database says so in the skip reason". A reason printed in a log nothing
reads is exactly what quiet means, and there was no CI to read it. So the run
itself now refuses: with `REQUIRE_DATABASE_TESTS=1`, a missing `TEST_DATABASE_URL`
is a `UsageError` before collection rather than 185 skips and a success. It is
stated by whoever starts the run rather than sniffed from the environment, so a
contributor is never surprised by it, and a checkout with no database still runs
the other 1 584 tests exactly as before.

`tests/test_ci.py` holds the workflow to what makes it worth running — that it
sets both variables, that it runs `scripts/check.sh` rather than restating the
checks, that its PostgreSQL major version is the one `docker-compose.yml`
deploys and its Python is the one `backend/Dockerfile` runs — for the same
reason `test_deployment.py` holds the compose file to a database with no
published port. Each was confirmed by mutation: deleting
`REQUIRE_DATABASE_TESTS`, moving to `postgres:15`, and inlining the commands
each fail exactly one test.

The workflow was not written and hoped for. Its conditions were reproduced here
before it was committed — a database created empty, so the suite's own
migrations have to build the schema, and the run performed as a non-root user,
because two storage tests skip under root and CI would have been the first thing
ever to execute them. They pass. What CI runs is a strictly larger suite than
anything this container can produce.

## Installing it as an app (Step 12)

VocalLens is installable: a manifest, an icon set and a service worker, which is
all a browser needs to offer "install" and to launch the page in its own window
with a launcher icon. **Installing changes how it is launched, not what it
measures.** There is no separate app build, no second code path and no feature
that exists only when installed.

### What the service worker caches, and what it refuses

Written down in `frontend/lib/service-worker-policy.ts`, which is the
specification, and implemented in `frontend/public/sw.js`, which is a classic
script with no bundler in front of it. The rules are unit-tested against the
module and the running worker is driven through a real Chromium; the split is
the one `pitch.ts` and `pitch.py` already use.

| Request | Served | Why |
| --- | --- | --- |
| `/api/**` | Network, never stored | A cached measurement is presented as current and the client cannot tell that it is not |
| A document | Network, offline page on failure | See the nonce measurement below |
| `/_next/static/**` | Cache first | The URL is a content hash, so it cannot go stale |
| `/icons/**`, the AudioWorklet | Cache, revalidated behind it | A stable URL over bytes that change between deploys |
| Anything else | Network, never stored | A default of "cache it" is how a cache comes to hold what nobody chose |

The API rule is checked **first**, before any rule that could match on shape. A
`POST` is never intercepted at all: replaying one from a cache would repeat an
upload rather than re-read a value. A response that is not `2xx` is never
stored, because caching a 502 turns a transient failure into a durable one whose
only cure is clearing site data.

### The nonce and the cached document — a measurement that refuted the argument for it

The obvious reason not to cache the HTML is that every response carries a fresh
per-request CSP nonce, so a cached document would carry a stale one and the
browser would refuse every script on the page. **That argument is wrong**, and
it was written into this repository before it was checked.

A cache stores a response *with its headers*. The nonce in the cached header and
the nonce in the cached body therefore go stale **together**, which is to say
they stay in agreement. Measured, by capturing a document exactly as a cache
would hold it and replaying it later through request interception:

| | |
| --- | --- |
| nonce in the cached body | `igmfCgZvsPeMJkiW4LOCkA==` |
| nonce in the cached header | `igmfCgZvsPeMJkiW4LOCkA==` |
| CSP violations on replay | **0** |
| page errors | **0** |
| hydrated | yes — a client-side control answered a click |

So the shell *can* be precached, and precaching it would give real offline use:
Live Vocal Practice runs entirely in the browser, and from a cached shell it
would work with no network at all.

**It is still not cached, for the reason that survives the measurement.** A
per-request nonce is unguessable because it is fresh every time. A cached shell
serves one fixed nonce for as long as the entry lives, turning a per-request
value into a durable per-device one. `app/layout.tsx` gave up the full-route
cache specifically to keep that property — the reasoning is recorded there — and
a service worker that quietly took it back would be overturning a decision made
one layer up without saying so.

That is a trade, not a free choice, and what it costs is offline practice. It is
recorded in `limitations.md` as something given up rather than something
overlooked, so that reversing it later is a decision somebody takes on the
evidence above rather than a gap they discover.

### What the policy had to admit

Two directives, each because something now loads it — the rule `lib/csp.ts`
already held itself to:

- **`worker-src 'self'`.** Without it the worker's own script falls under
  `script-src`, which carries `'strict-dynamic'`, which makes a browser ignore
  `'self'` and demand a nonce that a worker script cannot carry. Naming
  `worker-src` is what lets the page keep `'strict-dynamic'` and still register
  a worker.
- **`manifest-src 'self'`.** It would work as a fallback from `default-src`; it
  is named because a directive that is present says what is loaded, where a
  fallback only says what is allowed.

Both are `'self'` alone. **Making the app installable admitted no new origin**,
and a test asserts that rather than leaving it to review.

The `pcm-capture-worklet.js` AudioWorklet is *not* covered by `worker-src` — it
has its own fetch destination and stays under `script-src`. That adding one
directive did not disturb the other was measured rather than read off a
specification: recording a take in Chromium with the new policy engaged the live
readout, fetched and cached the worklet, and raised **0 CSP violations**.

### Offline

A navigation that cannot reach the network gets `public/offline.html`: a static
page with **no script of any kind**, which is what lets it be served from a
cache when nothing else can be. It says what needs a connection and why, and it
does not pretend the app is available.

## Feature allocation, Steps 7A–7M

Where each capability lives, and what it is *not*. Read the "Not" column as
part of the feature: most of the mistakes available in this product are
category errors, and this table exists to make them visible.

| Capability | Where it lives | Layer | Not |
| --- | --- | --- | --- |
| Upload, validation, metadata | `services/audio/`, `routes/recordings.py` | Deterministic | Not transcoding; format is decided by content, never by extension |
| Recording bytes | `services/audio/storage.py` (filesystem) | Deterministic | Not in the database |
| Recording metadata, analyses, owners | `db/`, `services/*/postgres_repository.py` | PostgreSQL | Not an ORM; not the filesystem |
| Owner identity | `api/owner.py`, `services/owners/` | PostgreSQL | **Not authentication**: bearer keys, no password, no recovery |
| Ownership enforcement | Every repository read, in SQL | PostgreSQL | Not frontend filtering; not a `403` |
| Identity portability and deletion | `services/owners/`, `routes/identity.py` | PostgreSQL + browser | Not authentication; the server cannot recover a lost key |
| Recording history | `routes/recordings.py`, `services/recordings/history.py` | PostgreSQL | Statuses only, never results; `null` ≠ pending ≠ failed; a page, and it says so |
| History paging | `services/recordings/cursor.py` | PostgreSQL | Keyset, not offset; opaque, not secret; never an owner scope |
| Speech transcription | `services/ai/deepgram.py` behind `SpeechToTextProvider` | Provider | Not a measurement; provenance travels with it |
| Speech metrics | `services/analysis/metrics.py` | Deterministic | Counted from the transcript, never estimated |
| Speech feedback | `services/ai/claude.py` | Provider | Prose about numbers; never produces a number |
| Backend pitch detection | `services/audio_analysis/detector.py` (NSDF) | Deterministic | Not CREPE, not librosa, not the browser detector |
| Detected range, stability, loudness, spectrum | `services/audio_analysis/analyzer.py` | Deterministic | Range is *this recording*, never physiological; RMS/peak are not LUFS |
| Note breakdown | `services/audio_analysis/notes.py` | Deterministic | Share of **voiced** time, not of duration; not musical transcription |
| Musical key | `services/audio_analysis/key.py` | Deterministic | The key *implied by what was sung*, never a song's key; a label, not a measurement; refuses rather than guesses, and no model ever sees it |
| Audio feedback | `services/ai/claude.py` via `AudioFeedbackProvider` | Provider | Never invoked on `INSUFFICIENT_PITCH_SIGNAL`; no timbre labels, no score |
| Live pitch readout | `frontend/lib/pitch-detector.ts` | Browser-local | Never uploaded; never compared with the backend result |
| Live Vocal Practice | `frontend/lib/live-practice.ts`, `hooks/use-live-stats.ts` | Browser-local | "Not enough yet" ≠ 0%; not a singing-ability score |
| Live musical key | `frontend/lib/live-key.ts` | Browser-local | The same mathematics as `key.py`, held to it by `fixtures/key-parity.json`; a second measurement, never shown beside the uploaded one; committed only after four consecutive ticks agree |
| Microphone recording | `frontend/lib/live-pitch-engine.ts`, `lib/wav.ts` | Browser-local | Uploaded only on an explicit action |
| One-analysis-at-a-time | Partial unique indexes | PostgreSQL | Not an `asyncio.Lock` — that was removed in 7M |
| One feedback run | `claim_feedback`, a single conditional `UPDATE` | PostgreSQL | Not a read-then-write |
| Recording comparison | `services/comparison/` | Deterministic | Measurement comparison, never a score; four of seven metrics have no better direction |
| Progress over time | `services/progress/` | Deterministic | Measurements over time, never a level or a trend line; `null` is a gap, never a zero |
| Installability | `app/manifest.ts`, `public/sw.js` | Browser-local | A launcher icon and a window; never a second code path, and never a feature that exists only when installed |
| What may be cached | `lib/service-worker-policy.ts` | Browser-local | Content-hashed assets only. Never an API response, never a document — the second for what caching does to the nonce, not because it would break |
| Song references | `services/compatibility/repository.py`, `routes/compatibility.py` | PostgreSQL | **Metadata somebody typed**, never audio; no decoder, no analysis lifecycle, no file to delete; owner-scoped like everything else |
| Song compatibility | `services/compatibility/fit.py` | Deterministic | Arithmetic on two closed intervals of semitones; never a score, and no field that could hold one; the transposition is a shift, never musical advice |
| Where a number came from | `RangeSource` on every range | Deterministic | `measured` is from audio, `asserted` is typed; never styled alike, never summed into a third number |

Two allocations are worth restating because they are the ones most likely to
erode:

- **Every number in the UI comes from the deterministic column.** A model is
  given already-computed measurements and returns language. It has no field in
  which to return a measurement, which is the cheapest guarantee available.
- **The live browser estimate and the backend audio analysis are different
  measurements** of the same kind of quantity, by different algorithms over
  different windows. Neither validates the other and the UI never implies they
  agree.
- **A compatibility result mixes a measured number with a typed one**, and that
  is the only place in the product where it happens. The mixing is legitimate
  because the arithmetic is exact and the provenance is carried into every
  response and onto the screen; it would stop being legitimate the moment the
  two were averaged, summed, or rendered the same way. `RangeSource` exists so
  that losing the distinction requires deleting a field rather than forgetting a
  convention.

### Comparison (7N)

Three layers, and the split is the point:

| Layer | Module | May do |
| --- | --- | --- |
| Query | `comparison/sources.py` + the recording repository | Load exactly two recordings, owner in the `WHERE` clause |
| Eligibility | `comparison/service.py` | Decide whether each side can take part, and say why not |
| Arithmetic | `comparison/compare.py` | Subtract. Nothing else — no IO, no provider, no re-measurement |

`compare.py` is a pure function over two already-stored `AudioMetrics` and two
note breakdowns, so a change to the analyzer cannot silently change what a
comparison *means*, and the whole of it tests in microseconds with no fixtures.

The note breakdown comes from `audio_analysis/notes.py` — the same function the
single-recording endpoint uses, over the same stored timeline. There is one note
aggregation in this system.

**No AI.** A comparison is subtraction, and a model that produced one of these
numbers would be producing a measurement. `ComparisonService` has no provider
dependency at all, which is what makes that structurally impossible rather than
merely discouraged.

The query is targeted by primary key, not by owner history: see the comment on
`COMPARISON_SOURCES_SQL` for the type cast that makes the difference, and the
measurements behind it.

### Progress (7O)

The same three layers as comparison, for the same reason:

| Layer | Module | May do |
| --- | --- | --- |
| Query | `progress/sources.py` + the recording repository | Load one owner's window, owner in the `WHERE` clause |
| Service | `progress/service.py` | Bound the window and delegate |
| Domain | `progress/series.py` | Build the series. No IO, no provider, no re-measurement |

Two decisions are worth stating.

**The analysis document is never selected.** Metrics live in a JSONB document
that also holds the pitch timeline, so a document grows with the *length* of the
recording rather than with the number of measurements in it. The query extracts
each scalar by path. Measured on 200 owners × 50 two-minute recordings, one
30-recording window: **125 ms and 14 KB** extracting scalars versus **676 ms and
18 MB** reading the documents. The gap widens with recording length.

**The document is reached once per row, not once per scalar.** Extracting by
path is only half of it: until Step 10.12 every scalar was read from
`a.document`, and each of those eleven expressions decompressed the whole
document again. The lateral now projects `document->'metrics'` and the scalars
come off that — **593 ms → 30 ms** for a 30-recording window of five-minute
recordings. See [above](#a-big-document-is-decompressed-once-per-expression-1012).

**SQL selects, filters and orders; the domain defines progress.** What a null
means, which analyses are eligible, what may be said about a change — all of it
is in `series.py`, where it tests in microseconds without a database. The
ordering is not re-derived there: SQL returned the window oldest-first with a
deterministic `recording_id` tie-break, and re-sorting would put two definitions
of "in order" in the codebase for one concept.

**No AI, structurally.** `ProgressService` takes only the recording repository.
There is no object in its graph through which a model could produce or judge a
trend, which is a stronger guarantee than a rule saying it must not.

### Not built in Phase 7

Nothing. The phase is complete. **Phase 8 was not started in Phase 7** — no song
analysis, key detection, BPM, melody extraction or transposition existed at the
end of it. Step 10.7 wrote
[phase-8-specification.md](phase-8-specification.md) without implementing any of
it, and Step 10.8 re-audited, corrected it, and then built it. What the
specification proposed is what shipped: the result is derived on read from the
pitch timeline `audio_analyses` already stores, in the same way `notes.py` is.

10.8 found that nothing in the product consumes a musical key —
`limitations.md` defines Phase 9 compatibility as comparing *ranges*, not keys —
so the scope was a product decision rather than an engineering one. It was taken:
**musical key only.** What the phase does *not* cover, and why, is recorded in the
specification; the shortest version is that there is no song in this product to
analyse, and `song` appears in the codebase only as a test upload filename.

**Phase 8 is now built.** `audio_analysis/key.py` folds a stored pitch timeline
into twelve pitch-class shares and estimates the key those shares fit;
`AudioAnalysisService.key()` reaches it through the same owner-scoped read
`notes()` uses — `current_timeline()` since 10.11, when the reads that need no
timeline stopped using the one that loads it; `GET …/audio-analysis/key` serves
it, adding no error code, no query and no persisted field; and
`musical-key-card.tsx` renders it below the note breakdown it shares a timeline
with.

It added no architecture, which was the point of specifying it that way. There is
no new table, no migration, no dependency, no provider, no background work and no
cache — the key is derived on read, so every analysis ever completed is
answerable and nothing can go stale. The endpoint folds from the analysis record
the route already loaded, so the read costs one document rather than two and no
window exists in which a re-analysis could pair one record's ids with another
record's key.

**No model sees the key**, structurally: it is absent from the feedback payload,
the comparison service and the progress series, none of which gained a field that
could hold it.

**The extension after it — the live readout — is built too, and it is the one
place this repository deliberately implements the same measurement twice.**
`frontend/lib/live-key.ts` estimates the key of a practice session in the
browser, folding the pitch stream Steps 7H/7J already run in the page. It has to
exist in TypeScript because microphone audio never leaves the tab: sending
frames to `key.py` would break the guarantee Live Vocal Practice is built on.
The duplication is held together by `fixtures/key-parity.json`, one table of
profiles and verdicts asserted by both suites, generated from the Python side —
the same discipline `pitch.py` and `lib/pitch.ts` already follow.

Architecturally it adds nothing at all: no endpoint, no request, no schema, no
storage, no telemetry, and nothing on the server knows the feature exists. The
live key is computed on the publish tick that already runs, displayed, and
discarded when the take ends. It is never rendered beside the uploaded
recording's key — a test sweeps the component tree to keep it that way — because
the two read different frames through different gates and are two measurements
rather than one measurement checked twice.

### Not built in Phase 9

All of it. The Phase 9 audit re-verified the whole reference-song surface against
source and found nothing: no reference upload, no reference storage, no
catalogue, no external music provider, no vocal separation, no stored song
metadata, and no code calculating overlap, difficulty, suitability, a target key
or a semitone shift. The one comparison this system performs —
`services/comparison/` — places two of *one owner's own* recordings side by side;
neither side is a reference, and there is nothing in the architecture to compare
a measurement against.

The architectural point, for whoever picks this up: **the singer's half is
finished and needs no work.** Detected range, pitch timeline, note histogram and
key are all built, stored in the `audio_analyses` document and owner-scoped. What
Phase 9 adds is the other side of the comparison, and its shape — table, storage,
API, background work, cost, whether anything is stored at all — depends entirely
on a product decision that has not been taken. The analysis, four input models
and a draft design are in
[phase-9-specification.md](phase-9-specification.md); nothing in it is
implemented.
