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
| 7 | User history: users, recordings, analyses, comparison, progress chart | Partly — see below |
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

**Not yet built** in Phase 7: the progress chart over time. It is the last
item in the phase and is not claimed to exist.

Comparison deliberately does **not** produce an overall figure, a ranking or
a judgement about which recording is better; four of its seven metrics have no
desirable direction at all. See [api.md](api.md) and
[limitations.md](limitations.md).
