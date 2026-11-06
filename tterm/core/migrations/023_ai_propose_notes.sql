-- 0.1.30: the AI a person connected (022) proposes commands and writes
-- notes in the logs, as the person allowed it (can_propose, can_note).
--
-- A command it proposes waits in the person's terminal, in a block of its
-- own, until the person runs it, puts it into the input line or removes
-- it: the AI runs nothing. One waits at a time in a terminal; the next one
-- the AI proposes there takes its place. A command the person runs from it
-- says so in its block and its line of the log (proposed_by).
CREATE TABLE ai_proposals (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id uuid NOT NULL REFERENCES ai_connections (id) ON DELETE CASCADE,
    terminal_id   uuid NOT NULL REFERENCES terminals (id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- The AI's name when it proposed it, as the block says it.
    ai            text NOT NULL CHECK (length(ai) BETWEEN 1 AND 100),
    command       text NOT NULL CHECK (length(command) BETWEEN 1 AND 2000),
    -- Why, cut of secrets (core/ai.py clean_why: what the AI said is at
    -- most 300 characters, the cut can make it longer).
    why           text NOT NULL DEFAULT '' CHECK (length(why) <= 1000),
    created_at    timestamptz NOT NULL DEFAULT now(),
    -- waiting; then what the person did (run, inserted, removed), or what
    -- happened to it (replaced by the next one; over: the terminal ended,
    -- the AI was disconnected or lost the right).
    state         text NOT NULL DEFAULT 'waiting'
        CHECK (state IN ('waiting', 'run', 'inserted', 'removed', 'replaced',
                         'over')),
    decided_at    timestamptz,
    -- The block it became, once the person ran it as proposed.
    seq           bigint
);
CREATE UNIQUE INDEX ai_proposals_waiting ON ai_proposals (terminal_id)
    WHERE state = 'waiting';
CREATE INDEX ai_proposals_terminal ON ai_proposals (terminal_id, created_at);
CREATE INDEX ai_proposals_connection ON ai_proposals (connection_id, created_at);

-- The language of the page the person allowed or changed the AI from: its
-- notes are in the person's language, this one while they chose none.
ALTER TABLE ai_connections
    ADD COLUMN language text CHECK (length(language) BETWEEN 2 AND 16);

-- The AI whose proposal a block's command was (the person ran it).
ALTER TABLE blocks
    ADD COLUMN proposed_by text CHECK (length(proposed_by) BETWEEN 1 AND 100);

-- The notes an AI wrote through a connection (details.via) in the last
-- hour: at most 500 an hour, counted again after a restart (api/mcp.py).
CREATE INDEX journal_ai_notes ON journal ((details->>'via'), at)
    WHERE kind = 'log.note' AND author = 'ai';
