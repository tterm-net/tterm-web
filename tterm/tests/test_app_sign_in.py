"""tTerm for the computer signs in with the browser (0.1.6): the browser
allows, the app on 127.0.0.1 trades the code and its secret for a device key,
asks sessions with it, and says which computer it runs on."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import secrets
import uuid
from urllib.parse import parse_qs, urlsplit

import websockets

from ..api import ws as ws_module
from ..core import auth, db, letters, machines
from .harness import page_socket, request
from .test_auth import (CHANGE, SAFARI_IPHONE, Browser, closed_with,
                        codes_are_old, sign_in)

EMAIL = "anna@example.com"


class App:
    """tTerm for the computer, as the core sees it: no cookies of its own,
    a secret for PKCE, then a device key and a session."""

    def __init__(self, port: int, *, name: str = "MacBook-Pro-Anna") -> None:
        self.port = port
        self.name = name
        self.verifier = secrets.token_urlsafe(48)
        self.state = secrets.token_urlsafe(16)
        self.key = ""
        self.session = ""
        self.device_id = ""

    @property
    def challenge(self) -> str:
        return auth.pkce_challenge(self.verifier)

    def link(self, port: int = 51234) -> dict:
        """What the app puts in the address of /connect."""
        return {"app": "mac", "name": self.name, "port": port,
                "challenge": self.challenge, "state": self.state}

    async def call(self, method: str, path: str, body=None, *,
                   cookie: str | None = None):
        headers = dict(CHANGE) if method not in ("GET", "HEAD") else {}
        cookie = self.cookie if cookie is None else cookie
        if cookie:
            headers["cookie"] = cookie
        data = json.dumps(body).encode() if body is not None else None
        return await request(self.port, method, path, headers=headers,
                             body=data)

    @property
    def cookie(self) -> str:
        return f"{auth.SESSION_COOKIE}={self.session}" if self.session else ""

    async def exchange(self, code: str, verifier: str | None = None):
        r = await self.call("POST", "/api/auth/app/exchange",
                            {"code": code, "verifier": verifier or self.verifier},
                            cookie="")
        if r.status == 200:
            got = r.json()
            self.key, self.session = got["key"], got["session"]
            self.device_id = got["deviceId"]
        return r

    async def new_session(self):
        r = await self.call("POST", "/api/auth/app/session", {"key": self.key},
                            cookie="")
        if r.status == 200:
            self.session = r.json()["session"]
        return r


def code_of(redirect: str) -> str:
    parts = urlsplit(redirect)
    return parse_qs(parts.query)["code"][0]


async def allowed(core, browser: Browser, app: App, port: int = 51234) -> str:
    """The browser allows the app: the code from where the page goes next."""
    r = await browser.call("POST", "/api/auth/app/allow", app.link(port))
    assert r.status == 200, r.text
    return code_of(r.json()["redirect"])


async def signed_in_app(core, browser: Browser | None = None,
                        app: App | None = None) -> tuple[Browser, App]:
    """Anna signed in in a browser, and her app signed in through it."""
    if browser is None:
        browser = Browser(core.page_port)
        await sign_in(core, browser, EMAIL)
    app = app or App(core.page_port)
    r = await app.exchange(await allowed(core, browser, app))
    assert r.status == 200, r.text
    return browser, app


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def fresh_browser(core, email: str = EMAIL) -> Browser:
    browser = Browser(core.page_port)
    await codes_are_old(core)
    await sign_in(core, browser, email)
    return browser


# ---------------------------------------------------------------- sign in

def test_the_challenge_is_rfc_7636s():
    # The example of RFC 7636, appendix B.
    assert auth.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") \
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert auth.is_challenge("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
    assert not auth.is_challenge("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-c")
    assert not auth.is_challenge("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw+cM")
    assert auth.is_verifier("a" * 43) and auth.is_verifier("a~b.c-d_" * 16)
    assert not auth.is_verifier("a" * 42) and not auth.is_verifier("a" * 129)
    assert not auth.is_verifier("a" * 42 + "/")
    assert auth.computer_name("  MacBook   Pro ") == "MacBook Pro"
    assert auth.computer_name("x" * 65) is None
    assert auth.computer_name("bell\x07") is None and auth.computer_name(5) is None
    assert auth.app_label("mac", "MacBook-Pro-Anna") == \
        "tTerm for Mac on MacBook-Pro-Anna"


async def test_an_app_signs_in_with_the_browser(h):
    async with h.core(owner_email="") as core:
        browser = Browser(core.page_port)
        await sign_in(core, browser, EMAIL)
        app = App(core.page_port)
        r = await browser.call("POST", "/api/auth/app/allow", app.link(51234))
        assert r.status == 200, r.text
        redirect = r.json()["redirect"]
        parts = urlsplit(redirect)
        assert (parts.scheme, parts.hostname, parts.port, parts.path) == \
            ("http", "127.0.0.1", 51234, "/signed-in"), redirect
        query = parse_qs(parts.query)
        assert query["state"] == [app.state], "the app's own state comes back"
        code = query["code"][0]
        assert len(code) >= 32
        sent = len(core.mailer.sent)
        r = await app.exchange(code)
        assert r.status == 200, r.text
        got = r.json()
        assert got["label"] == "tTerm for Mac on MacBook-Pro-Anna"
        assert got["cookie"] == auth.SESSION_COOKIE, "plain http: no prefix"
        assert got["me"]["email"] == EMAIL and got["me"]["name"] == "Anna"
        assert got["hub"] == "wss://connect.test/agent"
        assert len(got["key"]) >= 32 and got["key"] != got["session"]
        # The app's session is a session like the browser's.
        r = await app.call("GET", "/api/state")
        assert r.status == 200 and r.json()["me"]["email"] == EMAIL, r.text
        r = await app.call("GET", "/api/auth/state")
        assert r.json()["signedIn"] is True
        # Once only.
        r = await app.exchange(code)
        assert r.status == 410 and "Start again" in r.json()["error"], r.text
        # Anna hears of it, and sees it in Devices.
        mail = core.mailer.sent[sent:]
        assert len(mail) == 1 and mail[0].to == EMAIL, mail
        assert mail[0].subject == "New sign-in to tTerm"
        assert "tTerm for Mac on MacBook-Pro-Anna signed in as" in mail[0].text
        assert "App: tTerm for Mac on MacBook-Pro-Anna" in mail[0].text
        listed = (await browser.call("GET", "/api/devices")).json()["devices"]
        assert [(d["kind"], d["label"], d["current"]) for d in listed] == [
            ("app", "tTerm for Mac on MacBook-Pro-Anna", False)], listed
        listed = (await app.call("GET", "/api/devices")).json()["devices"]
        assert [(d["kind"], d["current"]) for d in listed] == [("app", True)]
        kinds = [(r["kind"], r["details"]) for r in await core.pool.fetch(
            "SELECT kind, details FROM journal WHERE kind LIKE 'user.%' "
            "ORDER BY id")]
        assert ("user.app_allowed",
                json.dumps({"app": "tTerm for Mac on MacBook-Pro-Anna"})) in \
            [(k, json.dumps(json.loads(d))) for k, d in kinds], kinds
        assert any(k == "user.device_trusted" and json.loads(d)["kind"] == "app"
                   for k, d in kinds), kinds
        assert any(k == "user.signed_in" and json.loads(d)["how"] == "app"
                   for k, d in kinds), kinds
        # Only hashes are kept.
        stored = await core.pool.fetchrow(
            "SELECT key_hash, pin_hash, kind FROM devices WHERE kind = 'app'")
        assert stored["key_hash"] == auth.token_hash(app.key)
        assert stored["pin_hash"] is None
        assert await core.pool.fetchval(
            "SELECT code_hash FROM app_grants") == auth.token_hash(code)


async def test_a_code_works_only_with_its_secret_once_and_soon(h):
    async with h.core(owner_email="") as core:
        browser = Browser(core.page_port)
        await sign_in(core, browser, EMAIL)
        app = App(core.page_port)
        # Someone who caught the code on its way, without the secret: it is
        # spent, and the app starts again.
        code = await allowed(core, browser, app)
        r = await app.exchange(code, verifier=secrets.token_urlsafe(48))
        assert r.status == 410, r.text
        r = await app.exchange(code)
        assert r.status == 410, "a wrong secret spends the code"
        # Too late.
        code = await allowed(core, browser, app)
        await core.pool.execute(
            "UPDATE app_grants SET expires_at = now() - interval '1 second'")
        r = await app.exchange(code)
        assert r.status == 410, r.text
        # Not even looked up when it cannot be one.
        for bad in ({"code": "short", "verifier": app.verifier},
                    {"code": "x" * 43, "verifier": "short"}):
            r = await app.call("POST", "/api/auth/app/exchange", bad, cookie="")
            assert r.status == 410, (bad, r.text)
        r = await app.call("POST", "/api/auth/app/exchange", {"code": "x"},
                           cookie="")
        assert r.status == 422, "the page's own refusal of a body it cannot read"
        # The page's guard holds here too.
        r = await request(core.page_port, "POST", "/api/auth/app/exchange",
                          headers={"content-type": "application/json"},
                          body=json.dumps({"code": code,
                                           "verifier": app.verifier}).encode())
        assert r.status == 403, "without x-tterm"
        r = await app.exchange(await allowed(core, browser, app))
        assert r.status == 200, "a fresh one works"
        assert await core.pool.fetchval(
            "SELECT count(*) FROM devices WHERE kind = 'app'") == 1


async def test_allow_takes_only_what_a_link_of_the_app_says(h):
    async with h.core(owner_email="") as core:
        app = App(core.page_port)
        r = await request(core.page_port, "POST", "/api/auth/app/allow",
                          headers=CHANGE, body=json.dumps(app.link()).encode())
        assert r.status == 401 and r.json()["signedOut"] is True, \
            "signed in first, in the browser"
        browser = Browser(core.page_port)
        await sign_in(core, browser, EMAIL)
        good = app.link()
        for change in ({"app": "amiga"}, {"port": 80}, {"port": 70000},
                       {"challenge": "x" * 42}, {"challenge": "x" * 42 + "="},
                       {"state": "short"}, {"state": "x" * 129},
                       {"state": "a/b" * 8}, {"name": ""}, {"name": "x" * 65},
                       {"name": "bell\x07"}):
            r = await browser.call("POST", "/api/auth/app/allow",
                                   {**good, **change})
            assert r.status == 422 and "not complete" in r.json()["error"], \
                (change, r.text)
        r = await browser.call("POST", "/api/auth/app/allow",
                               {**good, "name": "  Anna's   MacBook "})
        assert r.status == 200, r.text
        assert await core.pool.fetchval("SELECT name FROM app_grants") == \
            "Anna's MacBook", "spaces tidied, the rest as the app said it"
        # Not from an app's own session: apps are allowed in a browser.
        _, signed = await signed_in_app(core, browser)
        r = await signed.call("POST", "/api/auth/app/allow", App(0).link())
        assert r.status == 400 and "in a browser" in r.json()["error"], r.text
        # Not endlessly.
        await core.pool.execute("DELETE FROM app_grants")
        for _ in range(auth.APP_GRANTS_PER_HOUR):
            r = await browser.call("POST", "/api/auth/app/allow", good)
            assert r.status == 200, r.text
        r = await browser.call("POST", "/api/auth/app/allow", good)
        assert r.status == 429 and r.json()["wait"] == 3600, r.text
        await core.pool.execute(
            "UPDATE app_grants SET created_at = now() - interval '61 minutes'")
        r = await browser.call("POST", "/api/auth/app/allow", good)
        assert r.status == 200, "an hour later, again"


# ---------------------------------------------------------------- the key

async def test_the_app_asks_sessions_with_its_key(h):
    async with h.core(owner_email="") as core:
        browser, app = await signed_in_app(core)
        # A browser's key is no app's.
        await browser.call("POST", "/api/auth/pin", {"pin": "2741"})
        r = await app.call("POST", "/api/auth/app/session",
                           {"key": browser.jar["tterm-device"]}, cookie="")
        assert r.status == 410, r.text
        first = app.session
        async with page_socket(core.page_port, cookie=app.cookie) as ws:
            r = await app.new_session()
            assert r.status == 200, r.text
            assert r.json()["cookie"] == auth.SESSION_COOKIE
            assert app.session != first
            # One at a time: the one before ends, and its socket closes.
            assert await closed_with(ws, 3) == ws_module.SIGNED_OUT_CLOSE
        r = await app.call("GET", "/api/state",
                           cookie=f"{auth.SESSION_COOKIE}={first}")
        assert r.status == 401, "the session before ended"
        assert (await app.call("GET", "/api/state")).status == 200
        # Eight hours away end the session, not the app: its key brings it
        # back, and counts as using the device.
        await core.pool.execute(
            "UPDATE sessions SET last_seen_at = now() - $1::interval",
            auth.IDLE + auth.TOUCH_EVERY)
        await core.pool.execute(
            "UPDATE devices SET last_used_at = now() - interval '89 days'")
        assert (await app.call("GET", "/api/state")).status == 401
        assert (await app.new_session()).status == 200
        assert (await app.call("GET", "/api/state")).status == 200
        assert await core.pool.fetchval(
            "SELECT last_used_at > now() - interval '1 minute' FROM devices "
            "WHERE kind = 'app'") is True
        # Not used for DEVICE_DAYS: forgotten, and signing in again it is.
        await core.pool.execute(
            "UPDATE devices SET last_used_at = now() - make_interval(days => $1)",
            auth.DEVICE_DAYS + 1)
        r = await app.new_session()
        assert r.status == 410 and "not used for" in r.json()["error"], r.text
        assert (await app.call("GET", "/api/state")).status == 401
        # A key that is none: no session.
        for key in (auth.new_token(), "short"):
            r = await app.call("POST", "/api/auth/app/session", {"key": key},
                               cookie="")
            assert r.status == 410, r.text


async def test_an_apps_key_is_no_browsers(h):
    async with h.core(owner_email="") as core:
        browser, app = await signed_in_app(core)
        thief = Browser(core.page_port)
        thief.jar["tterm-device"] = app.key
        r = await thief.call("GET", "/api/auth/state")
        assert r.json()["device"] is None, "no quick code to ask for"
        r = await thief.call("POST", "/api/auth/pin-login", {"pin": "2741"})
        assert r.status == 410, r.text
        assert (await app.new_session()).status == 200, \
            "and a try with its key does not forget the app"
        # Its session sets no quick code.
        r = await app.call("POST", "/api/auth/pin", {"pin": "2741"})
        assert r.status == 400, r.text


async def test_forget_and_sign_out_sign_the_app_out(h):
    async with h.core(owner_email="") as core:
        browser, app = await signed_in_app(core)
        async with page_socket(core.page_port, cookie=app.cookie) as ws:
            r = await browser.call("DELETE", f"/api/devices/{app.device_id}")
            assert r.status == 200 and r.json() == {"forgotten": True,
                                                    "current": False}, r.text
            assert await closed_with(ws, 3) == ws_module.SIGNED_OUT_CLOSE
        assert (await app.call("GET", "/api/state")).status == 401
        r = await app.new_session()
        assert r.status == 410 and "signed out" in r.json()["error"], r.text
        # Forget all devices takes the app too.
        _, app = await signed_in_app(core, browser, App(core.page_port))
        await browser.call("POST", "/api/auth/signout-everywhere")
        assert (await app.new_session()).status == 410
        # The app's own Sign out: its session and its key, as a browser's.
        browser = Browser(core.page_port, agent=SAFARI_IPHONE)
        await codes_are_old(core)
        await sign_in(core, browser, EMAIL)
        _, app = await signed_in_app(core, browser, App(core.page_port))
        r = await app.call("POST", "/api/auth/signout", {}, cookie=(
            f"{auth.SESSION_COOKIE}={app.session}; "
            f"{auth.DEVICE_COOKIE}={app.key}"))
        assert r.status == 200, r.text
        assert (await app.call("GET", "/api/state")).status == 401
        assert (await app.new_session()).status == 410
        assert (await browser.call("GET", "/api/state")).status == 200, \
            "the browser that allowed it stays signed in"


async def test_cookies_of_an_app_on_https_are_named_with_the_prefix(h):
    async with h.core(owner_email="", environ={
            "TTERM_PAGE_URL": "https://app.example.test"}) as core:
        host = "app.example.test"
        origin = {"origin": "https://app.example.test"}
        async with core.pool.acquire() as conn, conn.transaction():
            person = await auth._make_account(conn, EMAIL, "Anna")
        token = await auth.open_session(core.pool, person.id)
        app = App(core.page_port)
        r = await request(core.page_port, "POST", "/api/auth/app/allow",
                          host=host, headers={
                              **CHANGE, **origin,
                              "cookie": f"__Host-{auth.SESSION_COOKIE}={token}"},
                          body=json.dumps(app.link()).encode())
        assert r.status == 200, r.text
        code = code_of(r.json()["redirect"])
        # The app sends no Origin: it is not a browser.
        r = await request(core.page_port, "POST", "/api/auth/app/exchange",
                          host=host, headers=CHANGE, body=json.dumps(
                              {"code": code, "verifier": app.verifier}).encode())
        assert r.status == 200, r.text
        got = r.json()
        assert got["cookie"] == f"__Host-{auth.SESSION_COOKIE}"
        r = await request(core.page_port, "GET", "/api/state", host=host,
                          headers={"cookie": f"{got['cookie']}={got['session']}"})
        assert r.status == 200, r.text


# ----------------------------------------------------------- the computer

async def test_the_app_says_which_computer_it_runs_on(h):
    async with h.core(owner_email="") as core:
        browser, app = await signed_in_app(core)
        key = machines.new_token("computer")
        said = {"key": key, "name": "MacBook-Pro-Anna",
                "os": "macOS 15.1", "app": "tterm-app 0.3.0"}
        r = await app.call("POST", "/api/computers/this", said)
        assert r.status == 201, r.text
        got = r.json()
        assert got["created"] is True and got["hub"] == "wss://connect.test/agent"
        machine = got["machine"]
        assert machine["name"] == "MacBook-Pro-Anna" and machine["code"] == "P"
        state = (await app.call("GET", "/api/state")).json()
        personal = next(p for p in state["projects"] if p["personal"])
        assert personal["id"] == machine["projectId"]
        listed = next(m for m in personal["machines"] if m["id"] == machine["id"])
        assert listed["kind"] == "computer" and listed["online"] is False
        assert listed["lastSeen"] is None and listed["os"] == "macOS 15.1"
        assert await core.pool.fetchval(
            "SELECT agent_token_hash FROM machines WHERE id = $1",
            uuid.UUID(machine["id"])) == machines.token_hash(key), \
            "only the hash"
        # Again, and on every start: the same one.
        r = await app.call("POST", "/api/computers/this", said)
        assert r.status == 200 and r.json()["created"] is False, r.text
        assert r.json()["machine"]["id"] == machine["id"]
        # The agent with that key is that computer: what the app does
        # itself from its step 3.
        hub = f"ws://127.0.0.1:{core.connect_port}/agent"
        async with websockets.connect(hub, proxy=None) as ws:
            await ws.send(json.dumps({"t": "hello", "token": key, "key": key,
                                      "name": "something-else", "web": 2,
                                      "agent": "0.14.0"}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
        assert reply == {"t": "welcome", "name": "MacBook-Pro-Anna"}, reply
        # Not from a browser's session.
        r = await browser.call("POST", "/api/computers/this", said)
        assert r.status == 403, r.text
        r = await app.call("POST", "/api/computers/this",
                           {**said, "key": "ABC"})
        assert r.status == 422, r.text
        r = await app.call("POST", "/api/computers/this",
                           {**said, "key": key + "\n"})
        assert r.status == 422, r.text
        # Bob's computer is not Anna's to take.
        bob = Browser(core.page_port)
        await sign_in(core, bob, "bob@example.com")
        _, bob_app = await signed_in_app(core, bob, App(core.page_port))
        r = await bob_app.call("POST", "/api/computers/this", said)
        assert r.status == 409 and "another account" in r.json()["error"], r.text
        # A removed computer's key never works again: the app makes another.
        r = await browser.call("DELETE", f"/api/machines/{machine['id']}")
        assert r.status == 200, r.text
        r = await app.call("POST", "/api/computers/this", said)
        assert r.status == 410 and "removed" in r.json()["error"], r.text
        fresh = {**said, "key": machines.new_token("computer")}
        r = await app.call("POST", "/api/computers/this", fresh)
        assert r.status == 201 and r.json()["machine"]["name"] == \
            "MacBook-Pro-Anna", "the name is free again"
        # A name taken in Personal gets a number.
        other = {**said, "key": machines.new_token("computer")}
        r = await app.call("POST", "/api/computers/this", other)
        assert r.status == 201 and r.json()["machine"]["name"] == \
            "MacBook-Pro-Anna-2", r.text
        kinds = [json.loads(r["details"]).get("via") for r in await core.pool.fetch(
            "SELECT details FROM journal WHERE kind = 'machine.added'")]
        assert kinds == ["app", "app", "app"], kinds


async def test_the_computer_of_the_agent_is_the_apps(h):
    async with h.core(owner_email="") as core:
        browser, app = await signed_in_app(core)
        state = (await browser.call("GET", "/api/state")).json()
        personal = next(p for p in state["projects"] if p["personal"])
        # A computer the installer brought in, with its own key.
        key = machines.new_token("computer")
        r = await browser.call("POST", "/api/installs",
                               {"projectId": personal["id"], "kind": "computer"})
        assert r.status == 201, r.text
        token = r.json()["command"].split("/c/")[1].split()[0]
        claimed = await machines.claim(core.pool, token)
        machine_id, name = await machines.add_computer(
            core.pool, claimed, "annas-mac.local", {"os": "macOS"}, key=key)
        r = await app.call("POST", "/api/computers/this",
                           {"key": key, "name": "MacBook-Pro-Anna"})
        assert r.status == 200, r.text
        assert r.json()["machine"] == {"id": str(machine_id), "name": name,
                                       "projectId": personal["id"], "code": "P"}
        assert r.json()["created"] is False, "no second Mac"
        # Moved to a project of her own: still hers, and still found.
        r = await browser.call("POST", "/api/projects", {"name": "Lab"})
        lab = r.json()["id"]
        r = await browser.call("POST", f"/api/machines/{machine_id}/move",
                               {"projectId": lab})
        assert r.status == 200, r.text
        r = await app.call("POST", "/api/computers/this",
                           {"key": key, "name": "x"})
        assert r.status == 200 and r.json()["machine"]["projectId"] == lab


async def test_a_new_computer_counts_against_the_plan(h):
    async with h.core(owner_email="") as core:
        _, app = await signed_in_app(core)
        await core.pool.execute(
            "UPDATE plans SET limits = jsonb_set(limits, '{machines}', '0')")
        r = await app.call("POST", "/api/computers/this",
                           {"key": machines.new_token("computer"),
                            "name": "MacBook-Pro-Anna"})
        assert r.status == 403 and "plan allows 0 machines" in r.json()["error"], \
            r.text
        assert await core.pool.fetchval("SELECT count(*) FROM machines") == 0


async def test_a_command_token_is_no_computers_key(h):
    """Found by the review of 0.1.6: the agent started by hand takes its
    command's token for the computer's key, and the token is shown in Add a
    machine. Whoever saw it must not claim the computer before it joins."""
    async with h.core(owner_email="") as core:
        anna = Browser(core.page_port)
        await sign_in(core, anna, EMAIL)
        state = (await anna.call("GET", "/api/state")).json()
        personal = next(p for p in state["projects"] if p["personal"])
        r = await anna.call("POST", "/api/installs",
                            {"projectId": personal["id"], "kind": "computer"})
        assert r.status == 201, r.text
        token = r.json()["byHand"].split()[0].split("=", 1)[1]
        bob = await fresh_browser(core, "bob@example.com")
        _, bob_app = await signed_in_app(core, bob, App(core.page_port))
        r = await bob_app.call("POST", "/api/computers/this",
                               {"key": token, "name": "annas-mac"})
        assert r.status == 410 and "token of a command" in r.json()["error"], \
            r.text
        # An expired command's token too.
        await core.pool.execute(
            "UPDATE install_tokens SET expires_at = now() - interval '1 hour'")
        r = await bob_app.call("POST", "/api/computers/this",
                               {"key": token, "name": "annas-mac"})
        assert r.status == 410, r.text
        assert await core.pool.fetchval(
            "SELECT count(*) FROM machines WHERE agent_token_hash = $1",
            machines.token_hash(token)) == 0


async def test_allowing_an_app_takes_a_sign_in_made_just_now(h):
    """Found by the review of 0.1.6: as for the quick code, whoever finds a
    browser open, or watches one, hands out no key that lasts."""
    async with h.core(owner_email="") as core:
        browser = Browser(core.page_port)
        await sign_in(core, browser, EMAIL)
        r = await browser.call("POST", "/api/auth/pin", {"pin": "2741"})
        assert r.status == 200, r.text
        await core.pool.execute(
            "UPDATE sessions SET created_at = now() - $1::interval",
            auth.APP_ALLOW_FRESH + dt.timedelta(minutes=1))
        app = App(core.page_port)
        r = await browser.call("POST", "/api/auth/app/allow", app.link())
        assert r.status == 403 and r.json()["confirm"] is True, r.text
        assert await core.pool.fetchval("SELECT count(*) FROM app_grants") == 0
        # The page ends this session only: the browser stays trusted.
        r = await browser.call("POST", "/api/auth/end-session", {})
        assert r.status == 200 and r.json() == {"ended": True}, r.text
        assert "tterm-session" not in browser.jar
        assert "tterm-device" in browser.jar
        assert (await browser.call("GET", "/api/state")).status == 401
        r = await browser.call("POST", "/api/auth/pin-login", {"pin": "2741"})
        assert r.status == 200, r.text
        r = await app.exchange(await allowed(core, browser, app))
        assert r.status == 200, "confirmed with the quick code, it allows"
        r = await browser.call("POST", "/api/auth/end-session", {})
        assert r.json() == {"ended": True}
        r = await browser.call("POST", "/api/auth/end-session", {})
        assert r.status == 200 and r.json() == {"ended": False}


async def test_a_code_not_traded_dies_with_its_session(h):
    """Found by the review of 0.1.6: Sign out and Forget all devices take
    back a code the app has not traded yet."""
    async with h.core(owner_email="") as core:
        browser = Browser(core.page_port)
        await sign_in(core, browser, EMAIL)
        app = App(core.page_port)
        code = await allowed(core, browser, app)
        await browser.call("POST", "/api/auth/signout-everywhere", {})
        r = await app.exchange(code)
        assert r.status == 410, r.text
        browser = await fresh_browser(core)
        code = await allowed(core, browser, app)
        await browser.call("POST", "/api/auth/signout", {})
        assert (await app.exchange(code)).status == 410
        browser = await fresh_browser(core)
        code = await allowed(core, browser, app)
        await browser.call("POST", "/api/auth/end-session", {})
        assert (await app.exchange(code)).status == 410
        assert await core.pool.fetchval(
            "SELECT count(*) FROM devices WHERE kind = 'app'") == 0


async def test_forget_all_devices_and_an_app_signing_in_take_turns(h):
    """Found by the second review of 0.1.6: an app's sign-in that comes
    while Forget all devices runs waits for it, and is then refused."""
    async with h.core(owner_email="") as core:
        browser = Browser(core.page_port)
        await sign_in(core, browser, EMAIL)
        app = App(core.page_port)
        code = await allowed(core, browser, app)
        anna = await core.pool.fetchval(
            "SELECT id FROM users WHERE email = $1", EMAIL)
        async with core.pool.acquire() as conn:
            # Forget all devices, half done: its lock and its two updates.
            tr = conn.transaction()
            await tr.start()
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                               f"sign-in:{anna}")
            await conn.execute("UPDATE devices SET forgotten_at = now() "
                               "WHERE user_id = $1 AND forgotten_at IS NULL", anna)
            await conn.execute("UPDATE sessions SET ended_at = now() "
                               "WHERE user_id = $1 AND ended_at IS NULL", anna)
            exchange = asyncio.create_task(app.exchange(code))
            await asyncio.sleep(0.5)
            assert not exchange.done(), "it waits"
            await tr.commit()
        r = await exchange
        assert r.status == 410, r.text
        assert await core.pool.fetchval(
            "SELECT count(*) FROM devices WHERE kind = 'app'") == 0


async def test_a_newline_is_no_part_of_a_link(h):
    """Found by the review of 0.1.6: "$" lets a newline at the end through."""
    for check in (auth.is_challenge, auth.is_verifier, auth.is_token):
        assert not check("a" * 43 + "\n"), check
    async with h.core(owner_email="") as core:
        browser = Browser(core.page_port)
        await sign_in(core, browser, EMAIL)
        app = App(core.page_port)
        for change in ({"challenge": app.challenge + "\n"},
                       {"state": app.state + "\n"}):
            r = await browser.call("POST", "/api/auth/app/allow",
                                   {**app.link(), **change})
            assert r.status == 422, (change, r.text)
        code = await allowed(core, browser, app)
        r = await app.exchange(code, verifier=app.verifier + "\n")
        assert r.status == 410, r.text
        assert (await app.exchange(code)).status == 200, \
            "refused before it was looked up: the code is still good"


async def test_this_computer_while_its_agent_joins(h):
    """Found by the review of 0.1.6: the person's own agent joins with the
    key while their app says which computer it runs on: the computer is
    theirs, not someone else's."""
    async with h.core(owner_email="") as core:
        browser, app = await signed_in_app(core)
        state = (await browser.call("GET", "/api/state")).json()
        personal = next(p for p in state["projects"] if p["personal"])
        key = machines.new_token("computer")
        # The join holds the project's lock; the app comes meanwhile.
        async with core.pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                               personal["id"])
            task = asyncio.create_task(app.call(
                "POST", "/api/computers/this",
                {"key": key, "name": "MacBook-Pro-Anna"}))
            await asyncio.sleep(0.5)
            joined = await conn.fetchval(
                "INSERT INTO machines (project_id, kind, name, agent_token_hash, "
                "created_by) VALUES ($1, 'computer', 'annas-mac', $2, $3) "
                "RETURNING id", uuid.UUID(personal["id"]), machines.token_hash(key),
                uuid.UUID(state["me"]["id"]))
            await tr.commit()
        r = await task
        assert r.status == 200 and r.json()["created"] is False, r.text
        assert r.json()["machine"]["id"] == str(joined)


