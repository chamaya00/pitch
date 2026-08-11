# API reference

Base URL: `http://localhost:8000`
Versioned prefix: `/api/v1`

Interactive documentation is served by FastAPI at `/docs` (Swagger UI) and
`/redoc`. The machine-readable schema is at `/openapi.json`.

---

## Identity: `X-VocalLens-Owner`

Every recording belongs to an owner, and the owner is resolved from this header.

- **Send it** on every request once you have one.
- **Omit it** on a first request. The response carries a newly minted value in
  the same header; store it and send it from then on. It is returned **only**
  when minted, never on later responses.
- A well-formed value the server does not recognise **mints a new identity**
  rather than failing. A `401` would be right for authentication; this is not
  authentication, and a client holding a token from a reset database would
  otherwise be permanently stuck with an error it cannot clear.
- A **malformed** value is refused with `VALIDATION_ERROR`. It cannot be a token
  this server issued, so treating it as absent would hide a client bug behind a
  silently changing identity.

The header is listed in the CORS `expose_headers`, so browser JavaScript can
read the minted value cross-origin.

**This is not authentication.** There is no password, no second factor and no
revocation: anyone holding the token is the owner, and losing it loses the
history. What it provides is a server-side answer to "whose recordings are
these?" — enforced in SQL, on every read, on every route.

### What another owner sees

A recording that belongs to somebody else answers exactly as one that does not
exist: `404 RECORDING_NOT_FOUND`, with the same message. The two are
deliberately indistinguishable — a different answer would confirm that an id is
real to somebody with no right to know. This applies to every recording-scoped
path, `GET` and `POST` alike.

---

## Implemented

### `GET /api/v1/identity`

What the caller's key holds. Takes no identifier.

**200 OK**

```json
{
  "created_at": "2026-08-11T10:03:19.499435Z",
  "anonymous": true,
  "recordings": 3,
  "analysed_recordings": 3,
  "ai_feedback": 3,
  "credentials": [
    {
      "credential_id": "0f2e1a44-6c1b-4f7e-9d2a-8b5c0e1f3a77",
      "label": "Original key",
      "created_at": "2026-08-11T10:03:19.499435Z",
      "current": true
    },
    {
      "credential_id": "8c4d2b91-3a70-4e55-b1c2-5d9f7e0a1b34",
      "label": "Phone",
      "created_at": "2026-08-12T18:41:02.113900Z",
      "current": false
    }
  ]
}
```

`anonymous: true` means every way in is a bearer key — no password, no second
factor, no server-side recovery. Holding several named keys does not change
that; the field becomes `false` only when a credential that is not a bearer key
is attached.

`credentials` lists every way in, oldest first, with `current` marking the one
this request was made with. **All of them resolve to the same owner**, so adding
a key to a second device does not create a second identity.

**No owner id, no key and no hash are returned.** Only a SHA-256 hash of each key
is stored, so the server *could not* return one; the browser holding it is the
only place a key exists in the clear. Echoing the internal owner id would put a
second permanent handle on the same person into logs and screenshots for no
benefit. A `credential_id` is not credential material: knowing one grants
nothing, and revoking needs it.

`ai_feedback` is counted separately because generating it costs a provider call:
measurements can be recomputed from the audio, generated prose cannot.

### `DELETE /api/v1/identity`

Remove the caller's identity and everything belonging to it: every recording,
every analysis, every generated interpretation, **and the stored audio itself**.

**200 OK**

```json
{ "recordings": 3, "audio_files_deleted": 3, "audio_files_failed": 0 }
```

Irreversible, with no soft delete: a row that still exists is not gone. The
audio files are removed before the rows, so a failure part-way through leaves
the rows and a retry finishes the job, rather than leaving audio nobody can
name. A file that could not be removed is reported in `audio_files_failed`
rather than hidden.

Repeating the request is safe. The caller is issued a fresh, empty identity on
its next request.

Neither of these two routes accepts a parameter, so there is nothing through
which a caller could name somebody else.

### `POST /api/v1/identity/credentials`

