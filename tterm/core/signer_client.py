"""How the core asks the signer for certificates.

The core makes a throwaway key pair for each connection and sends only the
public half. The private half lives in this process's memory for the length
of one connection; the CA key never leaves the signer.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import asyncssh

#: The certificate lives this long: it is needed only while connecting.
CERT_TTL = 120


class SignerUnavailable(Exception):
    """The signer does not answer; the message says what to check."""


class SignerRefused(Exception):
    """The signer answered, but would not sign; the message is its reason."""


class SignerClient:
    def __init__(self, socket_path: Path, timeout: float = 5.0) -> None:
        self.path = socket_path
        self.timeout = timeout

    async def call(self, request: dict) -> dict:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.path)), self.timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            raise SignerUnavailable(
                f"The signer is not answering on {self.path} ({exc}). "
                "Is tTerm Web started with python -m tterm.dev?") from None
        try:
            writer.write(json.dumps(request).encode() + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), self.timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            raise SignerUnavailable(f"The signer stopped answering ({exc}).") \
                from None
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        try:
            reply = json.loads(line)
        except ValueError:
            raise SignerUnavailable("The signer answered with nonsense.") from None
        if not reply.get("ok"):
            raise SignerRefused(reply.get("error") or "refused")
        return reply

    async def keys(self) -> list[dict]:
        """The CA public keys; the active one signs for new machines."""
        return (await self.call({"op": "keys"}))["cas"]

    async def active_key(self) -> dict:
        for ca in await self.keys():
            if ca["active"]:
                return ca
        raise SignerUnavailable("The signer has no active CA.")

    async def credentials(self, principal: str, key_id: str,
                          ca: str | None = None, ttl: int = CERT_TTL
                          ) -> tuple[asyncssh.SSHKey, asyncssh.SSHCertificate]:
        """A fresh key pair with a certificate for it, made out to one
        server's principal, ready for asyncssh."""
        key = asyncssh.generate_private_key("ssh-ed25519")
        reply = await self.call({
            "op": "sign",
            "public_key": key.export_public_key().decode(),
            "principal": principal,
            "key_id": key_id,
            "ttl": ttl,
            "ca": ca,
        })
        cert = asyncssh.import_certificate(reply["certificate"])
        return key, cert
