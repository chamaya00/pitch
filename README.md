# VocalLens

**Understand your voice. Understand your music.**

VocalLens analyses a vocal recording or a song and reports pitch, musical notes,
vocal range, stability, loudness and spectral characteristics — then uses an LLM
to explain those measurements in plain language.

> **Core principle:** every number comes from deterministic audio analysis.
> The LLM interprets measurements; it never invents them.

VocalLens is an audio-analysis tool. It is **not** a medical or professional
vocal assessment and makes no claims about vocal health.

---

## Status

**Phase 0 — project foundation (complete).**
Repository structure, FastAPI backend with a health endpoint, Next.js frontend
with the homepage and an API connectivity indicator, environment configuration,
Docker setup, tests and lint/type checks.

Audio upload, pitch detection and AI feedback are not implemented yet. See
[docs/roadmap.md](docs/roadmap.md) for the phase plan.

## Repository layout

```
.
├── frontend/          Next.js 16 + TypeScript + Tailwind CSS 4 (App Router)
│   ├── app/           Routes and layouts
│   ├── components/    UI components
│   ├── hooks/         Client-side React hooks
│   ├── lib/           API client and configuration
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
- [docs/audio-analysis.md](docs/audio-analysis.md) — planned analysis pipeline
- [docs/ai.md](docs/ai.md) — LLM interpretation layer
- [docs/limitations.md](docs/limitations.md) — what VocalLens cannot tell you
- [docs/roadmap.md](docs/roadmap.md) — development phases
