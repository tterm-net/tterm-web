"""The page and its API, for people.

Everyone signs in (from 0.0.9): the page and its files are open, the API
and the socket answer only within a session, and only about the person's
own projects, machines and terminals. Signing in is under /api/auth, see
core/auth.py.

The built page lives in web/dist. Every path that is not the API or a file of
the page gets index.html, so that addresses inside the page survive a reload.
"""
from __future__ import annotations

import asyncio
import html
import secrets
import uuid
from pathlib import Path

import asyncpg
from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from .. import __version__
from ..core import accounts, auth, letters, machines
from ..core.mail import Mailer
from ..core.terminals import NotAllowed, NotOpen, Terminal, Terminals
from .connect import client_ip
from .security import PAGE_HEADERS, AddHeaders, PageGuard, page_csp
from .ws import SIGNED_OUT_CLOSE, PageSocket

NO_STORE = {"cache-control": "no-store"}
#: Stage 1 builds the page with stable file names, so the browser must ask
#: every time whether a file changed. Cheap on localhost.
NO_CACHE = {"cache-control": "no-cache"}
#: tTerm's icon, 108 px, for the emails (letters.py).
MAIL_LOGO = Path(__file__).resolve().parent.parent / "templates" / "mail-logo.png"

PAGE_MISSING = """<!doctype html>
<meta charset="utf-8"><title>tTerm</title>
<p style="font: 15px sans-serif; margin: 40px">
The page is not built: <code>web/dist</code> is missing.
It comes ready in the release archive; unpack the archive again.
</p>
"""


class MePatch(BaseModel):
    #: Either or both. The name from 0.0.10, on the account page.
    theme: str | None = None
    name: str | None = None


class EmailBody(BaseModel):
    email: str


class CodeBody(BaseModel):
    email: str
    code: str


class PinBody(BaseModel):
    pin: str


class SignUpBody(BaseModel):
    #: The address on the screen: its code is the one this browser entered.
    email: str
    name: str
    #: The one tick, for both documents: the Terms of Service and the Privacy
    #: Policy.
    agree: bool = False


class SignedOut(Exception):
    """No session, or one that has ended: the page asks to sign in."""


#: How long the browser keeps a session's cookie; the session itself ends
#: sooner, after eight hours without anything done (auth.IDLE).
SESSION_DAYS = 30
#: The page marks what it asks by itself, now and then, with this header:
#: such a request is not something done, and does not keep a session on.
BACKGROUND_HEADER = "x-tterm-background"


class InstallRequest(BaseModel):
    projectId: uuid.UUID
    kind: str = "server"


class TerminalRequest(BaseModel):
    machineId: uuid.UUID
    cols: int = 80
    rows: int = 24


class CompleteRequest(BaseModel):
    line: str
    cursor: int


#: A line the input line may ask Tab about, at most.
MAX_LINE = 64 * 1024
NOT_OPEN = "The terminal is not open."


def _with_nonce(page: str, nonce: str) -> str:
    """The page with its one-time style number in a meta tag, right after
    the charset, which must stay first."""
    meta = f'<meta name="tt-style-nonce" content="{html.escape(nonce)}">'
    at = page.find("<meta charset")
    end = page.find(">", at) if at >= 0 else -1
    if end >= 0:
        return page[:end + 1] + "\n    " + meta + page[end + 1:]
    at = page.lower().find("<head>")
    if at >= 0:
        return page[:at + 6] + meta + page[at + 6:]
    # No head written out: after the doctype, never before it, or the
    # browser would fall back to its old quirks.
    end = page.find(">") if page.lower().startswith("<!doctype") else -1
    return page[:end + 1] + meta + page[end + 1:]


def _json(data, status: int = 200) -> Response:
    return JSONResponse(data, status_code=status, headers=NO_STORE)