Add another way in to the identity the caller already has. **This does not
create a new identity**: the new key resolves to the same owner, so it sees the
same recordings, analyses and history. Nothing is copied and nothing changes
hands.

**Request**

```json
{ "label": "Phone" }
```

`label` is optional and display-only — it is never part of resolving anything,
so it needs no uniqueness and carries no security weight. Omitted, blank or
whitespace-only gets `"New key"`; longer than 60 characters is rejected.

**201 Created**

```json
{
  "credential_id": "8c4d2b91-3a70-4e55-b1c2-5d9f7e0a1b34",
  "label": "Phone",
  "created_at": "2026-08-12T18:41:02.113900Z",
  "key": "Yb3xK9pQ2mLrT8wZ4nE1aQ"
}
```

**The key is returned once, here, and never again.** Only a hash of it is
stored, so no later request can show it. Anyone holding it is this identity: it
is a bearer key, not a password.

There is no field for an owner. The owner comes from whoever the request
resolved to, so a client cannot attach a key to somebody else's identity.

### `DELETE /api/v1/identity/credentials/{credential_id}`

Revoke one key. The recordings, analyses and other keys are untouched — this
removes a way in, not any data.

**200 OK** — the credentials that remain, in the same shape `GET /identity`
returns them.

**409 `LAST_CREDENTIAL`** — the caller's only remaining key. Removing it would
strand the identity: the recordings would still exist, still owned, and nobody
could ever reach them again. Deleting the identity is the honest way to get rid
of everything, and it is a different endpoint.

**404 `CREDENTIAL_NOT_FOUND`** — no such key *for this caller*. A credential
belonging to somebody else gets this answer too, including when it is that
owner's last one: a different status would confirm both that the id is real and
that the identity is down to one key.

Revoking the key the request was made with is allowed when others remain. The
request finishes; that key stops working afterwards.

### `GET /api/v1/recordings`

The caller's own recordings, newest first. Whose they are is decided entirely
from the owner header; there is no parameter that could name a different owner.

**Query parameters**

| Name | Default | Range | Meaning |
| --- | --- | --- | --- |
| `limit` | `50` | 1–200 | Largest number of recordings to return |

**200 OK**

```json
{
  "items": [
    {
      "recording": {
        "recording_id": "0c07991ba858449e976cb93f933f5dde",
        "original_filename": "take-a.wav",
        "format": "wav",
        "duration_seconds": 2.0,
        "sample_rate": 22050,
        "channels": 1,
        "size_bytes": 88244,
        "bits_per_sample": 16,
        "created_at": "2026-08-11T05:12:29.705890Z"
      },
      "speech_status": "completed",
      "audio_status": "completed",
      "feedback_status": "not_requested",
      "last_analysed_at": "2026-08-11T05:12:39.429850Z"
    }
  ],
  "count": 1,
  "limit": 50
}
```

**Statuses, not results.** Each item says how far its analyses got; the
measurements come from the per-recording endpoints. Embedding them here would
make a fifty-row history a multi-megabyte response, and would invite comparing
two recordings whose conditions nobody controlled.

**`null` means "never run".** It is not `pending` and not a failure, and a
client must not render it as either. A recording nobody has analysed has
`speech_status: null`, which is a statement about what was asked for, not about
how it went.

### `GET /api/v1/recordings/{recording_id}`

The stored metadata of one recording, provided it belongs to the caller.
Otherwise `404 RECORDING_NOT_FOUND` — see above.

### `GET /api/v1/recordings/progress`

The caller's recorded measurements over time, oldest first.

**Query parameters**

| Name | Default | Range | Meaning |
| --- | --- | --- | --- |
| `limit` | `30` | 1–200 | How many of the most recent recordings to include |

**This is a history of measurements, not a score.** There is no level, no grade,
no ranking and no claim that the singing improved — the response has no field
that could hold one. Four of the seven series carry `direction: "neutral"`,
because a change in them means nothing in particular: a wider detected range,
more pitched time, a higher voiced share and a longer recording are facts about
what was recorded.

**The three directed series are defined against equal temperament**, not against
singing. Slides, vibrato, bends and non-Western intonation move all three
without anything being wrong, and none of them measures skill.

