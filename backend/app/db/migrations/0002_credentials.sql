-- Credentials belong to an owner; they are no longer the owner.
--
-- Until now an owner *was* a key: `owners.token_hash` held exactly one value,
-- so an identity could not have a second way in, could not label the ways it
-- had, and could not revoke one without losing everything. This migration moves
-- the key into a table of its own so an owner can hold several.
--
-- Nothing changes hands. Every existing `token_hash` is copied verbatim into
-- `credentials`, so every key that worked before this migration works after it
-- and resolves to the same `owners.id` — which is the id already recorded
-- against that person's recordings, analyses, comparisons and progress.
--
-- The column is then dropped. It is dead once the copy exists, and a dead column
-- that used to be the identity — still carrying a UNIQUE index — is exactly the
-- kind of trap that gets read as authoritative later. The copy and the drop are
-- in one transaction (every migration is), so either both happen or neither.
--
-- Reversing this, if it were ever needed:
--     ALTER TABLE owners ADD COLUMN token_hash TEXT;
--     UPDATE owners o SET token_hash =
--         (SELECT c.credential_hash FROM credentials c
--           WHERE c.owner_id = o.id ORDER BY c.created_at LIMIT 1);
--     ALTER TABLE owners ALTER COLUMN token_hash SET NOT NULL;
-- An owner with several credentials would keep only its oldest, which is why
-- this is written down rather than automated.

CREATE TABLE credentials (
    id              UUID PRIMARY KEY,
    owner_id        UUID NOT NULL REFERENCES owners(id) ON DELETE CASCADE,

    -- SHA-256 of a 128-bit server-generated key, exactly as `owners.token_hash`
    -- held it. Globally unique: a key names one credential, and therefore one
    -- owner. A leaked database yields no usable keys.
    credential_hash TEXT NOT NULL UNIQUE,

    -- What the holder calls it — "Laptop", "Phone". Display only; it is never
    -- part of resolving anything, so it carries no security weight and needs no
    -- uniqueness.
    label           TEXT NOT NULL,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "This owner's credentials, oldest first" is the only query that is not a
-- lookup by hash, and it runs on the identity screen and on every revocation.
CREATE INDEX credentials_owner_idx ON credentials (owner_id, created_at);

-- Every existing owner keeps its key, and therefore its identity. The generated
-- id is deterministic in shape only; what matters is that the hash carries over
-- byte for byte.
INSERT INTO credentials (id, owner_id, credential_hash, label, created_at)
SELECT gen_random_uuid(), id, token_hash, 'Original key', created_at
  FROM owners;

ALTER TABLE owners DROP COLUMN token_hash;
