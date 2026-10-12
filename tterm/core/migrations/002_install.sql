-- 0.0.3: connecting servers.

-- A server may have several host keys (ed25519, ecdsa, rsa); all of them are
-- remembered at registration and any of them is accepted later. Nothing was
-- stored in the single-key column yet, so it simply goes.
ALTER TABLE machines DROP COLUMN host_key;
ALTER TABLE machines ADD COLUMN host_keys text[] NOT NULL DEFAULT '{}';
-- The same server registered again is found by its keys, not duplicated.
CREATE INDEX machines_host_keys ON machines USING gin (host_keys)
    WHERE removed_at IS NULL;

-- Where an install stands, so the page can show it while it waits: still
-- waiting, connecting right now, connected, or failed with a reason (the same
-- command then still works until it expires).
ALTER TABLE install_tokens
    ADD COLUMN state text NOT NULL DEFAULT 'waiting'
        CHECK (state IN ('waiting', 'connecting', 'connected', 'failed')),
    ADD COLUMN error text,
    ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
