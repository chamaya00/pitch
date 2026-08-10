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

## Backend layout

| Path | Responsibility |
| --- | --- |
| `app/main.py` | App factory, CORS, router mounting, lifespan logging |
| `app/version.py` | Single source of truth for the backend version |
| `app/api/v1/router.py` | Aggregates versioned routers |
| `app/api/v1/routes/` | One module per resource (`health`, later `audio`, `analysis`) |
| `app/core/config.py` | Environment-backed settings (`get_settings`, cached) |
| `app/core/logging.py` | JSON log formatter, stdlib only |
| `app/schemas/` | Pydantic request/response models — the API contract |
| `app/models/` | Database models (Phase 7) |
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

## Frontend layout

| Path | Responsibility |
| --- | --- |
| `app/` | App Router routes, layout, global styles |
| `components/` | Presentational and container components |
| `components/ui/` | Primitives (`Button`, …) |
| `components/record/` | Microphone recorder and the live pitch display |
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

Per-frame data does not go through React. Pitch frames arrive ~30 times a
second and are delivered to subscribers that write to the DOM or a canvas
directly; re-rendering the tree at that rate to move one number is how a live
display starts dropping frames. React state holds lifecycle, errors and the
finished file, plus a clock that only changes on the whole second.

No realtime backend was added for this: no WebSocket, no Redis, no queue, and
no per-frame storage anywhere.

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
