-- When an identity was last used, so an unused one can eventually be reclaimed.
--
-- Step 10.3 measured the problem this exists for: an unauthenticated request
-- mints an owner, so a crawler, a probe or a single curious visit leaves two
-- rows behind forever. Rate limiting bounded how fast that happens; nothing
-- bounded the total, and nothing ever reclaimed one.
--
-- The obstacle was that `owners` recorded only when an identity was *created*.
-- Age alone is the wrong signal: somebody who visits every week to look at
-- their progress writes nothing, so an owner created a year ago may be in
-- active daily use. Deleting on `created_at` would delete them.
--
-- `last_seen_at` is that missing signal. It is written when a credential
-- resolves to this owner, and deliberately throttled in the application so a
-- busy identity costs at most one UPDATE per interval rather than one per
-- request — the same objection Step 10.3 raised against a database-backed rate
-- limiter applies here.
--
-- The backfill matters as much as the column. Existing rows have no history, so
-- inventing `now()` would make every identity look active and inventing
-- `created_at` would make long-lived ones look abandoned. The best evidence
-- already in the database is the most recent thing the owner demonstrably did:
-- created the identity, added a credential, or uploaded a recording.

ALTER TABLE owners ADD COLUMN last_seen_at TIMESTAMPTZ;

UPDATE owners o
   SET last_seen_at = GREATEST(
       o.created_at,
       COALESCE((SELECT max(c.created_at) FROM credentials c WHERE c.owner_id = o.id),
                o.created_at),
       COALESCE((SELECT max(r.created_at) FROM recordings r WHERE r.owner_id = o.id),
                o.created_at)
   );

ALTER TABLE owners ALTER COLUMN last_seen_at SET NOT NULL;
ALTER TABLE owners ALTER COLUMN last_seen_at SET DEFAULT now();

-- The cleanup query asks for the oldest unused identities, so it reads this
-- column in order and stops. Without the index it is a sequential scan and a
-- sort of the whole table; with it, the rows come out of the index already
-- ordered. Added now rather than "when it hurts" because the whole point of the
-- feature is that the table grows unattended.
CREATE INDEX owners_last_seen_idx ON owners (last_seen_at);
