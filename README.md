# VocalLens

**Hear how you sound.**

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
  a rolling pitch-consistency figure, the range covered this session, the key
  you seem to be singing in, and an optional target note to practise against.
  All computed locally; audio never leaves the page during recording.
- **Upload** — WAV/MP3, validated by content rather than by filename, stored
  with server-generated names.
- **Speech analysis** — transcription, then deterministic metrics counted from
  the transcript: speaking time, words per minute, articulation rate, pauses,
  and filler words split into hesitations and discourse markers.
- **Audio analysis of an uploaded recording** — per-frame pitch with note
  conversion and cents deviation, detected range, pitch stability, loudness
  (RMS, peak, dynamic-range estimate, clipping) and spectral characteristics
  (centroid, bandwidth, rolloff, zero-crossing rate, flatness), plus a pitch
  timeline for the graph with the stretches where the pitch moved shaded on it.
- **Musical key** — the key implied by what was sung, folded from the same pitch
  timeline. It refuses rather than guesses: a hum, an arpeggio or a wandering
  phrase is reported as "not measured" with the reason and the evidence shown.
  The same measurement runs live in the practice card, implemented a second time
  in the browser because microphone audio never leaves it — the two are held to
  each other by a shared fixture table, are labelled apart, and are never shown
  side by side.
- **Recording history, comparison and progress** — an owner's recordings over
  time, two of them side by side, and their measurements charted. Deterministic
  arithmetic throughout: no grade, no level, no trend line.
- **Song compatibility** — describe a song by its lowest and highest note and
  see how much of it falls inside the range a recording covered, the distance
  out at each end, and the shift that would bring the rest in. **The song's
  numbers are typed, not measured**, and every range says which it is: there is
  no reference audio anywhere in this product. No compatibility score, and no
  field that could hold one.
- **AI feedback** — an LLM explaining the speech measurements. It never produces
  a number and never produces a score.

**Installable.** A manifest, an icon set and a service worker make VocalLens
installable to a home screen or a dock, launching in its own window. It caches
its own static files and **never a measurement** — a cached number would be
presented as current when it is not. There is no offline mode: the app needs a
network to open, and says so on a static offline page when it cannot reach one.
Installing and the microphone both require HTTPS — see below.

The speech and audio analyses are **separate**: separate endpoints, separate
records, separate sections in the UI. There is no combined "voice score", and
none is planned — see [docs/architecture.md](docs/architecture.md).

Providers are selected by configuration and are only needed for speech analysis;
audio analysis works on a deployment with no credentials at all. With `mock`
selected the speech output is demo data, and the UI says so in a banner rather
than passing it off as analysis.

**Not implemented, and not planned:** vocal/instrument separation, song melody
extraction, tempo and beat tracking, and musical transcription. **This product
accepts no song audio, no reference track and no second audio input** — song
compatibility works from a range you type, which is the input model chosen in
[docs/phase-9-specification.md §3A](docs/phase-9-specification.md#3a-the-decision-2026-08-20)
and whose cost is stated there and on every result. Because there is no melody
on either side, nothing here can say *which notes* of a song will be hard, or
whether the notes you sang were the intended ones — the key estimate above is
the key of *what you sang*. See [docs/roadmap.md](docs/roadmap.md).

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
│   │   ├── db/        Connection pool, migrations, filesystem import
│   │   ├── schemas/   Pydantic request/response schemas
│   │   └── services/  audio/ · analysis/ · audio_analysis/ · ai/
│   │                  owners/ · recordings/ · comparison/ · progress/
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

Starts PostgreSQL, the backend, the frontend and an nginx proxy. **The proxy is
the only published port** — open <http://localhost>. The API, the web app and
the database are reachable only on the internal network, which is what makes the
backend's trusted-proxy setting safe; see
[docs/architecture.md](docs/architecture.md). The proxy speaks HTTP: TLS
termination is an external responsibility. Every page is served with a
Content-Security-Policy built around a nonce minted for that request, which is
why pages render per request rather than from the prerender cache.
PostgreSQL is the source of truth for recordings, analyses, owners and the
credentials that resolve to them; the backend applies its migrations at startup
and will not serve the recording endpoints without it.

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

**With a database, or the SQL is not checked.** The PostgreSQL suites skip when
`TEST_DATABASE_URL` is unset, and that is 185 tests — the whole PostgreSQL half
of the contract suite, every migration, every statement-shape assertion, the
shared rate limiter and the connection budget. A run without one is worth having
and is not a run that has checked the repository's statements:

```bash
# A scratch database. The suites TRUNCATE between tests, so never a real one.
createdb vocallens_test
TEST_DATABASE_URL=postgresql://localhost/vocallens_test ./scripts/check.sh
```

A run that must not skip them sets `REQUIRE_DATABASE_TESTS=1`, which turns a
missing `TEST_DATABASE_URL` into a failed run rather than 185 quiet skips.
[`.github/workflows/checks.yml`](.github/workflows/checks.yml) sets both and runs
this same script on every push and pull request.

## Serving it over HTTPS

The microphone and the installable app both need a secure context, so neither
works over plain HTTP anywhere but `localhost`. TLS can be terminated at the
bundled proxy:

```bash
export PUBLIC_ORIGIN=https://vocallens.example   # required, no default
export PUBLIC_HOST=vocallens.example
export TLS_CERT_DIR=/etc/letsencrypt/live/vocallens.example
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d
```

Port 80 then serves the ACME `http-01` challenge and redirects everything else;
port 443 serves the app with HSTS. **No ACME client is bundled** — point an
external certbot at the challenge webroot.

Deployments that already terminate TLS upstream — a load balancer, an ingress
controller — should use the base compose file alone. Two terminators are not
more secure, only one more certificate to let expire.

`scripts/verify-proxy.sh start-tls` runs the same configuration locally with a
self-signed certificate, if you want to see it work before pointing a domain at
it.

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
| `DATABASE_URL` | — | **required** since Step 7M |
| `ANTHROPIC_API_KEY` | — | Phase 6 |

## Documentation

- [docs/architecture.md](docs/architecture.md) — system design and conventions
- [docs/api.md](docs/api.md) — endpoint reference
- [docs/audio-analysis.md](docs/audio-analysis.md) — live pitch detection, the backend pipeline, and musical key
- [docs/ai.md](docs/ai.md) — LLM interpretation layer
- [docs/limitations.md](docs/limitations.md) — what VocalLens cannot tell you
- [docs/roadmap.md](docs/roadmap.md) — development phases

## Licence

[MIT](LICENSE). Use it, change it, ship it; keep the copyright notice, and
expect no warranty.

The absence of this file was found by the Step 10.20 audit rather than by anyone
asking: the repository was public and carried no licence, which meant in law that
nobody could use it, and nothing had written that down. It was the last of Phase
10's outstanding items to need a decision rather than an engineer.