# ------------------------------------------------------------ the rest

def test_the_email_about_an_app_names_it():
    mail = letters.sign_in_email(
        to=EMAIL, label="tTerm for Mac on MacBook-Pro-Anna", ip="203.0.113.7",
        at=dt.datetime(2026, 10, 23, 1, 30, tzinfo=dt.timezone.utc),
        page_url="https://app.tterm.net", app=True)
    assert mail.subject == "New sign-in to tTerm"
    assert ("tTerm for Mac on MacBook-Pro-Anna signed in as anna@example.com: "
            "a browser signed in to this account allowed it.") in mail.text
    assert "App: tTerm for Mac on MacBook-Pro-Anna" in mail.text
    assert "forget the app under Devices" in mail.text
    assert "Browser:" not in mail.text
    assert "&lt;" not in mail.html
    tricky = letters.sign_in_email(
        to=EMAIL, label="tTerm for Mac on <b>x</b>", ip="", page_url="https://x",
        at=dt.datetime(2026, 10, 23, tzinfo=dt.timezone.utc), app=True)
    assert "<b>x</b>" not in tricky.html and "&lt;b&gt;x&lt;/b&gt;" in tricky.html
    browser = letters.sign_in_email(
        to=EMAIL, label="Chrome on macOS", ip="", page_url="https://x",
        at=dt.datetime(2026, 10, 23, tzinfo=dt.timezone.utc))
    assert "Someone signed in as anna@example.com with a code" in browser.text
    assert "Browser: Chrome on macOS" in browser.text