**No trend line is computed.** There is no slope, regression, moving average,
percentage-improvement figure or forecast. The strongest statement made is how
the latest measured value compares with the previous measured one.

**200 OK**

```json
{
  "series": [
    {
      "metric": "in_tune_ratio",
      "label": "Share of pitched time within 25 cents of a note",
      "unit": "percentage_points",
      "direction": "higher_is_nearer_the_note",
      "points": [
        { "recording_id": "4b20…", "recorded_at": "2026-08-11T09:07:12.400Z",
          "status": "measured", "value": 0.0 },
        { "recording_id": "78 67…", "recorded_at": "2026-08-11T09:07:20.100Z",
          "status": "not_eligible", "value": null }
      ],
      "observation": {
        "direction": "higher", "latest": 100.0, "previous": 0.0, "delta": 100.0,
        "latest_recording_id": "…", "previous_recording_id": "…"
      },
      "measured_count": 2
    }
  ],
  "recordings": [
    { "recording_id": "4b20…", "recorded_at": "2026-08-11T09:07:12.400Z",
      "original_filename": "take-one.wav", "duration_seconds": 2.0,
      "audio_format": "wav", "analysed": true,
      "lowest_note": "A4", "highest_note": "A4" }
  ],
  "depth": "series",
  "limit": 30
}
```

#### The seven series

| `metric` | `unit` | `direction` |
| --- | --- | --- |
| `in_tune_ratio` | percentage_points | higher_is_nearer_the_note |
| `mean_abs_cents_deviation` | cents | lower_is_nearer_the_note |
| `cents_std` | cents | lower_is_steadier |
| `semitone_span` | semitones | **neutral** — wider is not better |
| `voiced_ratio` | percentage_points | neutral |
| `voiced_seconds` | seconds | neutral |
| `duration_seconds` | seconds | neutral |

The same seven the comparison endpoint uses, so the vocabulary is learned once.

**No loudness or spectral measurement is plotted.** RMS and peak depend on input
gain; the spectral features have no validated interpretation in this project;
clipping is a recording condition, not progress; and note count is not an
achievement. Plotting any of them over time would invent an interpretation this
system refuses to make.

#### Point statuses

| `status` | Meaning | Carries a value |
| --- | --- | --- |
| `measured` | A real measurement | yes |
| `not_measured` | The analysis completed; this metric was unavailable | no |
| `not_eligible` | No completed analysis: never run, running, failed, or no reliable pitch | no |

`null` is **never** a zero. A client draws the last two as gaps, and must not
plot them at the axis. Both still appear in `recordings`, because a recording
belongs in its own history whether or not it could be measured.

#### Ordering

By the recording's server-assigned `created_at`, oldest first, with
`recording_id` as the tie-break so two recordings created in the same instant
have one deterministic order. The client clock is never consulted. The window is
taken from the most recent end and then returned oldest-first.

#### `depth`

How much *measured* history exists, so a client can be honest about it:

| `depth` | Meaning |
| --- | --- |
| `empty` | Nothing measured |
| `single` | One measurement — nothing to compare it with |
| `pair` | Two — a comparison, **not** a trend |
| `series` | Three or more |

#### No AI

Progress is deterministic arithmetic over stored measurements. No provider is
consulted, and `ProgressService` has no provider dependency through which one
could be — a model producing one of these numbers would be producing a
measurement.

### `GET /api/v1/recordings/compare`

Place two recordings' measurements side by side.

**Query parameters**

| Name | Required | Meaning |
| --- | --- | --- |
| `left_id` | yes | The first recording. Must be a 32-character hex id. |
| `right_id` | yes | The second. Must differ from `left_id`. |

**This is a comparison of measurements, not a verdict.** There is no overall
score, no ranking and no statement about which recording is better — the
response has no field that could hold one. Four of the seven metrics carry
`direction: "neutral"` because their difference has no better end, including the
detected range: a wider range is bounded by what was performed and by the
microphone, not by ability.

**Ownership is enforced in the database query.** A recording belonging to
somebody else is never selected and reports `not_found`, identically to an id
that was never real.

