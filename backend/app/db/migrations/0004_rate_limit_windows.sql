-- A rate-limit counter every API worker can see.
--
-- Step 10.3's limiter is one process's memory, and that was measured rather
-- than assumed before this table existed: four uvicorn workers sharing a limit
-- of five new identities per hour minted twenty, exactly 4.0x, where one worker
-- minted five. The limit was not approximately wrong by the worker count; it
-- was wrong by exactly the worker count.
--
-- 10.3 rejected a table like this in one sentence — it "would add a write to
-- every request in order to bound the cost of writes". That objection does not
-- survive contact with the two call sites. The identity guard runs only from
-- `before_mint`, immediately before an owner row and a credential row are
-- inserted; the costly guard runs only on uploading, starting either analysis,
-- asking for feedback and adding a key. Reading is never limited. Every request
-- that reaches this table was already about to write to another one.
--
-- One row per (bucket, key), not one row per attempt. An attempt is an UPDATE
-- of an existing row, so the table grows with distinct callers and not with
-- traffic — the same shape as the in-process dictionary it replaces.

CREATE TABLE rate_limit_windows (
    -- Which limit this row counts. The two limits are keyed by different kinds
    -- of thing — a client address and an owner id — and separating them here
    -- means one can never be spent by the other, even if a client address were
    -- ever to read like a UUID.
    bucket            TEXT        NOT NULL,
    limit_key         TEXT        NOT NULL,
    -- When the current window opened. Rolled forward by the application's
    -- upsert when it has expired, in the same statement that increments the
    -- count, so a window can never be rolled without the count being reset.
    window_started_at TIMESTAMPTZ NOT NULL,
    count             INTEGER     NOT NULL,

    -- The primary key is what makes the counter correct across workers: two
    -- workers counting the same caller conflict on this index, and the second
    -- waits for the first to commit before applying its own increment to the
    -- row the first one wrote. Without it they would insert two rows and count
    -- separately, which is precisely the bug being fixed.
    PRIMARY KEY (bucket, limit_key)
);

-- Expired rows are deleted by the limiter itself, at most once per window per
-- process. That sweep asks for rows older than a cut-off, which without this
-- index is a sequential scan of every caller ever counted. Added now rather
-- than "when it hurts", for the same reason `owners_last_seen_idx` was: the
-- whole point of the table is that it grows unattended.
CREATE INDEX rate_limit_windows_started_idx ON rate_limit_windows (window_started_at);
