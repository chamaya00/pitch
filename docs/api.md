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

A provider's own error text never reaches a client. Each code carries our
wording; the vendor's exception class or status is logged as a short `reason`
and goes no further. See `backend/app/services/ai/errors.py`.

Codes are added as the phases that raise them land; the list above is the
planned vocabulary.