**A refusal is a `200`.** A missing recording, a missing analysis, a failed
analysis or one with no reliable pitch all return successfully with
`comparable: false` and a per-side `status`. Only comparing a recording with
itself is a request error (`VALIDATION_ERROR`).

**200 OK**

```json
{
  "left": {
    "recording_id": "4b203e0d3d8042c19d94fa794c61a0ca",
    "status": "ready",
    "original_filename": "take-one.wav",
    "created_at": "2026-08-11T09:07:12.400Z",
    "duration_seconds": 2.0,
    "audio_format": "wav",
    "error_code": null,
    "lowest_note": "A4",
    "highest_note": "A4"
  },
  "right": { "...": "same shape" },
  "comparable": true,
  "metrics": [
    {
      "key": "in_tune_ratio",
      "label": "Share of pitched time within 25 cents of a note",
      "unit": "percentage_points",
      "direction": "higher_is_nearer_the_note",
      "left": 74.0,
      "right": 80.0,
      "delta": 6.0,
      "availability": "both"
    }
  ],
  "notes": [
    {
      "midi_note": 67,
      "note_name": "G4",
      "presence": "left_only",
      "left": { "duration_seconds": 1.8, "percentage_of_voiced_time": 92.0,
                "frame_count": 78, "mean_abs_cents": 4.1, "in_tune_ratio": 1.0 },
      "right": null,
      "share_delta_points": null,
      "duration_delta_seconds": null,
      "mean_abs_cents_delta": null
    }
  ],
  "caveats": ["different_duration"]
}
```

#### The seven metrics

| `key` | `unit` | `direction` |
| --- | --- | --- |
| `duration_seconds` | seconds | neutral |
| `voiced_seconds` | seconds | neutral |
| `voiced_ratio` | percentage_points | neutral |
| `semitone_span` | semitones | **neutral** — wider is not better |
| `in_tune_ratio` | percentage_points | higher_is_nearer_the_note |
| `mean_abs_cents_deviation` | cents | lower_is_nearer_the_note |
| `cents_std` | cents | lower_is_steadier |

`percentage_points` is its own unit because a ratio moving 0.50 → 0.56 is six
percentage **points**, not six percent. Clients must not relabel it.

**No loudness or spectral measurement is compared.** RMS and peak depend on
input gain, so a difference says as much about the microphone setup as about the
singer; the spectral features have no validated interpretation in this project
and a side-by-side delta would invite exactly the timbre reading the rest of the
system refuses to make. Clipping — the one loudness fact with a defensible
reading — is reported as a caveat instead.

#### `null` is never zero

`delta` is `null` whenever either side was not measured, and `availability` says
which side was missing (`both`, `left_only`, `right_only`, `neither`). A
recording can complete its analysis and still have no detected range — voiced
frames existed, but none was held long enough — and that reports as absent, not
as a range of zero semitones. In `notes`, an absent side means the note was
**never sung**, which is not the same as having been sung for no time.

#### Side statuses

| `status` | Meaning |
| --- | --- |
| `ready` | Measured; takes part in the comparison |
| `not_found` | Unknown **or not the caller's** — deliberately the same answer |
| `analysis_missing` | Nobody has measured this recording yet |
| `analysis_in_progress` | Being measured now |
| `analysis_failed` | Measurement failed; `error_code` says how |
| `insufficient_pitch_signal` | Decoded, but carried no reliable pitch |

#### Caveats

`caveats` names differences this system can actually measure:
`different_duration`, `different_voiced_time`, `little_pitched_signal`,
`different_sample_rate`, `different_audio_format`,
`different_analysis_settings`, `clipping`, `no_detected_range`.

The list is **never exhaustive**. Two recordings are only meaningfully
comparable when captured under reasonably similar conditions, and microphone
quality, room acoustics, effort and physical condition are not measured by this
system and are not claimed here.

### `GET /health`

Unversioned liveness probe for infrastructure (Docker healthcheck, load
balancers). Performs no external calls.

### `GET /api/v1/health`

Same payload, versioned for API clients.

**200 OK**

