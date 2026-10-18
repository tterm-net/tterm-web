-- 0.0.12, after the security audit of 18.10: a certificate made for one
-- server is of no use on any other.
--
-- Each server in tTerm Web has a principal of its own, m-<uuid>, and its
-- line in authorized_keys trusts tTerm Web's CA only for certificates made
-- out to it:
--     cert-authority,principals="m-…" ssh-ed25519 AAAA… tterm-web ca-…
-- The line of a server connected before took any certificate of the CA for
-- the user tterm. The core signs in there once more that way, puts in a
-- line of its own for each machine that is this server, and takes the old
-- line out (trust.py). Until then own_line is false.
ALTER TABLE machines ADD COLUMN principal text;
ALTER TABLE machines ADD COLUMN own_line boolean NOT NULL DEFAULT false;
UPDATE machines SET principal = 'm-' || gen_random_uuid() WHERE kind = 'server';
CREATE UNIQUE INDEX machines_principal ON machines (principal)
    WHERE principal IS NOT NULL;

-- The principal a server's command puts in its line, and the machine takes
-- when the command connects it.
ALTER TABLE install_tokens ADD COLUMN principal text;
UPDATE install_tokens SET principal = 'm-' || gen_random_uuid()
    WHERE kind = 'server';
