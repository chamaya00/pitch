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

### `GET /api/v1/analysis/{analysis_id}/pitch` — Phase 3

Pitch timeline for the graph: `timestamp`, `frequency`, `midi_note`,
`note_name`, `cents`, `confidence` per frame.

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