```json
{
  "status": "ok",
  "service": "VocalLens API",
  "version": "0.1.0",
  "environment": "development"
}
```

### `POST /api/v1/recordings/{recording_id}/analysis`

Starts an analysis of a stored recording, or returns the one that already
exists. `recording_id` must be 32 lower-case hex characters; anything else is a
`422` before the request reaches a service.

**The response is sent before the analysis runs.** Transcription and feedback
happen in a background task, so a provider failure never appears here — it is
recorded on the analysis and surfaces on `GET`.

**202 Accepted** — work is queued or already in flight.

```json
{
  "analysis_id": "3fa85f6457174562b3fc2c963f66afa6",
  "recording_id": "8f14e45fceea167a5a36dedd4bea2543",
  "status": "pending"
}
```

The full `AnalysisResponse` shape is returned; the fields above are the ones
populated at this stage.

**200 OK** — an analysis had already completed. Nothing was queued and no
provider was called.

Repeating the request is safe. An analysis that is `pending`, `transcribing`,
`analyzing` or `completed` is returned as-is. Only a recording whose last
analysis *failed* starts a new one; the failed record stays readable.

| Failure | Status | Code |
| --- | --- | --- |
| Unknown recording | 404 | `RECORDING_NOT_FOUND` |
| Malformed recording id | 422 | `VALIDATION_ERROR` |

### `GET /api/v1/recordings/{recording_id}/analysis`

Returns the recording's most recent analysis: its progress while it runs, its
results once it finishes.

**A failed analysis is a successful response** — `200` with `status: "failed"`
and an `error_code`. The request worked; the analysis did not. Provider failures
are never turned into `500`s.

Which fields are populated depends on `status`:

| status | transcript | metrics | feedback | error_code |
| --- | --- | --- | --- | --- |
| `pending`, `transcribing` | `null` | `null` | `null` | `null` |
| `analyzing` | present | present | `null` | `null` |
| `completed` | present | present | present *or* `null` | `null` |
| `failed` | usually `null` | usually `null` | `null` | present |

A completed analysis with `feedback: null` is a normal outcome: the numbers are
produced without a language model, so a feedback outage degrades an analysis to
measurements rather than losing it.

**A `null` metric means "not measurable from this recording", never zero.** See
[speech-analysis.md](speech-analysis.md) for which metrics need what. `metrics`
and `feedback` are separate objects — measured arithmetic and generated prose —
and `provenance` says which provider produced each, including `is_mock`.

There is deliberately no overall score, grade or percentage, and nothing about
pronunciation.

| Failure | Status | Code |
| --- | --- | --- |
| Recording exists, never analysed | 404 | `ANALYSIS_NOT_FOUND` |
| Unknown recording | 404 | `RECORDING_NOT_FOUND` |
| Malformed recording id | 422 | `VALIDATION_ERROR` |

### `POST /api/v1/recordings/{recording_id}/audio-analysis`

Starts the **deterministic audio analysis** of a stored recording: pitch,
detected range, pitch stability, loudness and spectral characteristics, measured
from the signal.

This is a different resource from `/analysis` above, not a variant of it. That
one transcribes and counts words; this one measures audio, needs no provider and
no credentials, and runs independently. Neither affects the other, and nothing
combines their results — see [architecture.md](architecture.md).

Returns `202` with `status: "pending"` when work is queued, `200` when an
already-finished analysis is handed back. The measurement runs in the
background, so a measurement failure never appears here.

### `GET /api/v1/recordings/{recording_id}/audio-analysis`

```json
{
  "audio_analysis_id": "…",
  "recording_id": "…",
  "status": "completed",
  "error_code": null,
  "pitch_point_count": 431,
  "summary": {
    "duration_seconds": 10.4,
    "settings": { "sample_rate_hz": 44100, "frame_length_samples": 4096, "…": "…" },
    "range": {
      "lowest_note": "G2", "highest_note": "C5", "semitone_span": 29,
      "lowest_frequency_hz": 98.0, "highest_frequency_hz": 523.25
    },
    "stability": {
      "voiced_ratio": 0.74, "voiced_frames": 320, "total_frames": 431,
      "mean_cents_deviation": -17.2, "cents_std": 21.4, "in_tune_ratio": 0.82,
      "unstable_sections": []
    },
    "loudness": { "rms": 0.18, "peak": 0.92, "dynamic_range_db": 14.2 },
    "spectral": { "centroid_hz": 1420.5, "flatness": 0.031, "…": "…" }
  }
}
```

