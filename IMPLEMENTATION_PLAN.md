# VocalLens — Master Specification & Implementation Plan

## 0. Role

You are the lead software engineer, AI engineer, audio-processing engineer, and technical architect for this project. Your task is to build **VocalLens**, a modern web application that analyzes a user's recorded voice or a song and provides detailed vocal/music analysis.

Do **not** try to build the entire application in one step. Work incrementally. For every phase:

1. Inspect the existing codebase.
2. Understand the current architecture.
3. Create a short implementation plan.
4. Implement the feature.
5. Run tests.
6. Run lint/type checks.
7. Fix errors.
8. Update documentation.
9. Summarize what changed.
10. State what should be built next.

Never silently make large architectural changes. When requirements are ambiguous, choose the simplest production-quality solution and clearly state the assumption.

## 1. Product vision

**VocalLens** — *Understand your voice. Understand your music.*

The application allows users to:

- upload a vocal recording
- record their voice using a microphone
- upload a song/audio file
- analyze pitch
- detect musical notes
- calculate pitch accuracy
- estimate vocal range
- visualize pitch over time
- analyze loudness/dynamics
- analyze basic timbre characteristics
- detect potentially unstable notes
- compare multiple recordings
- receive AI-generated vocal feedback
- eventually analyze whether a song is suitable for the user's vocal range

The application should feel like a combination of a vocal practice tool, a music analysis tool, a personal vocal tracker, and an AI singing coach.

## 2. Important product principle

Separate **deterministic audio analysis** from **AI interpretation**.

The audio engine is responsible for:

- pitch detection
- note detection
- frequency conversion
- cents deviation
- vocal range
- loudness
- spectral features
- timing
- statistical measurements

Claude/LLM is responsible for:

- explaining results
- identifying patterns
- generating personalized feedback
- suggesting practice exercises
- comparing recordings
- explaining weaknesses
- generating natural-language summaries

**Never** ask the LLM to invent numerical audio measurements. All numerical measurements must originate from the audio-analysis pipeline.

## 3. Target users

1. Beginner singers
2. Casual singers
3. Karaoke users
4. People practicing singing
5. Musicians
6. Users curious about their vocal range

The application should prioritize simplicity. A user should be able to upload an audio file and understand the result without knowing music theory.

## 4. MVP scope

Audio input support:

- MP3
- WAV
- M4A if technically feasible
- microphone recording

Limits:

- Maximum initial file size: 50 MB
- Maximum analysis duration: 5 minutes

Reject unsupported or corrupted files gracefully.

## 5. MVP analysis

For every audio file calculate:

### Pitch

- fundamental frequency (Hz)
- musical note
- octave
- MIDI note
- cents deviation

Example: `261.63 Hz → C4 → MIDI 60 → ~0 cents deviation`

### Vocal range

- lowest detected note
- highest detected note
- total semitone range
- approximate range in musical notation

Example: `G2 – C5`

### Pitch stability

- percentage of voiced frames
- pitch variance
- average cents deviation
- standard deviation of cents
- unstable sections

### Loudness

- RMS
- peak amplitude
- approximate dynamic range

Do not claim that these measurements represent professional LUFS mastering measurements unless proper LUFS analysis is implemented.

### Basic spectral analysis

- spectral centroid
- spectral bandwidth
- spectral rolloff
- zero-crossing rate
- spectral flatness

Use these only as measurable audio characteristics. Do not automatically label someone as "bright", "dark", "breathy", etc. unless the classification method has been validated.

## 6. Technology stack

**Frontend**

- Next.js
- TypeScript
- Tailwind CSS
- modern React
- responsive design

**Backend**

- Python
- FastAPI

**Audio analysis**

- librosa
- numpy
- scipy

For pitch detection, evaluate suitable algorithms such as `librosa.pyin`, CREPE, or another reliable pitch-detection implementation. Do not blindly add heavy ML dependencies. Start with the simplest reliable method.

## 7. Architecture

Clean separation:

```text
frontend
  ↓
API
  ↓
audio processing service
  ↓
analysis result
  ↓
AI interpretation layer
  ↓
database
```

Recommended structure:

```text
vocallens/
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── hooks/
│   ├── lib/
│   └── types/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── audio/
│   │   │   ├── analysis/
│   │   │   └── ai/
│   │   └── main.py
│   │
│   └── tests/
│
├── docs/
│
├── scripts/
│
├── CLAUDE.md
├── README.md
└── docker-compose.yml
```

