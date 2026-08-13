-- Step 7M: the initial schema.
--
-- Two decisions run through every table here.
--
-- **The domain object is stored as JSONB; the columns beside it are indexed
-- projections.** The pydantic models in ``services/`` already serialise
-- themselves exactly, and re-expressing thirty measurements as columns would
-- add a hand-maintained mapping that drifts from the models the first time one
-- changes. The JSONB document is authoritative for the domain object. The
-- columns exist because queries need them — status, timestamps, ownership —
-- and they are written from the same object in the same statement, so they
-- cannot disagree with it.
--
-- **Correctness lives in constraints, not in application locks.** Step 7A-7L
-- relied on a process-local asyncio.Lock, which a second worker process
-- silently defeats. The partial unique indexes below are what actually make
-- "one analysis in flight per recording" true.

CREATE TABLE owners (
    id          UUID PRIMARY KEY,
    -- Only a hash is stored. A leaked database therefore yields no usable
    -- tokens, exactly as it would not yield usable passwords.
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE recordings (
    id                CHAR(32) PRIMARY KEY,
    owner_id          UUID NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    audio_format      TEXT NOT NULL,
    duration_seconds  DOUBLE PRECISION NOT NULL,
    sample_rate       INTEGER NOT NULL,
    channels          INTEGER NOT NULL,
    size_bytes        BIGINT NOT NULL,
    bits_per_sample   INTEGER,
    created_at        TIMESTAMPTZ NOT NULL,
    document          JSONB NOT NULL
);

-- The history query, and the only access pattern recordings have: one owner's
-- recordings, newest first.
CREATE INDEX recordings_owner_created_idx ON recordings (owner_id, created_at DESC);

CREATE TABLE speech_analyses (
    id           CHAR(32) PRIMARY KEY,
    recording_id CHAR(32) NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    status       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    error_code   TEXT,
    document     JSONB NOT NULL
);

-- Replaces the directory scan in list_for_recording.
CREATE INDEX speech_analyses_recording_idx
    ON speech_analyses (recording_id, created_at DESC);

-- The constraint that replaces the asyncio.Lock: at most one speech analysis
-- per recording may be un-finished at a time. Two concurrent starts become one
-- insert and one unique violation, whichever process they run in.
CREATE UNIQUE INDEX speech_analyses_one_active_idx
    ON speech_analyses (recording_id)
    WHERE status IN ('pending', 'transcribing', 'analyzing');

CREATE TABLE audio_analyses (
    id              CHAR(32) PRIMARY KEY,
    recording_id    CHAR(32) NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    status          TEXT NOT NULL,
    feedback_status TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    error_code      TEXT,
    document        JSONB NOT NULL
);

CREATE INDEX audio_analyses_recording_idx
    ON audio_analyses (recording_id, created_at DESC);

CREATE UNIQUE INDEX audio_analyses_one_active_idx
    ON audio_analyses (recording_id)
    WHERE status IN ('pending', 'analyzing');

-- The pitch timeline lives inside the audio_analyses document rather than in a
-- table of its own. It is written once, read whole, and never queried by
-- predicate: the note breakdown is derived from it in Python by code that is
-- already written and tested, and one row per frame would be ~13 000 rows per
-- recording to support a GROUP BY nobody issues. If a future feature needs to
-- query across frames, that is the moment to normalise it — not before.
