-- 0.1.7: search over everything, and one's own photo.

-- What a command printed, as plain text to search: the escape sequences
-- out, the start and the end of a long output (core/search.py). Filled when
-- a block is saved; for blocks saved before, by the core in the background.
-- It goes with its block, when its output expires.
ALTER TABLE blocks ADD COLUMN output_text text;

-- Trigram indexes make a word anywhere in a command or its output quick to
-- find. pg_trgm comes with PostgreSQL; the database's owner may add it
-- (PostgreSQL 13 and later). Without it search still works, only slower
-- on a long history.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_trgm is not available: search goes without its indexes';
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        CREATE INDEX blocks_command_trgm ON blocks
            USING gin (command gin_trgm_ops);
        CREATE INDEX blocks_output_trgm ON blocks
            USING gin (output_text gin_trgm_ops);
    END IF;
END
$$;

-- The blocks the core has yet to make text of.
CREATE INDEX blocks_no_text ON blocks (id)
    WHERE output_text IS NULL AND output IS NOT NULL;

-- One's own photo, in place of the initials where the name is next to it:
-- 256 pixels, a JPEG with nothing in it but the picture (core/photos.py).
-- When it was put in names the address the page loads it from, so that a
-- new photo is never taken for the one the browser kept.
CREATE TABLE photos (
    user_id     uuid PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    image       bytea NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