## 8. Database

Use PostgreSQL. Initial entities:

**User**

```text
id
email
name
created_at
```

**Recording**

```text
id
user_id
filename
duration
file_size
created_at
```

**Analysis**

```text
id
recording_id
lowest_note
highest_note
lowest_frequency
highest_frequency
pitch_accuracy
average_deviation
pitch_std
voiced_ratio
rms
peak
spectral_centroid
created_at
```

**PitchPoint**

```text
id
analysis_id
timestamp
frequency
midi_note
note_name
cents
confidence
```

Do not prematurely normalize everything. Optimize for clarity first.

## 9. API design

### `POST /api/v1/audio/upload`

Upload an audio file.

```json
{
  "recording_id": "...",
  "status": "uploaded"
}
```

### `POST /api/v1/analysis/{recording_id}`

Start analysis.

```json
{
  "analysis_id": "...",
  "status": "processing"
}
```

### `GET /api/v1/analysis/{analysis_id}`

Return analysis result.

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

### `GET /api/v1/analysis/{analysis_id}/pitch`

Return pitch timeline.

### `POST /api/v1/analysis/{analysis_id}/ai-feedback`

Generate AI interpretation.

## 10. Frontend — Home

Hero: **Understand your voice.**

Subtitle: *Upload a recording and discover your pitch, range, stability, and vocal patterns.*

Buttons:

- Upload audio
- Record voice

## 11. Upload component

Requirements:

- drag and drop
- file picker
- file validation
- progress indicator
- error handling
- audio preview

Do not upload automatically without user confirmation.

## 12. Recording component

Use the browser MediaRecorder API.

Requirements:

- microphone permission
- record
- pause
- resume
- stop
- playback
- retry

Display:

```text
00:14
Recording...
```

Use a simple waveform visualization if feasible.

## 13. Analysis dashboard

After analysis, show summary cards:

```text
Vocal Range
G2 – C5

Pitch Accuracy
82%

Lowest Note
G2

Highest Note
C5
```

## 14. Pitch graph

One of the most important UI elements.

- X axis: time
- Y axis: musical pitch

Overlay:

- detected pitch
- note boundaries
- confidence

Allow zoom, hover, and playback synchronization.

Example concept:

```text
C5 ────────────────●────
B4 ────────────●───────
A4 ───────●────────────
G4 ───●────────────────
F4
E4
D4
C4
    0s   2s   4s   6s
```

## 15. Pitch accuracy

Show:

```text
Pitch Accuracy
82%
```

Explain the metric. Do not present the number as an absolute measurement of "singing ability". Use language such as *"Pitch consistency in this recording"* rather than *"Your singing skill is 82%"*.

## 16. Note breakdown

```text
Note      Accuracy

C4        96%
D4        91%
E4        74%
F4        83%
G4        88%
A4        61%
```

Highlight notes with consistently high deviation.

## 17. Vocal range

```text
Your estimated range

G2 ━━━━━━━━━━━━━ C5

31 semitones
```

Explain: *This is the range detected in this recording, not necessarily your physiological maximum vocal range.* This distinction is important.

## 18. AI feedback

Use the Claude API. The backend sends structured analysis data.

Example input:

```json
{
  "range": {
    "lowest": "G2",
    "highest": "C5"
  },
  "pitch_accuracy": 82.4,
  "average_deviation": -17.2,
  "unstable_notes": [
    {
      "note": "A4",
      "average_deviation": -34
    }
  ],
  "spectral": {
    "centroid": 2180
  }
}
```

Prompt:

```text
You are a vocal practice coach.

Analyze the supplied objective audio measurements.

Do not invent measurements.

Do not diagnose medical conditions.

Do not make claims about vocal health.

Explain the results in beginner-friendly language.

Structure your response as:

1. Overall summary
2. Strong points
3. Areas to improve
4. Pitch observations
5. Range observations
6. Suggested practice exercises
7. One short practice plan

If the data is insufficient to support a conclusion, explicitly say so.
```

## 19. AI response format

Return structured JSON:

```json
{
  "summary": "...",
  "strengths": [],
  "areas_to_improve": [],
  "pitch_observations": [],
  "range_observations": [],
  "exercises": [],
  "practice_plan": []
}
```

