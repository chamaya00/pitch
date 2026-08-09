# Roadmap

Phases are built in order. A phase is done only when implementation, UI, API,
tests, error states, lint, type checks and documentation are all in place.

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Project foundation: structure, config, health check, homepage, docs | ✅ Complete |
| 1 | Audio upload: UI, endpoint, validation, storage, metadata | Next |
| 2 | Pitch engine: preprocessing, detection, confidence filter, Hz→MIDI→note→cents | Planned |
| 3 | Analysis dashboard: summary cards, range, accuracy, pitch timeline, note distribution | Planned |
| 4 | Microphone recording in the browser | Planned |
| 5 | Advanced metrics: RMS, peak, spectral features | Planned |
| 6 | Claude integration: structured payload, service, feedback UI | Planned |
| 7 | User history: users, recordings, analyses, comparison, progress chart | Planned |
| 8 | Song analyser: key, BPM, melody/range estimation, limitations messaging | Planned |
| 9 | Song compatibility: range overlap, difficulty, transpose suggestions | Planned |
| 10 | Production polish: auth, security hardening, error pages, performance, deployment | Planned |

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
