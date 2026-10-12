"""SSH to servers: a fresh certificate every time, and the server's own key
checked against what it told us when it registered.
"""
from __future__ import annotations

import asyncssh

from .signer_client import SignerClient, SignerRefused, SignerUnavailable

#: How long to wait for a server to answer at all.
CONNECT_TIMEOUT = 12


class HostKeysUnusable(Exception):
    """None of the remembered host keys could be read."""


def key_id(name: str, user_id: str, machine: str) -> str:
    """What the server's own log shows for each sign-in.

    sshd writes the certificate's key id into auth.log, so on the server
    itself one can see who came in: "Denis Gerasimenko via tTerm Web
    (2fe3b3c7) to tterm-lab-01".
    """
    clean = "".join(ch for ch in name if ch.isprintable())[:80] or "someone"
    return f"{clean} via tTerm Web ({user_id[:8]}) to {machine[:40]}"


def known_hosts(host: str, port: int, keys: list[str]) -> bytes:
    """known_hosts lines for exactly this address and these keys.

    For port 22 the address stands bare, otherwise as [address]:port: that
    is how OpenSSH writes it, and asyncssh looks the entry up the same way.
    With [address]:22 nothing would match, which reads as a changed key.
    """
    where = host if port == 22 else f"[{host}]:{port}"
    lines = []
    for key in keys:
        try:
            asyncssh.import_public_key(key)
        except Exception:
            continue
        lines.append(f"{where} {key}")
    if not lines:
        raise HostKeysUnusable("none of the server's keys could be read")
    text = "\n".join(lines) + "\n"
    # Parsed once here, as text, so a bad entry fails now rather than in the
    # middle of connecting; asyncssh.connect itself is handed bytes, the form
    # it documents.
    asyncssh.import_known_hosts(text)
    return text.encode()


async def connect(signer: SignerClient, *, host: str, port: int, login: str,
                  host_keys: list[str], who: str, ca: str | None = None,
                  ) -> asyncssh.SSHClientConnection:
    """Signs in with a new certificate; refuses a server whose key changed."""
    pinned = known_hosts(host, port, host_keys)
    key, cert = await signer.credentials(login, who, ca=ca)
    return await asyncssh.connect(
        host, port=port, username=login,
        client_keys=[(key, cert)],
        known_hosts=pinned,
        # Only our certificate: no keys from ~/.ssh, no ssh-agent, no
        # passwords, nothing the person did not ask for.
        agent_path=None,
        password=None,
        kbdint_auth=False,
        preferred_auth=("publickey",),
        connect_timeout=CONNECT_TIMEOUT,
        keepalive_interval=30,
        keepalive_count_max=3,
    )


def explain(exc: BaseException, host: str, port: int) -> str:
    """Why connecting failed, in a sentence for the person and for the
    installer's output."""
    where = f"{host}:{port}"
    if isinstance(exc, (SignerUnavailable, SignerRefused)):
        return f"tTerm Web could not sign a certificate: {exc}"
    if isinstance(exc, HostKeysUnusable):
        return "The server sent no host key we could read."
    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        return (f"The server at {where} answered with a different host key "
                "than it registered with. Not connecting.")
    if isinstance(exc, asyncssh.PermissionDenied):
        return (f"The server at {where} did not accept our certificate. "
                "Did the script finish without errors?")
    if isinstance(exc, ConnectionRefusedError):
        return (f"Nothing answers on SSH at {where}: connection refused. "
                f"Is SSH listening on port {port}?")
    if isinstance(exc, TimeoutError):
        return (f"No answer from SSH at {where}. If a firewall lets only some "
                "addresses reach SSH, allow tTerm Web's. A machine without a "
                "public address connects as a computer instead.")
    if isinstance(exc, OSError):
        return f"Could not reach SSH at {where}: {exc.strerror or exc}."
    if isinstance(exc, asyncssh.Error):
        return f"SSH to {where} failed: {exc.reason}"
    return f"Connecting to {where} failed: {exc}"
