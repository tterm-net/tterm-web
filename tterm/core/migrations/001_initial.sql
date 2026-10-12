-- The data model: people, projects, machines, terminals, blocks, the journal.
--
-- Laid out for the whole first release at once, so that sign-in, teams and,
-- later, the Telegram bot move in without rebuilding it. Stage 1 uses only
-- part of it: one local owner and their Personal project.

-- Plans live in data, not in code. Limits are checked by the core at the
-- moment of an action; everything is free for now.
CREATE TABLE plans (
    id      text PRIMARY KEY,
    name    text NOT NULL,
    limits  jsonb NOT NULL DEFAULT '{}'
);
INSERT INTO plans (id, name) VALUES ('free', 'Free');

CREATE TABLE users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    -- Unique, compared in lower case. Empty until sign-in arrives in stage 2.
    email           text,
    plan_id         text NOT NULL DEFAULT 'free' REFERENCES plans (id),
    theme           text NOT NULL DEFAULT 'system'
                    CHECK (theme IN ('system', 'light', 'dark')),
    -- Stage 1 has no sign-in: the page is open only on the owner's laptop
    -- and everything it does is done as this one person.
    is_local_owner  boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email ON users (lower(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX users_one_local_owner ON users (is_local_owner)
    WHERE is_local_owner;

-- Ways to sign in. Telegram is here from the start, without the sign-in
-- itself, so that the bot's people move in at docking as ordinary accounts.
CREATE TABLE sign_in_methods (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    kind        text NOT NULL
                CHECK (kind IN ('email', 'passkey', 'github', 'telegram')),
    -- The address, the credential id, the GitHub or Telegram user id.
    subject     text NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, subject)
);

CREATE TABLE projects (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        uuid NOT NULL REFERENCES users (id),
    name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    -- Marks the project in tabs: CX/web-01. One to four letters or digits,
    -- upper case, unique among one owner's projects.
    code            text NOT NULL
                    CHECK (length(code) BETWEEN 1 AND 4 AND code = upper(code)),
    -- Who may watch a terminal: its owner allows it, the team always may,
    -- or nobody. Whoever is watched always sees who watches.
    watch_rule      text NOT NULL DEFAULT 'ask'
                    CHECK (watch_rule IN ('ask', 'always', 'off')),
    output_days     integer NOT NULL DEFAULT 30
                    CHECK (output_days BETWEEN 1 AND 30),
    journal_days    integer NOT NULL DEFAULT 365
                    CHECK (journal_days BETWEEN 1 AND 365),
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, code)
);

CREATE TABLE project_members (
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role        text NOT NULL
                CHECK (role IN ('owner', 'admin', 'member', 'observer')),
    -- A contractor for two weeks: membership ends by itself.
    until       timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX project_members_user ON project_members (user_id);

-- Certificate authorities. The private keys never come here: they stay with
-- the signer. Several at once, because every machine records which one it
-- trusts: needed to rotate a key, and at docking for the bot's CA.
CREATE TABLE cas (
    id          text PRIMARY KEY,
    public_key  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    retired_at  timestamptz
);

-- A server or a computer with the agent: for the model they are the same.
CREATE TABLE machines (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          uuid NOT NULL REFERENCES projects (id),
    kind                text NOT NULL CHECK (kind IN ('server', 'computer')),
    name                text NOT NULL CHECK (length(name) BETWEEN 1 AND 64),
    -- Servers: the address we dial, the port, and the Unix user the
    -- installer created.
    host                text,
    port                integer CHECK (port BETWEEN 1 AND 65535),
    login               text,
    -- The server's host key as remembered at registration and checked on
    -- every connection, in known_hosts form: for port 22 without brackets.
    host_key            text,
    ca_id               text REFERENCES cas (id),
    -- Computers: the agent's token, only its hash.
    agent_token_hash    bytea,
    -- What the machine is: system, version, package manager, shell, commands.
    passport            jsonb NOT NULL DEFAULT '{}',
    created_by          uuid REFERENCES users (id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    last_seen_at        timestamptz,
    removed_at          timestamptz
);
CREATE UNIQUE INDEX machines_name ON machines (project_id, lower(name))
    WHERE removed_at IS NULL;
CREATE UNIQUE INDEX machines_agent_token ON machines (agent_token_hash)
    WHERE agent_token_hash IS NOT NULL;

-- The one-command install: works once, for ten minutes. Only the hash of the
-- token is kept.
CREATE TABLE install_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('server', 'computer')),
    token_hash  bytea NOT NULL UNIQUE,
    created_by  uuid NOT NULL REFERENCES users (id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz,
    machine_id  uuid REFERENCES machines (id)
);

-- A shell on a machine. Its owner is whoever opened it.
CREATE TABLE terminals (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    machine_id  uuid NOT NULL REFERENCES machines (id),
    owner_id    uuid NOT NULL REFERENCES users (id),
    -- Shown after the machine when a machine has several: web-01 · deploy.
    name        text,
    -- 'term' is the real terminal of the web; 'blocks' is how the Telegram
    -- bot works, one command per message.
    kind        text NOT NULL DEFAULT 'term' CHECK (kind IN ('term', 'blocks')),
    opened_at   timestamptz NOT NULL DEFAULT now(),
    closed_at   timestamptz,
    -- Every event in a terminal is numbered; after a dropped connection the
    -- page asks for everything after the last number it has.
    last_seq    bigint NOT NULL DEFAULT 0
);
CREATE INDEX terminals_machine ON terminals (machine_id) WHERE closed_at IS NULL;

-- One command and what it printed. The kind is there from the start: later
-- an answer from the AI is a block too.
CREATE TABLE blocks (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    terminal_id uuid NOT NULL REFERENCES terminals (id) ON DELETE CASCADE,
    kind        text NOT NULL DEFAULT 'command' CHECK (kind IN ('command', 'ai')),
    seq_start   bigint NOT NULL,
    seq_end     bigint,
    command     text,
    started_by  uuid REFERENCES users (id),
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    exit_code   integer,
    cwd         text,
    branch      text,
    -- Compressed, at most a megabyte: the beginning and the end are kept,
    -- the middle is dropped and output_cut says so.
    output      bytea,
    output_cut  boolean NOT NULL DEFAULT false,
    -- From the project's output_days; a daily sweep removes what expired.
    expires_at  timestamptz NOT NULL
);
CREATE INDEX blocks_terminal ON blocks (terminal_id, id);
CREATE INDEX blocks_expiry ON blocks (expires_at);

-- Who did what, where and when. Only ever appended to; rows leave only when
-- they expire.
CREATE TABLE journal (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    project_id  uuid REFERENCES projects (id),
    user_id     uuid REFERENCES users (id),
    kind        text NOT NULL,
    machine_id  uuid REFERENCES machines (id),
    terminal_id uuid REFERENCES terminals (id),
    details     jsonb NOT NULL DEFAULT '{}',
    expires_at  timestamptz NOT NULL
);
CREATE INDEX journal_project ON journal (project_id, at DESC);
CREATE INDEX journal_expiry ON journal (expires_at);

CREATE FUNCTION journal_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'the journal is append-only';
END
$$;
CREATE TRIGGER journal_append_only BEFORE UPDATE ON journal
    FOR EACH ROW EXECUTE FUNCTION journal_append_only();