Do not store arbitrary markdown as the canonical database representation. The frontend renders the structured response.

## 20. Safety

The application must **not**:

- diagnose vocal disorders
- diagnose medical conditions
- claim vocal damage
- claim professional-level vocal assessment
- make medical recommendations

If the data suggests something unusual, say:

> This analysis is only an audio-based estimate and is not a medical or professional vocal assessment.

## 21. Song analysis — phase 2

After the MVP works, support songs. User uploads `song.mp3` and the system analyzes:

- estimated key
- BPM
- pitch distribution
- detected vocal regions if possible
- estimated melody
- vocal range requirement

Important: do not assume the entire song is the singer's vocal track. Instrumental audio can interfere with pitch detection. Clearly communicate that full mixed-song analysis is less reliable than isolated vocal analysis.

## 22. Song compatibility

```text
Your range:
G2 – C5

Song estimated range:
B2 – D5
```

Calculate:

```text
Range overlap
Upper-range difficulty
Lower-range difficulty
```

Show:

```text
Estimated compatibility: Moderate
```

Do not call this an objective measure of whether someone "can sing the song".

## 23. Key transpose feature

```text
Original key: C

-5 -4 -3 -2 -1  0 +1 +2 +3 +4 +5
```

Display:

```text
Recommended starting point:
-2 semitones
```

This recommendation must be based on measurable range overlap.

## 24. Progress tracking

Allow users to compare recordings:

```text
July 1
Pitch consistency: 71%

July 15
Pitch consistency: 76%

August 1
Pitch consistency: 82%
```

Display a progress chart. Claude can explain: *"Your recent recordings show improved pitch consistency, particularly in the middle register."* Only make this statement when supported by stored data.

## 25. Testing requirements

Write unit tests for:

- Hz → MIDI
- MIDI → note name
- cents calculation
- vocal range
- pitch statistics
- confidence filtering
- empty audio
- silent audio
- corrupted audio

Example:

```text
440 Hz → A4
261.625 Hz → C4
```

Use tolerance where appropriate. Do not expect floating-point equality.

## 26. Audio edge cases

Handle:

- silence
- background noise
- very quiet recordings
- clipping
- extremely short audio
- unsupported formats
- corrupted files
- multiple simultaneous voices
- instrumental-heavy songs

Never crash the API because audio analysis failed. Return a useful error:

```json
{
  "status": "failed",
  "error_code": "INSUFFICIENT_PITCH_SIGNAL",
  "message": "We could not detect enough reliable pitch information."
}
```

## 27. Performance

Do not block the HTTP request for long-running analysis. Initial architecture:

```text
API
 ↓
Background job
 ↓
Analysis
 ↓
Database
```

For the MVP, a simple background task is acceptable. If processing becomes substantial, introduce Redis and Celery/RQ/Arq — but not until needed.

## 28. Observability

Add structured logging, analysis duration, errors, and processing status.

Log:

```text
analysis_started
analysis_completed
analysis_failed
ai_feedback_generated
```

Never log API keys, private user data, or raw audio contents.

## 29. Security

Implement:

- file type validation
- file size limits
- safe temporary-file handling
- sanitized filenames
- API authentication architecture
- environment variables for secrets

Never hardcode `ANTHROPIC_API_KEY` or `DATABASE_URL`.

## 30. Environment variables

```text
DATABASE_URL=
ANTHROPIC_API_KEY=
NEXT_PUBLIC_API_URL=
MAX_AUDIO_SIZE_MB=50
MAX_AUDIO_DURATION_SECONDS=300
```

Provide `.env.example`. Never commit `.env`.

## 31. UI design

Style:

- modern
- minimal
- dark-first but accessible
- music-production inspired
- data visualization focused

Avoid excessive gradients, a generic AI-chatbot appearance, huge decorative animations, and unnecessary complexity. The product should feel like an actual music-analysis tool.

## 32. Development phases

Build exactly in this order.

### Phase 0 — Project foundation

Create the repository structure, frontend, backend, environment configuration, README, Docker setup if useful, and a health check. Do **not** implement audio analysis yet.

### Phase 1 — Audio upload

Implement the upload UI, API endpoint, file validation, storage, and audio metadata extraction.

*Definition of done:* a user can upload a valid audio file and see filename, duration, file size, and an audio player.

### Phase 2 — Pitch engine

