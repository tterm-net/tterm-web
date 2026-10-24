-- 0.1.6: tTerm for the computer signs in with the browser (stage 3 of the
-- app). The page in a browser, signed in, allows the app that opened it; a
-- one-time code then goes to that app on that very computer (by 127.0.0.1),
-- and the app trades it, with a secret only it knows (PKCE), for a device
-- key of its own. With the key it asks for sessions. See core/auth.py.

-- A device is a browser with a quick code, or from now an app with its key
-- and no quick code: the app keeps its key in the system's keychain.
ALTER TABLE devices ADD COLUMN kind text NOT NULL DEFAULT 'browser'
    CHECK (kind IN ('browser', 'app'));
ALTER TABLE devices ALTER COLUMN pin_hash DROP NOT NULL;
ALTER TABLE devices ADD CONSTRAINT devices_pin_of_browsers
    CHECK ((kind = 'browser') = (pin_hash IS NOT NULL));

-- The app's sessions.
ALTER TABLE sessions DROP CONSTRAINT sessions_method_check;
ALTER TABLE sessions ADD CONSTRAINT sessions_method_check
    CHECK (method IN ('code', 'pin', 'app'));

-- What a browser allowed: for two minutes, once, and only for the app whose
-- secret hashes to the challenge, while the session that allowed it is on
-- (Sign out and Forget all devices take back what is not used yet). The
-- code itself is kept as its hash.
CREATE TABLE app_grants (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    session_id  uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    code_hash   bytea NOT NULL UNIQUE,
    -- base64url of the SHA-256 of the app's secret, as RFC 7636 has it.
    challenge   text NOT NULL,
    -- Which app (mac, windows, linux) on which computer: the device's label.
    app         text NOT NULL,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz
);
CREATE INDEX app_grants_user ON app_grants (user_id, created_at DESC);
CREATE INDEX app_grants_created ON app_grants (created_at);
