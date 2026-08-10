# VocalLens

**Hear how you speak.**

Record from your microphone or upload a file. VocalLens measures the recording
two ways — what you *said* and how you said it (transcript, pace, pauses, filler
words), and what the *audio* was (pitch, detected range, pitch stability,
loudness, spectrum) — then uses an LLM to explain the speech numbers in plain
language. While you record, it shows the pitch of your voice live, detected in
your browser.

> **Core principle:** every number comes from deterministic audio analysis.
> The LLM interprets measurements; it never invents them.

VocalLens is an audio-analysis tool. It is **not** a medical or professional
vocal assessment and makes no claims about vocal health.

---

## Status

Implemented and working end to end:

- **Microphone recording** — captured as raw PCM in the browser and written as a
  WAV, uploaded only when you ask.
- **Live Vocal Practice** — while you record: the detected note, a tuner meter
  showing flat or sharp with the deviation in cents, a scrolling pitch trace,
  a rolling pitch-consistency figure, the range covered this session, and an
  optional target note to practise against. All computed locally; audio never
  leaves the page during recording.
- **Upload** — WAV/MP3, validated by content rather than by filename, stored
  with server-generated names.
- **Speech analysis** — transcription, then deterministic metrics counted from
  the transcript: speaking time, words per minute, articulation rate, pauses,
  and filler words split into hesitations and discourse markers.
- **Audio analysis of an uploaded recording** — per-frame pitch with note
  conversion and cents deviation, detected range, pitch stability, loudness
  (RMS, peak, dynamic-range estimate, clipping) and spectral characteristics
  (centroid, bandwidth, rolloff, zero-crossing rate, flatness), plus a pitch
  timeline for the graph.
- **AI feedback** — an LLM explaining the speech measurements. It never produces
  a number and never produces a score.

The speech and audio analyses are **separate**: separate endpoints, separate
records, separate sections in the UI. There is no combined "voice score", and
none is planned — see [docs/architecture.md](docs/architecture.md).

Providers are selected by configuration and are only needed for speech analysis;
audio analysis works on a deployment with no credentials at all. With `mock`
selected the speech output is demo data, and the UI says so in a banner rather
than passing it off as analysis.

**Not implemented yet:** vocal/instrument separation, song melody extraction,
song key estimation, song compatibility, transpose recommendation, and progress
tracking across recordings. See [docs/roadmap.md](docs/roadmap.md).

Every result is an **audio-based estimate from one recording**. Nothing here has
been validated against annotated reference data; see
[docs/limitations.md](docs/limitations.md).

### Recording in the browser

The microphone recorder runs entirely locally. Audio is captured as raw PCM,
analysed for pitch in the page, and written as a WAV — nothing is sent to any
provider, model or VocalLens endpoint while recording. The finished recording
is uploaded only when you press **Upload for analysis**.

Recording needs a secure context, so use `http://localhost` in development or
HTTPS anywhere else; browsers do not grant microphone access otherwise.

## Repository layout

```
.
├── frontend/          Next.js 16 + TypeScript + Tailwind CSS 4 (App Router)
│   ├── app/           Routes and layouts
│   ├── components/    UI components
│   ├── hooks/         Client-side React hooks
│   ├── lib/           API client, pitch detection, WAV encoding
│   ├── public/        Static assets, including the audio capture worklet
│   ├── tests/         node --test suites for the pure logic modules
│   └── types/         Shared API response types
├── backend/           FastAPI + Python 3.11
│   ├── app/
│   │   ├── api/       HTTP routes (versioned under /api/v1)
│   │   ├── core/      Configuration and logging
│   │   ├── models/    Database models (Phase 7)
│   │   ├── schemas/   Pydantic request/response schemas
│   │   └── services/  audio/ · analysis/ · ai/
│   └── tests/
├── docs/              Architecture, API, audio analysis, limitations
├── scripts/           Developer helper scripts
└── docker-compose.yml
```

## Requirements

- Node.js 20+ (developed against 22)
- Python 3.11+
- Docker (optional, for the compose setup)

## Getting started

```bash
cp .env.example .env
```

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

- Health: <http://localhost:8000/health>
- API docs: <http://localhost:8000/docs>

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>. The homepage shows a live API status indicator;
if it reads "API unavailable", the backend is not running or
`NEXT_PUBLIC_API_URL` points somewhere else.

### Docker

```bash
docker compose up --build
```

Starts PostgreSQL, the backend on `:8000` and the frontend on `:3000`.
PostgreSQL is provisioned ahead of Phase 7 and is not used yet.

## Checks

Run these before every commit — all are also listed in
[docs/architecture.md](docs/architecture.md).

```bash
# Backend
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app

# Frontend
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

Or run everything at once:

```bash
./scripts/check.sh
```

## Configuration

All configuration comes from the environment; see [`.env.example`](.env.example).
Secrets (`ANTHROPIC_API_KEY`, `DATABASE_URL`) are never hardcoded or committed.

| Variable | Default | Used from |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | Phase 0 |
| `LOG_LEVEL` | `INFO` | Phase 0 |
| `CORS_ORIGINS` | `http://localhost:3000` | Phase 0 |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Phase 0 |
| `MAX_AUDIO_SIZE_MB` | `50` | Phase 1 |
| `MAX_AUDIO_DURATION_SECONDS` | `300` | Phase 1 |
| `DATABASE_URL` | — | Phase 7 |
| `ANTHROPIC_API_KEY` | — | Phase 6 |

## Documentation

- [docs/architecture.md](docs/architecture.md) — system design and conventions
- [docs/api.md](docs/api.md) — endpoint reference
- [docs/audio-analysis.md](docs/audio-analysis.md) — live pitch detection, and the planned backend pipeline
- [docs/ai.md](docs/ai.md) — LLM interpretation layer
- [docs/limitations.md](docs/limitations.md) — what VocalLens cannot tell you
- [docs/roadmap.md](docs/roadmap.md) — development phases
