# API reference

Base URL: `http://localhost:8000`
Versioned prefix: `/api/v1`

Interactive documentation is served by FastAPI at `/docs` (Swagger UI) and
`/redoc`. The machine-readable schema is at `/openapi.json`.

---

## Implemented

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