Implement audio preprocessing, pitch detection, confidence filtering, Hz → MIDI, MIDI → note, and cents. Create automated tests.

*Definition of done:* given a controlled audio sample, the system detects expected pitches within reasonable tolerance.

### Phase 3 — Analysis dashboard

Summary cards, vocal range, pitch accuracy, pitch timeline, note distribution.

### Phase 4 — Microphone recording

Browser recording, preview, upload of recorded audio, analysis.

### Phase 5 — Advanced audio metrics

RMS, peak, spectral centroid, bandwidth, rolloff, zero crossing, spectral flatness.

### Phase 6 — Claude integration

Structured analysis payload, Claude API service, structured output, AI feedback UI, error handling. Claude must **not** be involved in numerical audio calculations.

### Phase 7 — User history

Users, recordings, analyses, comparison, progress chart.

### Phase 8 — Song analyzer

Key estimation, BPM, melody/range estimation where technically reliable, limitations messaging.

### Phase 9 — Song compatibility

User's detected range, song estimated range, overlap calculation, difficulty indicators, transpose suggestions.

### Phase 10 — Production polish

Authentication, security hardening, error pages, loading states, responsive UI, performance optimization, deployment documentation.

## 33. Claude Code workflow

```text
UNDERSTAND
↓
PLAN
↓
IMPLEMENT
↓
TEST
↓
VERIFY
↓
DOCUMENT
```

Before changing code:

1. inspect relevant files
2. identify dependencies
3. identify existing abstractions
4. avoid unnecessary rewrites

After changing code:

1. run tests
2. run lint
3. run type checking
4. manually verify important behavior
5. inspect the git diff

## 34. Git workflow

Use small commits. Examples:

```text
feat: add audio upload API
feat: implement pitch detection
feat: add pitch visualization
feat: add microphone recording
feat: integrate Claude feedback
fix: handle silent audio
test: add pitch conversion tests
refactor: separate audio analysis services
```

Do not make giant commits containing unrelated features.

## 35. AI coding rules

**Do:**

- inspect first
- explain the plan briefly
- implement incrementally
- reuse existing code
- write tests
- verify results

**Do not:**

- rewrite the entire application
- introduce unnecessary dependencies
- create duplicate utilities
- ignore existing architecture
- remove tests just to make them pass
- fake functionality
- create mock data as if it were real analysis
- claim an algorithm is accurate without validation

## 36. When something doesn't work

Do not immediately rewrite everything. First:

1. reproduce the issue
2. inspect logs
3. identify the root cause
4. create the smallest fix
5. run regression tests

If an audio algorithm performs poorly:

1. identify the failure case
2. inspect signal preprocessing
3. inspect confidence filtering
4. compare another algorithm
5. document limitations

## 37. Documentation

Maintain:

```text
README.md
docs/architecture.md
docs/audio-analysis.md
docs/api.md
docs/ai.md
docs/limitations.md
```

Document important algorithmic decisions, especially: pitch detection method, confidence threshold, sampling rate, frame size, hop length, filtering, and accuracy limitations.

## 38. Definition of done

A feature is not complete until:

- implementation exists
- UI works
- API works
- tests exist where appropriate
- error states exist
- lint passes
- type checks pass
- documentation is updated
- no obvious console errors remain

## 39. First task

Start with **Phase 0 only**. Do not build the entire application. First inspect the repository. Then:

1. Determine whether a project already exists.
2. If empty, initialize the project.
3. Create the frontend and backend structure.
4. Set up TypeScript.
5. Set up Python/FastAPI.
6. Add environment configuration.
7. Add `.gitignore`.
8. Add `.env.example`.
9. Add a basic health endpoint.
10. Add a basic frontend homepage.
11. Add a README.
12. Run all available checks.

At the end, report:

```text
## Completed

- ...

## Files created

- ...

## Tests

- ...

## Issues

- ...

## Next phase

PHASE 1 — Audio Upload
```

Do not proceed to Phase 1 until explicitly asked to continue.

## 40. Engineering philosophy

Build the smallest working version first. Prefer:

- simple > clever
- measurable > subjective
- deterministic analysis > LLM guessing
- tested > assumed
- incremental > massive rewrite
- working MVP > premature architecture

The ultimate goal is not merely to build an AI demo. The goal is to build a credible software product while using Claude Code as an engineering partner.