async def test_the_sweep_takes_old_codes_of_apps(h):
    async with h.core(owner_email="") as core:
        browser = Browser(core.page_port)
        await sign_in(core, browser, EMAIL)
        await allowed(core, browser, App(core.page_port))
        await allowed(core, browser, App(core.page_port))
        await core.pool.execute(
            "UPDATE app_grants SET created_at = now() - interval '25 hours' "
            "WHERE id = (SELECT id FROM app_grants LIMIT 1)")
        await auth.sweep(core.pool)
        assert await core.pool.fetchval("SELECT count(*) FROM app_grants") == 1


async def test_010_keeps_the_devices_and_sessions_there_are(h):
    """010 on a database from before it: browsers stay browsers, with their
    quick codes, and their sessions stay on."""
    url = await h.empty_database()
    pool = await db.connect(url, max_size=2)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE schema_migrations (version text PRIMARY KEY, "
                "applied_at timestamptz NOT NULL DEFAULT now())")
            for path in sorted(db.MIGRATIONS.glob("*.sql")):
                if path.stem >= "010":
                    break
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (version) "
                                   "VALUES ($1)", path.stem)
            user = await conn.fetchval("INSERT INTO users (name) VALUES ('A') "
                                       "RETURNING id")
            device = await conn.fetchval(
                "INSERT INTO devices (user_id, key_hash, pin_hash, label) "
                "VALUES ($1, 'k', 'p', 'Chrome on macOS') RETURNING id", user)
            for method in ("code", "pin"):
                await conn.execute(
                    "INSERT INTO sessions (user_id, token_hash, device_id, "
                    "method) VALUES ($1, $2, $3, $4)", user,
                    method.encode(), device, method)
        # 010, and whatever came after it (from 0.1.7).
        assert (await db.migrate(pool))[0] == "010_app_sign_in"
        row = await pool.fetchrow("SELECT kind, pin_hash FROM devices")
        assert row["kind"] == "browser" and row["pin_hash"] == b"p"
        assert await pool.fetchval(
            "SELECT count(*) FROM sessions WHERE ended_at IS NULL") == 2
        await pool.execute(
            "INSERT INTO sessions (user_id, token_hash, method) "
            "VALUES ($1, 'a', 'app')", user)
    finally:
        await pool.close()


async def test_an_app_device_must_have_no_quick_code(h):
    async with h.pool() as pool:
        async with pool.acquire() as conn, conn.transaction():
            person = await auth._make_account(conn, EMAIL, "Anna")
        for kind, pin in (("app", b"x"), ("browser", None)):
            try:
                await pool.execute(
                    "INSERT INTO devices (user_id, kind, key_hash, pin_hash, "
                    "label) VALUES ($1, $2, $3, $4, 'x')", person.id, kind,
                    secrets.token_bytes(32), pin)
            except Exception as exc:
                assert "devices_pin_of_browsers" in str(exc), exc
            else:
                raise AssertionError(f"a {kind} with pin {pin!r} went in")
