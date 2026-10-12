-- 0.0.4: terminals.

-- Which shell the terminal ran (bash, zsh, or the login shell when it is
-- neither and runs without markers), and how it ended, in a sentence.
ALTER TABLE terminals
    ADD COLUMN shell text,
    ADD COLUMN end_reason text;

-- The page lists the person's open terminals.
CREATE INDEX terminals_owner ON terminals (owner_id) WHERE closed_at IS NULL;
