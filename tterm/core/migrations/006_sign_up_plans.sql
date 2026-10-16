-- 0.0.9, after the owner's look at the sign-in on 15.10: signing up with a
-- name and a yes to the terms, the free plan's limits, usage counts.
--
-- A file of its own rather than more in 005: a laptop may have run 005 as
-- it was first sent.

-- The code was right for an address without an account: until then the
-- browser that asked may make the account, with the person's name and
-- their yes to the Terms of Use.
ALTER TABLE login_codes ADD COLUMN signup_by timestamptz;

-- When the person agreed to the Terms of Use and the Privacy Policy: at
-- sign-up, with one tick for both. The texts of each date are in the
-- site's history. Empty for the laptop's owner, who runs it.
ALTER TABLE users ADD COLUMN terms_accepted_at timestamptz;

-- The free plan's limits: everyone shares one server for now. Changed on a
-- running server with one UPDATE, no release needed; usage_stats shows
-- when they need another look.
UPDATE plans SET limits = '{"machines": 50, "terminals": 24}' WHERE id = 'free';

-- A limit is a whole number, zero or more, under a name the core knows: a
-- typo in the UPDATE that changes one is refused at once, instead of quietly
-- meaning "no limit". A new kind of limit comes with its own migration.
ALTER TABLE plans ADD CONSTRAINT plans_limits_known CHECK (
    jsonb_typeof(limits) = 'object'
    AND NOT jsonb_path_exists(limits, 'strict $.* ? (@.type() != "number")',
                              '{}', true)
    AND NOT jsonb_path_exists(limits, 'lax $.* ? (@ < 0 || @.floor() != @)')
    AND NOT jsonb_path_exists(
        limits, 'lax $.keyvalue() ? (!(@.key == "machines" || @.key == "terminals"))'));

-- Once an hour, how much tTerm Web carries: counts only, no one's data,
-- kept a year. deploy/stats.sh shows them.
CREATE TABLE usage_stats (
    taken_at          timestamptz PRIMARY KEY DEFAULT now(),
    users             integer NOT NULL,
    -- People who did something in the last 24 hours.
    active_day        integer NOT NULL,
    -- Accounts made in the last 24 hours, and codes sent.
    signups_day       integer NOT NULL,
    codes_day         integer NOT NULL,
    machines          integer NOT NULL,
    -- The most machines one person has.
    machines_top      integer NOT NULL,
    computers_online  integer NOT NULL,
    -- The most terminals open at once since the last snapshot, in all and
    -- for one person.
    terminals_peak    integer NOT NULL,
    terminals_top     integer NOT NULL,
    commands_day      integer NOT NULL,
    database_bytes    bigint NOT NULL,
    -- The memory the core takes, resident.
    core_bytes        bigint NOT NULL
);

-- The commands of the last day, counted hourly without reading them all.
CREATE INDEX blocks_started ON blocks (started_at);