Which fields are populated by `status`:

| `status` | `summary` | `error_code` |
| --- | --- | --- |
| `pending`, `analyzing` | `null` | `null` |
| `completed` | present | `null` |
| `failed` | `null` | present |

**A `null` measurement means the signal did not support it, never zero.** A
recording with nothing voiced has `range: null` — not a range of zero semitones.

`settings` is published because the numbers are only interpretable against it: a
range measured at a 0.80 clarity threshold is not the same measurement as one
taken at 0.90.

There is deliberately no score, no grade, and no timbre label.

| Failure | Status | Code |
| --- | --- | --- |
| Recording exists, audio never analysed | 404 | `AUDIO_ANALYSIS_NOT_FOUND` |
| Decoded fine, no reliable pitch | 200, `status: "failed"` | `INSUFFICIENT_PITCH_SIGNAL` |
| Undecodable file | 200, `status: "failed"` | `AUDIO_UNSUPPORTED` |
| Too short to measure | 200, `status: "failed"` | `AUDIO_TOO_SHORT` |

### `GET /api/v1/recordings/{recording_id}/audio-analysis/pitch`

The pitch timeline, for the graph.

```json
{
  "total_points": 431,
  "returned_points": 431,
  "decimation": 1,
  "points": [
    { "timestamp_seconds": 1.42, "frequency_hz": 440.12, "midi_note": 69,
      "note_name": "A4", "cents": 0.47, "confidence": 0.96 }
  ]
}
```

**Only voiced frames appear.** Unvoiced audio — silence, noise, consonants,
anything below the clarity threshold — produces no point at all rather than a
point with null fields, so every point returned was measured. A gap between
consecutive timestamps is therefore meaningful: draw it as a gap and never
interpolate across it. The share of the recording that was voiced is
`stability.voiced_ratio` on the summary.

`max_points` (default 1000, max 50000) caps the response by taking every n-th
point; `decimation` reports the factor used. A five-minute recording produces
around 13 000 voiced frames, which no graph can draw.

Available only once the analysis has completed; otherwise `404`
`AUDIO_ANALYSIS_NOT_FOUND`.

### `GET /api/v1/recordings/{recording_id}/audio-analysis/notes`

How the recording's pitched time was divided between musical notes.

```json
{
  "voiced_seconds": 5.84,
  "total_frames": 253,
  "in_tune_cents": 25.0,
  "notes": [
    {
      "midi_note": 60,
      "note_name": "C4",
      "duration_seconds": 1.84,
      "percentage_of_voiced_time": 31.2,
      "frame_count": 92,
      "average_cents": -4.1,
      "mean_abs_cents": 8.7,
      "in_tune_ratio": 0.84
    }
  ]
}
```

A companion to `/pitch` rather than a replacement: that path returns the
timeline frame by frame for drawing, this one returns it aggregated by note for
reading. Both derive from the same stored measurement, and the aggregation
happens server-side so a browser never downloads thousands of points to build a
table of a few rows.

**`percentage_of_voiced_time` is a share of pitched time, not of the
recording.** Silence and unvoiced audio are excluded from the denominator, so
the percentages sum to 100. **`duration_seconds` counts one analysis hop per
frame**, not one frame length — frames overlap, and charging each its full
length would multiply every duration.

**An empty `notes` list means no notes were detected.** That is not a successful
measurement of zero notes and must not be rendered as an empty table.

`in_tune_ratio` is the share of a note's frames within `in_tune_cents` of it —
the same threshold the recording-level figure uses. It is a measurement against
a stated threshold, not a grade.

Notes are ordered by duration descending, with the lower MIDI note first on a
tie, so the same analysis always renders the same way.

