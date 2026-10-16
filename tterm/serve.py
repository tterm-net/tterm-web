"""tTerm Web on the server: python -m tterm.serve

The core alone: the page with its API, and the machines' side. The signer
is a service of its own there (python -m tterm.signer, as another user),
and the core asks it over its socket. systemd starts and stops both; see
deploy/README.md. On the laptop, python -m tterm.dev does all of it.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from . import __version__
from .core import db
from .core.config import Settings
from .core.mail import BrevoMailer
from .server import Core, PortBusy

log = logging.getLogger("tterm")


async def run(settings: Settings) -> int:
    core = Core(settings)
    try:
        await core.start()
    except (db.DatabaseUnavailable, PortBusy, RuntimeError) as exc:
        log.error("%s", exc)
        return 1
    ca = core.active_ca
    log.warning("tTerm Web %s: page %s on port %s, machines %s on port %s, "
                "signer %s", __version__, settings.page_url, core.page_port,
                settings.connect_url, core.connect_port,
                ca["id"] if ca else "not answering")
    if not isinstance(core.mailer, BrevoMailer):
        log.warning("no TTERM_BREVO_KEY: the codes for signing in go to this "
                    "log instead of email")

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    waiter = asyncio.create_task(stopping.wait())
    died = asyncio.create_task(core.wait())
    await asyncio.wait([waiter, died], return_when=asyncio.FIRST_COMPLETED)
    if not stopping.is_set():
        log.error("a server stopped on its own; stopping everything")
    await core.stop()
    for task in (waiter, died):
        task.cancel()
    return 0 if stopping.is_set() else 1


def main() -> None:
    # journald puts the time on each line itself.
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    logging.getLogger("asyncssh").setLevel(logging.WARNING)
    sys.exit(asyncio.run(run(Settings.load())))


if __name__ == "__main__":
    main()
