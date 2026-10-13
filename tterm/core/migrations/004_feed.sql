-- 0.0.5: the block feed and tTerm's own input line.

-- The width a command ran at: the page draws its output again at that width.
ALTER TABLE blocks ADD COLUMN cols integer;

-- The home directory the shell reported, so that the feed of a terminal
-- that ended in an earlier run still shows ~ for it.
ALTER TABLE terminals ADD COLUMN home text;

-- The feed pages through a terminal's blocks by their numbers.
CREATE INDEX blocks_terminal_seq ON blocks (terminal_id, seq_start);

-- The input line's history: commands on a machine, ended terminals included.
CREATE INDEX terminals_machine_all ON terminals (machine_id, owner_id);
