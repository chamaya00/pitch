# VocalLens — working notes

Guidance for anyone (human or agent) working in this repository.

## The one rule that matters

**Deterministic analysis produces numbers. The LLM explains them.**

Never ask a model to estimate, invent or "fill in" a measurement. If a value is
shown in the UI, it must be traceable to `backend/app/services/analysis/`.
Never present mock data as real analysis.

## Layout

- `frontend/` — Next.js 16 (App Router), TypeScript, Tailwind CSS 4
- `backend/` — FastAPI, Python 3.11
- `docs/` — architecture, API, audio analysis, AI layer, limitations, roadmap
- `scripts/check.sh` — runs every check

See [docs/architecture.md](docs/architecture.md) for conventions.

## Commands

```bash
# Backend (from backend/)
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format .
.venv/bin/mypy app
uvicorn app.main:app --reload --port 8000

# Frontend (from frontend/)
npm run dev
npm run lint && npm run typecheck && npm run build

# Everything
./scripts/check.sh
```

## Workflow

Work in phases (see [docs/roadmap.md](docs/roadmap.md)) and, within a phase:
understand → plan → implement → test → verify → document.

- Inspect existing code before changing it; reuse what is there.
- Small, focused commits (`feat:`, `fix:`, `test:`, `refactor:`, `docs:`).
- No large architectural change without saying so first.
- Add a dependency only when the phase that needs it lands.
- Never delete a test to make a suite pass.
- When something breaks: reproduce → read logs → find the root cause → make the
  smallest fix → re-run the regression.

## Non-negotiables

- No medical claims, no vocal-health diagnosis, no "professional assessment".
- Pitch accuracy is *pitch consistency in this recording*, not singing ability.
- Detected vocal range is what this recording contained, not a physiological
  maximum.
- RMS/peak are not LUFS. Do not imply mastering-grade loudness measurement.
- Do not label timbre ("bright", "breathy") from unvalidated spectral numbers.
- Analysis failure returns a documented `error_code`; it never crashes the API.
- Secrets come from the environment. Never commit `.env`; never log keys, raw
  audio or private user data.
- Document algorithmic choices (detector, thresholds, sample rate, frame/hop) in
  `docs/audio-analysis.md` as they are implemented, including what didn't work.