Available only once the analysis has completed; otherwise `404`
`AUDIO_ANALYSIS_NOT_FOUND`.

### `POST /api/v1/recordings/{recording_id}/audio-analysis/feedback`

Ask a language model to explain the measured audio in plain language.

**The model is not given the recording.** It receives a structured set of
measurements the analysis already computed and nothing else — no audio, no
path, no filename, no recording id. It explains numbers; it does not produce
them, and it is instructed never to state one that was not supplied. See
[ai.md](ai.md).

**Nothing is generated automatically.** This is an explicit request, and
repeating it is safe: feedback already written, or already being written, is
returned as-is, so a client cannot run up a provider bill by re-rendering.

Returns `202` while generation runs and `200` when feedback already existed.
Poll `GET` on the same path.

| Failure | Status | Code |
| --- | --- | --- |
| No reliable pitch in the recording | 422 | `INSUFFICIENT_PITCH_SIGNAL` |
| No completed audio analysis | 404 | `AUDIO_ANALYSIS_NOT_FOUND` |
| No feedback provider configured | 503 | `ANALYSIS_NOT_CONFIGURED` |

`INSUFFICIENT_PITCH_SIGNAL` here is the guard that matters: ordinary speech, a
whisper or a noisy room is **refused before a provider is called**, so a vocal
assessment can never be invented from a recording that contained no singing.

### `GET /api/v1/recordings/{recording_id}/audio-analysis/feedback`

```json
{
  "status": "completed",
  "error_code": null,
  "feedback": {
    "summary": "…",
    "strengths": ["…"],
    "areas_to_improve": [],
    "pitch_observations": ["…"],
    "range_observations": ["…"],
    "note_observations": [],
    "audio_observations": [],
    "exercises": [],
    "practice_plan": [],
    "provenance": { "provider": "mock", "model": null, "is_mock": true }
  }
}
```

`status` is `not_requested`, `generating`, `completed` or `failed`. A recording
with no completed analysis has no record to carry a status, so "unavailable"
surfaces as a `404` with a specific `error_code` rather than as a fifth value.

**A failed generation is a successful response**: `200` with
`status: "failed"` and an `error_code`. The measurements are unaffected — they
were computed without a model, and a provider outage costs the prose and
nothing else.

An empty section means the model had nothing to say about it, and should be
omitted rather than rendered as "none". `provenance.is_mock` marks demo data;
it must never be presented as a real interpretation.

---

## Planned

These are the contracts later phases will implement. They are documented here so
the frontend types and backend schemas are designed against the same shape.

### `POST /api/v1/audio/upload` — Phase 1

`multipart/form-data` with a single `file` field.

Validation: MIME type and extension (`.mp3`, `.wav`, `.m4a` where feasible),
size ≤ `MAX_AUDIO_SIZE_MB`, duration ≤ `MAX_AUDIO_DURATION_SECONDS`.

**201 Created**

```json
{ "recording_id": "…", "status": "uploaded" }
```

### `POST /api/v1/analysis/{recording_id}` — Phase 2

Starts analysis in the background.

**202 Accepted**

```json
{ "analysis_id": "…", "status": "processing" }
```

### `GET /api/v1/analysis/{analysis_id}` — Phase 2

**200 OK**

```json
{
  "status": "completed",
  "summary": {
    "lowest_note": "G2",
    "highest_note": "C5",
    "pitch_accuracy": 82.4,
    "average_cents_deviation": -17.2,
    "voiced_ratio": 0.74
  }
}
```

`status` is one of `processing`, `completed`, `failed`.

### `POST /api/v1/analysis/{analysis_id}/ai-feedback` — Phase 6

Generates the LLM interpretation of an existing analysis. Returns the structured
object documented in [ai.md](ai.md). Requires `ANTHROPIC_API_KEY`.

---

## Errors

Handled failures use a stable envelope:

```json
{
  "status": "failed",
  "error_code": "INSUFFICIENT_PITCH_SIGNAL",
  "message": "We could not detect enough reliable pitch information."
}
```