def create_page_app(pool: asyncpg.Pool, dist: Path, page_url: str,
                    connect_url: str, terminals: Terminals | None = None,
                    agents=None, *, secret: bytes, mailer: Mailer,
                    legal: dict[str, str] | None = None):
    """The ASGI app for the page, wrapped in the page's guard. `legal` has
    the addresses the sign-in screens link to: the Terms of Service, the
    Privacy Policy, and support's email."""
    legal = dict(legal or {})
    # Support's address, at the bottom of the emails too.
    support = legal.get("support") or letters.SUPPORT
    # The cookies: on https with the __Host- prefix, which a browser takes
    # only from this very host, over https, for the whole site. On the
    # laptop's plain http that prefix is not accepted, so they go without.
    secure = page_url.startswith("https://")

    def cookie_name(base: str) -> str:
        return f"__Host-{base}" if secure else base

    def cookie(request: Request | WebSocket, base: str) -> str | None:
        return request.cookies.get(cookie_name(base)) or None

    def set_cookie(response: Response, base: str, value: str,
                   seconds: int) -> None:
        response.set_cookie(cookie_name(base), value, max_age=seconds,
                            path="/", secure=secure, httponly=True,
                            samesite="strict")

    def clear_cookie(response: Response, base: str) -> None:
        response.delete_cookie(cookie_name(base), path="/", secure=secure,
                               httponly=True, samesite="strict")

    async def signed_in(request: Request) -> auth.Session:
        session = await auth.session(
            pool, cookie(request, auth.SESSION_COOKIE),
            touch=request.headers.get(BACKGROUND_HEADER) != "1")
        if session is None:
            raise SignedOut()
        return session

    #: The page sockets open now: a session that ends closes its own at once.
    sockets: set[PageSocket] = set()

    async def cut(ended) -> None:
        """Closes the sockets of sessions that ended, so that whatever was
        open there asks to sign in: `ended` tells them by their session."""
        doomed = [s for s in sockets if ended(s.session)]
        await asyncio.gather(*(s.sign_out() for s in doomed),
                             return_exceptions=True)

    def refused(exc: auth.Refused) -> Response:
        return _json({"error": str(exc), **exc.details}, exc.status)

    def with_agents(machine: dict) -> dict:
        """A computer is online while its agent is connected; the page shows
        it and which agent it has."""
        if machine.get("kind") == "computer":
            agent = agents.describe(machine["id"]) if agents is not None else None
            machine["online"] = agent is not None
            machine["agent"] = agent
        return machine
    app = FastAPI(title="tTerm Web", version=__version__, docs_url=None,
                  redoc_url=None, openapi_url=None)
    dist = dist.resolve()

    @app.exception_handler(SignedOut)
    async def signed_out(request: Request, exc: SignedOut) -> Response:
        return _json({"error": "Sign in to go on.", "signedOut": True}, 401)

    # ------------------------------------------------------------ sign in

    @app.get("/api/auth/state")
    async def auth_state(request: Request) -> Response:
        """Signed in or not; if not, whether this browser is a trusted
        device, so that the page asks for its quick code instead of email."""
        if await auth.session(pool, cookie(request, auth.SESSION_COOKIE)):
            return _json({"signedIn": True, "legal": legal})
        device = await auth.device(pool, cookie(request, auth.DEVICE_COOKIE))
        return _json({"signedIn": False, "device": {
            "label": device.label, "name": device.name, "email": device.email,
        } if device else None, "legal": legal})

    @app.post("/api/auth/code")
    async def ask_code(body: EmailBody, request: Request) -> Response:
        """A code for signing in, sent to the address, for this browser."""
        email = auth.normalize_email(body.email)
        if email is None:
            return _json({"error": "That does not look like an email address."},
                         422)
        browser = cookie(request, auth.BROWSER_COOKIE) or auth.new_token()
        try:
            await auth.ask_code(pool, secret, mailer, email=email,
                                browser=browser, ip=client_ip(request),
                                page_url=page_url, support=support)
        except auth.Refused as exc:
            return refused(exc)
        response = _json({"sent": True, "email": email,
                          "minutes": auth.CODE_MINUTES}, 202)
        set_cookie(response, auth.BROWSER_COOKIE, browser, 24 * 3600)
        return response

    @app.post("/api/auth/verify")
    async def check_code(body: CodeBody, request: Request) -> Response:
        email = auth.normalize_email(body.email)
        code = auth.digits(body.code, auth.CODE_DIGITS)
        if email is None or code is None:
            return _json({"error": "Enter the six digits from the email."}, 422)
        browser = cookie(request, auth.BROWSER_COOKIE)
        if browser is None:
            return _json({"error": "This browser did not keep the sign-in "
                          "cookie. Allow cookies for this site, then ask for a "
                          "new code.", "left": 0}, 401)
        try:
            signed = await auth.check_code(
                pool, secret, mailer, email=email, code=code, browser=browser,
                ip=client_ip(request),
                label=auth.device_label(request.headers.get("user-agent", "")),
                page_url=page_url, support=support)
        except auth.Refused as exc:
            return refused(exc)
        if isinstance(signed, auth.SignUp):
            # No account yet: the name and the tick first, in this
            # browser, which keeps its cookie for that.
            return _json({"signUp": True, "email": signed.email})
        return await signed_in_now(request, signed)

    @app.post("/api/auth/signup")
    async def sign_up(body: SignUpBody, request: Request) -> Response:
        """Makes the account of the address whose code this browser just
        entered: the person's name, and their yes to the Terms of Service and
        the Privacy Policy."""
        email = auth.normalize_email(body.email)
        name = auth.clean_name(body.name)
        if name is None:
            return _json({"error": "Enter your name: up to "
                          f"{auth.NAME_MAX} characters."}, 422)
        if not body.agree:
            return _json({"error": "Agree to the Terms of Service and Privacy "
                          "Policy to go on."}, 422)
        browser = cookie(request, auth.BROWSER_COOKIE)
        try:
            if browser is None or email is None:
                raise auth.SignUpOver("Ask for a new code.")
            signed = await auth.sign_up(
                pool, email=email, browser=browser, name=name,
                ip=client_ip(request),
                label=auth.device_label(request.headers.get("user-agent", "")))
        except auth.Refused as exc:
            return refused(exc)
        return await signed_in_now(request, signed)

    async def signed_in_now(request: Request, signed: auth.SignedIn) -> Response:
        """Signed in with a code from email: the session's cookie, and the
        offer of a quick code for this device."""
        # A session this browser may still have ends: its cookie is replaced.
        await auth.sign_out(pool, cookie(request, auth.SESSION_COOKIE))
        response = _json({"signedIn": True, "new": signed.new,
                          "name": signed.person.first_name,
                          "offerPin": True, "pinDigits": auth.PIN_DIGITS})
        set_cookie(response, auth.SESSION_COOKIE, signed.token,
                   SESSION_DAYS * 24 * 3600)
        clear_cookie(response, auth.BROWSER_COOKIE)
        return response

    @app.post("/api/auth/pin")
    async def set_pin(body: PinBody, request: Request,
                      session: auth.Session = Depends(signed_in)) -> Response:
        """Trusts this browser with a quick code, right after signing in
        with a code from email."""
        pin = auth.digits(body.pin, auth.PIN_DIGITS)
        if pin is None:
            return _json({"error": f"The quick code is {auth.PIN_DIGITS} "
                          "digits."}, 422)
        if auth.too_easy(pin):
            return _json({"error": "That one is the first anyone would try. "
                          "Choose digits that are not all the same and not "
                          "in a row."}, 422)
        try:
            key = await auth.trust_device(
                pool, secret, session, pin,
                auth.device_label(request.headers.get("user-agent", "")),
                replaces=cookie(request, auth.DEVICE_COOKIE))
        except auth.Refused as exc:
            return refused(exc)
        response = _json({"trusted": True})
        set_cookie(response, auth.DEVICE_COOKIE, key,
                   auth.DEVICE_DAYS * 24 * 3600)
        return response

    @app.post("/api/auth/pin-login")
    async def pin_login(body: PinBody, request: Request) -> Response:
        pin = auth.digits(body.pin, auth.PIN_DIGITS)
        if pin is None:
            return _json({"error": f"The quick code is {auth.PIN_DIGITS} "
                          "digits."}, 422)
        key = cookie(request, auth.DEVICE_COOKIE)
        try:
            if key is None:
                raise auth.DeviceForgotten("Sign in with a code from email.")
            signed = await auth.check_pin(
                pool, secret, key=key, pin=pin, ip=client_ip(request),
                label=auth.device_label(request.headers.get("user-agent", "")))
        except auth.DeviceForgotten as exc:
            response = refused(exc)
            clear_cookie(response, auth.DEVICE_COOKIE)
            return response
        except auth.Refused as exc:
            return refused(exc)
        await auth.sign_out(pool, cookie(request, auth.SESSION_COOKIE))
        response = _json({"signedIn": True})
        set_cookie(response, auth.SESSION_COOKIE, signed.token,
                   SESSION_DAYS * 24 * 3600)
        return response

    @app.post("/api/auth/signout")
    async def sign_out(request: Request) -> Response:
        """Sign out on the account page: this browser forgets the person.
        The session ends, and a trusted device is forgotten too, so that
        next time it is a code from email; that is also how one switches
        to another account (from 0.0.10; until then the device stayed
        trusted, which is now what eight hours away does)."""
        ended = await auth.forget_browser(
            pool, cookie(request, auth.SESSION_COOKIE),
            cookie(request, auth.DEVICE_COOKIE))
        if ended:
            await cut(lambda s: s.id in ended)
        response = _json({"signedOut": True})
        clear_cookie(response, auth.SESSION_COOKIE)
        clear_cookie(response, auth.DEVICE_COOKIE)
        return response

    @app.post("/api/auth/signout-everywhere")
    async def sign_out_everywhere(
            session: auth.Session = Depends(signed_in)) -> Response:
        """Every session of the person ends and every device is forgotten:
        "Forget all devices" on the account page."""
        await auth.sign_out_everywhere(pool, session.person.id)
        await cut(lambda s: s.person.id == session.person.id)
        response = _json({"signedOut": True})
        clear_cookie(response, auth.SESSION_COOKIE)
        clear_cookie(response, auth.DEVICE_COOKIE)
        return response

    @app.get("/api/devices")
    async def list_devices(
            session: auth.Session = Depends(signed_in)) -> Response:
        return _json({"devices": await auth.devices(pool, session.person.id,
                                                    session.device_id)})

    @app.delete("/api/devices/{device_id}")
    async def forget_device(device_id: uuid.UUID,
                            session: auth.Session = Depends(signed_in)) -> Response:
        ended = await auth.forget_device(pool, session.person.id, device_id)
        if ended is None:
            return _json({"error": "no such device"}, 404)
        await cut(lambda s: s.id in ended)
        response = _json({"forgotten": True,
                          "current": device_id == session.device_id})
        if device_id == session.device_id:
            # This very browser: its session went with it.
            clear_cookie(response, auth.DEVICE_COOKIE)
            clear_cookie(response, auth.SESSION_COOKIE)
        return response

    @app.get("/health")
    async def health() -> Response:
        return _json({"ok": True, "service": "tterm-web page",
                      "version": __version__})

    # ------------------------------------------------- within a session

    @app.get("/api/state")
    async def state(session: auth.Session = Depends(signed_in)) -> Response:
        """Everything the page needs on start."""
        me = session.person
        projects = await accounts.projects_of(pool, me.id)
        for project in projects:
            project["machines"] = [with_agents(m) for m in project["machines"]]
        return _json({
            "version": __version__,
            "me": {
                "id": str(me.id),
                "name": me.name,
                "firstName": me.first_name,
                "initials": me.initials,
                "theme": me.theme,
                "plan": me.plan,
                "email": me.email,
            },
            "projects": projects,
            "terminals": ([t.describe() for t in terminals.live(me.id)]
                          if terminals else []),
        })

    @app.patch("/api/me")
    async def patch_me(change: MePatch,
                       session: auth.Session = Depends(signed_in)) -> Response:
        """The person's own settings: the theme, and from 0.0.10 the name.
        A new name comes back as the page shows it; the theme alone, with
        nothing."""
        me = session.person
        name = None
        if change.name is not None:
            name = auth.clean_name(change.name)
            if name is None:
                return _json({"error": "Enter your name: up to "
                              f"{auth.NAME_MAX} characters."}, 422)
        if change.theme is not None:
            try:
                await accounts.set_theme(pool, me.id, change.theme)
            except ValueError as exc:
                return _json({"error": str(exc)}, 422)
        if name is None:
            return Response(status_code=204, headers=NO_STORE)
        await accounts.set_name(pool, me.id, name)
        return _json({"name": name, "firstName": accounts.first_name(name),
                      "initials": accounts.initials(name)})

    @app.get("/api/account")
    async def account(session: auth.Session = Depends(signed_in)) -> Response:
        """The account page (0.0.10): the plan with what counts against its
        limits, the trusted devices, and where the documents and support
        are. The name, the address and the theme come with /api/state."""
        me = session.person
        plan = await accounts.plan_of(pool, me.id)
        return _json({
            "plan": {"id": plan["id"], "name": plan["name"],
                     "limits": plan["limits"]},
            "usage": {"machines": plan["machines"],
                      "terminals": len(terminals.live(me.id)) if terminals else 0},
            "devices": await auth.devices(pool, me.id, session.device_id),
            "legal": legal,
        })

    @app.post("/api/installs")
    async def new_install(request: InstallRequest,
                          session: auth.Session = Depends(signed_in)) -> Response:
        """A one-time command that connects a server, for ten minutes; for a
        computer, the command that starts its agent with a new key."""
        try:
            install = await machines.create_install(
                pool, project_id=request.projectId, user_id=session.person.id,
                kind=request.kind, connect_url=connect_url)
        except PermissionError as exc:
            return _json({"error": str(exc)}, 403)
        except ValueError as exc:
            return _json({"error": str(exc)}, 422)
        return _json({"id": str(install.id), "command": install.command,
                      "byHand": install.by_hand,
                      "expiresAt": install.expires_at.isoformat(),
                      "minutes": machines.INSTALL_MINUTES}, 201)

    @app.get("/api/installs/{install_id}")
    async def install_status(install_id: uuid.UUID,
                             session: auth.Session = Depends(signed_in)) -> Response:
        status = await machines.install_status(pool, install_id,
                                               session.person.id)
        if status is None:
            return _json({"error": "no such install"}, 404)
        return _json(status)

    @app.get("/api/machines/{machine_id}")
    async def machine(machine_id: uuid.UUID,
                      session: auth.Session = Depends(signed_in)) -> Response:
        details = await machines.machine_details(pool, session.person.id,
                                                 machine_id)
        if details is None:
            return _json({"error": "no such machine"}, 404)
        return _json(with_agents(details))

    @app.delete("/api/machines/{machine_id}")
    async def remove_machine(machine_id: uuid.UUID,
                             session: auth.Session = Depends(signed_in)) -> Response:
        """Takes the machine out of tTerm Web. Its terminals end, a
        computer's agent hears it is removed and stops; the history stays.
        For a server, the answer carries the command that takes tTerm
        Web's access back on the server itself."""
        try:
            removed = await machines.remove(pool, session.person.id, machine_id)
        except PermissionError as exc:
            return _json({"error": str(exc)}, 403)
        if removed is None:
            return _json({"error": "no such machine"}, 404)
        ending = f"{removed['name']} was removed from tTerm Web."
        if agents is not None and removed["kind"] == "computer":
            agents.revoke(machine_id, ending)
        if terminals is not None:
            await terminals.end_machine(machine_id, ending)
        return _json(removed)

    @app.post("/api/terminals")
    async def open_terminal(request: TerminalRequest,
                            session: auth.Session = Depends(signed_in)) -> Response:
        """A new terminal on a machine. It connects in the background; the
        page attaches to it over the socket and watches it come up."""
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        try:
            terminal = await terminals.open(session.person, request.machineId,
                                            request.cols, request.rows)
        except NotAllowed as exc:
            return _json({"error": str(exc)}, 404)
        return _json(terminal.describe(), 201)

    @app.get("/api/terminals/{terminal_id}")
    async def terminal_info(terminal_id: uuid.UUID,
                            session: auth.Session = Depends(signed_in)) -> Response:
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        terminal = terminals.get(terminal_id, session.person.id)
        if terminal is not None:
            return _json(terminal.describe())
        ended = await terminals.ended(terminal_id, session.person.id)
        if ended is None:
            return _json({"error": "no such terminal"}, 404)
        return _json(ended)

    @app.delete("/api/terminals/{terminal_id}")
    async def end_terminal(terminal_id: uuid.UUID,
                           session: auth.Session = Depends(signed_in)) -> Response:
        """Ends a terminal: its shell gets a hangup, as when a window
        closes. One whose computer is away stops waiting for it, and the
        shell there ends when the computer is back."""
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        terminal = terminals.get(terminal_id, session.person.id)
        if terminal is None:
            return _json({"error": "no such terminal"}, 404)
        await terminal.close()
        return _json(terminal.describe())

    @app.get("/api/terminals/{terminal_id}/blocks")
    async def terminal_blocks(terminal_id: uuid.UUID, before: int | None = None,
                              limit: int = 60,
                              session: auth.Session = Depends(signed_in)) -> Response:
        """The feed: finished blocks, the latest first page of them, each
        with the end of its output."""
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        feed = await terminals.feed(terminal_id, session.person.id, before,
                                    limit)
        if feed is None:
            return _json({"error": "no such terminal"}, 404)
        return _json(feed)

    @app.get("/api/terminals/{terminal_id}/blocks/{seq}/output")
    async def block_output(terminal_id: uuid.UUID, seq: int,
                           session: auth.Session = Depends(signed_in)) -> Response:
        """Everything a block kept of its output, as the terminal printed it."""
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        got = await terminals.block_output(terminal_id, session.person.id, seq)
        if got is None:
            return _json({"error": "no such block"}, 404)
        output, cut = got
        return Response(output, media_type="application/octet-stream",
                        headers={**NO_STORE, "x-tterm-cut": "1" if cut else "0"})

    @app.get("/api/terminals/{terminal_id}/history")
    async def terminal_history(terminal_id: uuid.UUID,
                               session: auth.Session = Depends(signed_in)) -> Response:
        if terminals is None:
            return _json({"error": "terminals are not running"}, 503)
        commands = await terminals.history(terminal_id, session.person.id)
        if commands is None:
            return _json({"error": "no such terminal"}, 404)
        return _json({"commands": commands})

    def _open(terminal_id: uuid.UUID, session: auth.Session) -> Terminal | None:
        terminal = (terminals.get(terminal_id, session.person.id)
                    if terminals else None)
        if terminal is None or terminal.state != "open":
            return None
        return terminal

    # Tab and the two lists run a small script on the machine: changes in
    # the HTTP sense or not, they carry the page's header like every POST.

    @app.post("/api/terminals/{terminal_id}/complete")
    async def complete_word(terminal_id: uuid.UUID, request: CompleteRequest,
                            session: auth.Session = Depends(signed_in)) -> Response:
        terminal = _open(terminal_id, session)
        if terminal is None:
            return _json({"error": NOT_OPEN}, 409)
        if len(request.line) > MAX_LINE:
            return _json({"error": "The line is too long."}, 422)
        try:
            return _json(await terminal.complete(request.line, request.cursor))
        except NotOpen:
            return _json({"error": NOT_OPEN}, 409)

    @app.post("/api/terminals/{terminal_id}/dirs")
    async def terminal_dirs(terminal_id: uuid.UUID,
                            session: auth.Session = Depends(signed_in)) -> Response:
        terminal = _open(terminal_id, session)
        if terminal is None:
            return _json({"error": NOT_OPEN}, 409)
        try:
            return _json(await terminal.dirs())
        except NotOpen:
            return _json({"error": NOT_OPEN}, 409)

    @app.post("/api/terminals/{terminal_id}/branches")
    async def terminal_branches(terminal_id: uuid.UUID,
                                session: auth.Session = Depends(signed_in)) -> Response:
        terminal = _open(terminal_id, session)
        if terminal is None:
            return _json({"error": NOT_OPEN}, 409)
        try:
            return _json(await terminal.branches())
        except NotOpen:
            return _json({"error": NOT_OPEN}, 409)

    @app.websocket("/api/ws")
    async def socket(websocket: WebSocket) -> None:
        if terminals is None:
            await websocket.close(code=1013)
            return
        # The page opens its socket by itself, on start and after a lost
        # connection: not something done.
        session = await auth.session(
            pool, cookie(websocket, auth.SESSION_COOKIE), touch=False)
        if session is None:
            # Accepted to be closed with a reason the page understands.
            await websocket.accept()
            await websocket.close(code=SIGNED_OUT_CLOSE)
            return
        page_socket = PageSocket(websocket, terminals, session, pool)
        sockets.add(page_socket)
        try:
            await page_socket.run()
        finally:
            sockets.discard(page_socket)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH",
                                                "DELETE"])
    async def api_missing(rest: str) -> Response:
        return _json({"error": "no such API"}, 404)

    @app.get(letters.LOGO_PATH)
    async def mail_logo() -> Response:
        """The logo in the emails. Mail apps and their image proxies fetch
        it from wherever the email is read, so this one file, unlike the
        rest of the page, other sites may load."""
        return FileResponse(MAIL_LOGO, media_type="image/png", headers={
            "cache-control": "public, max-age=604800",
            "cross-origin-resource-policy": "cross-origin"})

    @app.get("/{path:path}")
    async def page(path: str) -> Response:
        if path:
            candidate = (dist / path).resolve()
            # Only files inside dist; `..` and absolute paths end up elsewhere.
            if candidate.is_file() and candidate.is_relative_to(dist):
                return FileResponse(candidate, headers=NO_CACHE)
            if Path(path).suffix:
                # Looks like a file of the page that is not there.
                return Response("Not found\n", status_code=404,
                                media_type="text/plain", headers=NO_CACHE)
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(PAGE_MISSING, status_code=503, headers=NO_STORE)
        # A fresh number for the page's own style elements, every time.
        nonce = secrets.token_urlsafe(18)
        text = _with_nonce(index.read_text(encoding="utf-8"), nonce)
        return HTMLResponse(text, headers={
            **NO_STORE, "content-security-policy": page_csp(nonce)})

    # The guard inside the headers, so that its refusals carry them too.
    return AddHeaders(PageGuard(app, page_url), PAGE_HEADERS)
