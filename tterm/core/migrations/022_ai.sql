-- tTerm Web 0.1.29: AI through MCP (core/ai.py; PROJECT.md, «ИИ через
-- MCP»). An AI app (Claude, ChatGPT, Claude Code, Cursor, any with MCP)
-- registers itself, the person allows it on /connect-ai, and it gets tokens
-- to ask tTerm, over MCP at /mcp, about the machines it was allowed: what
-- ran in the person's terminals there, what the machines' logs say. It
-- types nothing, runs nothing, opens nothing and reaches no machine. Of
-- every code, token and secret only a hash is kept.

-- An app that registered itself (RFC 7591): anyone may, and it may do
-- nothing until a person allows it. Its client_id is its id.
CREATE TABLE ai_clients (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- What it calls itself (client_name), one line: its own words, shown
    -- as such, beside where it takes the person back to.
    name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    -- Where the person's browser may take a code back to, exactly; on this
    -- computer (127.0.0.1, localhost) on any port (RFC 8252).
    redirect_uris text[] NOT NULL
        CHECK (cardinality(redirect_uris) BETWEEN 1 AND 10),
    -- 'none': a public client, with PKCE only. Otherwise the hash of the
    -- secret it got, which it shows at /oauth/token.
    auth_method   text NOT NULL
        CHECK (auth_method IN ('none', 'client_secret_basic',
                               'client_secret_post')),
    secret_hash   bytea,
    application_type text NOT NULL DEFAULT 'web'
        CHECK (application_type IN ('web', 'native')),
    -- Where registrations are counted for their limit (as login_codes.net).
    net           text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK ((auth_method = 'none') = (secret_hash IS NULL))
);
CREATE INDEX ai_clients_created ON ai_clients (created_at);
CREATE INDEX ai_clients_net ON ai_clients (net, created_at);

-- A request to connect (GET /oauth/authorize), for ten minutes, until the
-- person allows or refuses it on /connect-ai.
CREATE TABLE ai_requests (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id    uuid NOT NULL REFERENCES ai_clients (id) ON DELETE CASCADE,
    redirect_uri text NOT NULL,
    -- base64url of the SHA-256 of the app's secret (PKCE, S256).
    challenge    text NOT NULL,
    -- What the app wants back as it gave it (state), and the scope it
    -- asked for, kept only to be shown.
    state        text,
    scope        text,
    net          text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    answered_at  timestamptz
);
CREATE INDEX ai_requests_created ON ai_requests (created_at);
CREATE INDEX ai_requests_net ON ai_requests (net, created_at);

-- What a person allowed an app: what it may do, how long, and from which
-- device. It is connected once the app traded its code (connected_at), and
-- ends when the person forgets it, all AI, all devices, or its time is up.
CREATE TABLE ai_connections (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    client_id       uuid REFERENCES ai_clients (id) ON DELETE SET NULL,
    -- 'oauth' through /connect-ai; 'key' for an access key (0.1.31).
    kind            text NOT NULL DEFAULT 'oauth'
        CHECK (kind IN ('oauth', 'key')),
    -- As the person sees it: by where it takes them back (claude.ai is
    -- Claude), or by what it called itself; and that place.
    name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    said            text NOT NULL DEFAULT '' CHECK (length(said) <= 100),
    returns_to      text NOT NULL DEFAULT '' CHECK (length(returns_to) <= 300),
    -- What it may do: see (the terminals and logs it was allowed), and from
    -- 0.1.30 propose commands and write notes in the logs, which need see.
    can_see         boolean NOT NULL DEFAULT true,
    can_propose     boolean NOT NULL DEFAULT false,
    can_note        boolean NOT NULL DEFAULT false,
    CHECK (can_see OR NOT (can_propose OR can_note)),
    -- Null: until the person takes it back.
    expires_at      timestamptz,
    -- The device it was allowed from: the machines under the person's keys
    -- reach it only while the keys are open there; for a browser that is
    -- not a trusted device, the session.
    keys_device_id  uuid REFERENCES devices (id) ON DELETE SET NULL,
    keys_session_id uuid REFERENCES sessions (id) ON DELETE SET NULL,
    -- The person's time zone on that device, for the times it reads.
    time_zone       text NOT NULL DEFAULT 'UTC'
        CHECK (length(time_zone) BETWEEN 1 AND 64),
    created_at      timestamptz NOT NULL DEFAULT now(),
    connected_at    timestamptz,
    last_call_at    timestamptz,
    ended_at        timestamptz,
    end_reason      text
);
CREATE INDEX ai_connections_user ON ai_connections (user_id, created_at)
    WHERE ended_at IS NULL;

-- The machines a connection may see: their terminals (the person's own
-- there), their log, or both; only while the machine is in the project it
-- was allowed in, and the person is in that project.
CREATE TABLE ai_grants (
    connection_id uuid NOT NULL REFERENCES ai_connections (id)
        ON DELETE CASCADE,
    machine_id    uuid NOT NULL REFERENCES machines (id) ON DELETE CASCADE,
    project_id    uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    terminals     boolean NOT NULL,
    log           boolean NOT NULL,
    CHECK (terminals OR log),
    PRIMARY KEY (connection_id, machine_id)
);
CREATE INDEX ai_grants_machine ON ai_grants (machine_id);

-- What /connect-ai gave the app for its tokens: once, for five minutes,
-- with the secret whose challenge the request had, while the session that
-- allowed it is on.
CREATE TABLE ai_codes (
    code_hash     bytea PRIMARY KEY,
    connection_id uuid NOT NULL REFERENCES ai_connections (id)
        ON DELETE CASCADE,
    client_id     uuid NOT NULL REFERENCES ai_clients (id) ON DELETE CASCADE,
    redirect_uri  text NOT NULL,
    challenge     text NOT NULL,
    session_id    uuid REFERENCES sessions (id) ON DELETE CASCADE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    used_at       timestamptz
);
CREATE INDEX ai_codes_created ON ai_codes (created_at);

-- Its tokens: to ask (for an hour), and to get the next ones (as long as
-- the connection), each of those once: the next is made from it.
CREATE TABLE ai_tokens (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    connection_id uuid NOT NULL REFERENCES ai_connections (id)
        ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN ('access', 'refresh')),
    token_hash    bytea NOT NULL UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz,
    -- A refresh token, once the next was made from it.
    used_at       timestamptz,
    CHECK ((kind = 'access') = (expires_at IS NOT NULL))
);
CREATE INDEX ai_tokens_connection ON ai_tokens (connection_id);

-- What it read of the person's terminals: the page marks those blocks
-- with its eye.
CREATE TABLE ai_reads (
    connection_id uuid NOT NULL REFERENCES ai_connections (id)
        ON DELETE CASCADE,
    terminal_id   uuid NOT NULL REFERENCES terminals (id) ON DELETE CASCADE,
    seq_start     bigint NOT NULL,
    at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (connection_id, terminal_id, seq_start)
);
CREATE INDEX ai_reads_terminal ON ai_reads (terminal_id, seq_start);
CREATE INDEX ai_reads_at ON ai_reads (at);