| `error_code` | Meaning |
| --- | --- |
| `UNSUPPORTED_FORMAT` | File type not accepted |
| `FILE_TOO_LARGE` | Exceeds `MAX_AUDIO_SIZE_MB` |
| `AUDIO_TOO_LONG` | Exceeds `MAX_AUDIO_DURATION_SECONDS` |
| `CORRUPTED_AUDIO` | File could not be decoded |
| `INSUFFICIENT_PITCH_SIGNAL` | Too few reliably voiced frames to analyse |
| `AUDIO_UNSUPPORTED` | The stored file could not be decoded |
| `AUDIO_ANALYSIS_FAILED` | Audio analysis failed with no more specific cause |
| `AUDIO_ANALYSIS_NOT_FOUND` | The recording exists but its audio has never been analysed |
| `AI_UNAVAILABLE` | LLM provider not configured or unreachable |

Speech analysis (Phase 7) adds the following. The HTTP status is listed because
these separate "our deployment is not set up", "the provider is having a bad
day" and "the recording had nothing in it", which callers retry differently.

| `error_code` | HTTP | Meaning |
| --- | --- | --- |
| `ANALYSIS_NOT_CONFIGURED` | 503 | No speech-to-text provider is configured on this server |
| `ANALYSIS_PROVIDER_UNAVAILABLE` | 503 | The provider could not be reached |
| `ANALYSIS_PROVIDER_TIMEOUT` | 504 | The provider did not answer in time |
| `ANALYSIS_RATE_LIMITED` | 429 | The provider refused the request under its own rate limits |
| `ANALYSIS_PROVIDER_ERROR` | 502 | The provider answered with an error or an unusable response |
| `TRANSCRIPT_EMPTY` | 422 | Transcription succeeded and found no speech |
| `ANALYSIS_NOT_FOUND` | 404 | The recording exists but has never been analysed |

The first six are *analysis* outcomes, not request outcomes: they are persisted
on the analysis record and returned by `GET` inside a `200` response, never as
an HTTP error status. `ANALYSIS_NOT_FOUND` is the exception — it describes the
request, and is a real 404.

Credentials (Step 10.2) add two more. Both describe the *request*, so both are
real HTTP statuses:

| `error_code` | HTTP | Meaning |
| --- | --- | --- |
| `CREDENTIAL_NOT_FOUND` | 404 | No key with that id belongs to the caller |
| `LAST_CREDENTIAL` | 409 | Refusing to revoke an owner's only remaining key |

`CREDENTIAL_NOT_FOUND` is deliberately the same answer for "no such key" and
"somebody else's key", including somebody else's *last* key — so neither the
existence of an id nor another identity's key count can be probed.

A provider's own error text never reaches a client. Each code carries our
wording; the vendor's exception class or status is logged as a short `reason`
and goes no further. See `backend/app/services/ai/errors.py`.

Both real adapters translate at their own boundary — no Deepgram or Anthropic
exception class escapes into the application:

| Situation | Deepgram | Claude | Code |
| --- | --- | --- | --- |
| Provider selected, no API key | — | — | `ANALYSIS_NOT_CONFIGURED` |
| 401 / 403 | `ApiError` | `AuthenticationError`, `PermissionDeniedError` | `ANALYSIS_NOT_CONFIGURED` |
| 429 | `ApiError` | `RateLimitError` | `ANALYSIS_RATE_LIMITED` |
| Timeout | `httpx.TimeoutException`, 408 | `APITimeoutError` | `ANALYSIS_PROVIDER_TIMEOUT` |
| Unreachable, 5xx | `httpx.TransportError`, 502/503/504 | `APIConnectionError`, 5xx | `ANALYSIS_PROVIDER_UNAVAILABLE` |
| Other 4xx, unusable response | `BadRequestError`, `ParsingError` | other 4xx, schema mismatch, refusal | `ANALYSIS_PROVIDER_ERROR` |
| Success with no speech | empty transcript | — | `TRANSCRIPT_EMPTY` |

`GET /api/v1/config` never exposes `DEEPGRAM_API_KEY`, `ANTHROPIC_API_KEY`, or
the configured model names.

Codes are added as the phases that raise them land; the list above is the
planned vocabulary.
