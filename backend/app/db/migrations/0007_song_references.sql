-- Step 11.3: song references.
--
-- Phase 9 under the input model recorded in docs/phase-9-specification.md §3A:
-- **a reference is metadata somebody typed**, never audio this system measured.
-- So this is one table, no audio, no analysis lifecycle, no storage directory
-- and no file to delete — the whole of what Option D adds to the schema.
--
-- The two decisions from 0001 hold. The domain object is the JSONB document;
-- the columns beside it are indexed projections, written from the same object
-- in the same statement so they cannot disagree with it.
--
-- **There are deliberately no columns for the two notes.** Nothing queries by
-- range: a compatibility result is derived in Python from a reference the
-- caller named by id, exactly as the note breakdown is derived from a stored
-- timeline. A column exists here when a statement needs it, and the day a
-- feature asks "which of my references fit my range" is the moment to add two —
-- not before. The document is authoritative, and its own validator already
-- refuses a range that is upside down or outside MIDI.
--
-- **A title is not unique, per owner or otherwise.** Two takes on the same song
-- are two references, the same way re-uploading the same audio is two
-- recordings; a uniqueness rule here would be a product decision nobody has
-- taken, and the inconsistency with recordings would be the more surprising of
-- the two.

CREATE TABLE song_references (
    id         CHAR(32) PRIMARY KEY,
    owner_id   UUID NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL,
    document   JSONB NOT NULL
);

-- The only access pattern a reference has: one owner's references, newest
-- first. The same shape as recordings_owner_created_idx, and it serves the
-- retention predicate's NOT EXISTS as well.
CREATE INDEX song_references_owner_created_idx ON song_references (owner_id, created_at DESC);
