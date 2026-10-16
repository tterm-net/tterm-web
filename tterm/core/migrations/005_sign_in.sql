-- 0.0.9: signing in, for everyone.
--
-- A person signs in with a one-time code sent to their email. On a device
-- they trust, they may then set a four-digit code for quick sign-in: it
-- works only together with that device's own key, which the browser keeps.
-- Only hashes are stored: of the codes, the device keys and the sessions.

-- A code sent by email, for ten minutes, to the browser that asked for it.
CREATE TABLE login_codes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- In lower case.
    email       text NOT NULL,
    code_hash   bytea NOT NULL,
    -- The browser that asked: the code works only there.
    browser     bytea NOT NULL,
    tries       integer NOT NULL DEFAULT 0,
    ip          text NOT NULL DEFAULT '',
    -- Where the limits per network count: an IPv4 address on its own, an
    -- IPv6 one by its /64, which a home or a server gets whole.
    net         text NOT NULL DEFAULT '',
    -- The address had no account when the code was asked for: a sign-up.
    -- Sign-ups have a budget of their own, so that codes to made-up
    -- addresses cannot use up the day's email for those who have accounts.
    new_account boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz
);
CREATE INDEX login_codes_email ON login_codes (email, created_at DESC);
CREATE INDEX login_codes_net ON login_codes (net, created_at DESC);
CREATE INDEX login_codes_created ON login_codes (created_at);

-- A browser a person trusts: its key, and their code for quick sign-in.
CREATE TABLE devices (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    key_hash     bytea NOT NULL UNIQUE,
    pin_hash     bytea NOT NULL,
    pin_tries    integer NOT NULL DEFAULT 0,
    -- "Chrome on macOS", from the browser's own description.
    label        text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz NOT NULL DEFAULT now(),
    forgotten_at timestamptz
);
CREATE INDEX devices_user ON devices (user_id) WHERE forgotten_at IS NULL;

-- A signed-in browser. It ends after hours without activity, on Sign out,
-- or when its device is forgotten.
CREATE TABLE sessions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash   bytea NOT NULL UNIQUE,
    device_id    uuid REFERENCES devices (id) ON DELETE CASCADE,
    -- How it began: 'code' (from email) or 'pin'.
    method       text NOT NULL CHECK (method IN ('code', 'pin')),
    label        text NOT NULL DEFAULT '',
    ip           text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    ended_at     timestamptz
);
CREATE INDEX sessions_user ON sessions (user_id) WHERE ended_at IS NULL;
