-- How many frames a stored pitch timeline holds, beside the document instead of
-- inside it.
--
-- Step 10.11 split the audio-analysis read in two: a summary that selects
-- `document - 'pitch_points'` so the timeline never crosses the socket, and a
-- full read for the three routes that genuinely want it. The summary still has
-- to answer "how many frames were measured?", so it selected
-- `jsonb_array_length(document->'pitch_points')` beside the stripped document,
-- with a comment claiming the second expression "reads the same document the
-- server has already decompressed to build the first".
--
-- Step 10.12 measured that claim and it is false. A document this size lives in
-- the TOAST table, and PostgreSQL detoasts it once per expression that
-- references it. On a completed analysis of a five-minute recording — 11 083
-- points, 1 446 kB raw and 253 kB stored:
--
--     document - 'pitch_points' alone                1.80 ms,  37 buffers
--     jsonb_array_length(document->'pitch_points')   2.31 ms,  37 buffers
--     both together (what 10.11 shipped)             5.70 ms, 109 buffers
--
-- Two expressions, two decompressions. The count cost more than the read it was
-- riding on, on the one endpoint the browser polls while a measurement runs.
--
-- The count could not simply be read out of the stripped document, which is why
-- 10.11 selected it separately: a document written before 10.11 has no
-- `pitch_point_count` key at all, and defaulting it to zero would report
-- "measured nothing" for every analysis completed before that step. A column
-- with a backfill answers for those rows too, permanently, instead of paying a
-- full decompression on every read to work around them.
--
-- This is the same arrangement `status`, `feedback_status` and `error_code`
-- already have: the document stays authoritative, and the column beside it is
-- written from the same object in the same statement so it cannot drift.

-- Added with a default so existing rows need no rewrite to acquire the column;
-- the backfill below then gives them their real value.
ALTER TABLE audio_analyses ADD COLUMN pitch_point_count INTEGER NOT NULL DEFAULT 0;

-- `coalesce` covers a pending or failed record, whose document has no
-- `pitch_points` key. Those rows keep the zero the default gave them, which is
-- the truth: nothing was measured.
UPDATE audio_analyses
   SET pitch_point_count = coalesce(jsonb_array_length(document->'pitch_points'), 0);

-- No index. Nothing filters, joins or orders by this column — it is selected
-- alongside a row already located by primary key or by
-- `audio_analyses_recording_idx`, and an index on it would be write cost with
-- no reader.
