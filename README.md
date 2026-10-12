# tTerm Web

tTerm in the browser: a real terminal with command blocks, for people who work
with servers and for their teams. Projects, roles, a journal of who ran what,
and watching a colleague's terminal, always with them knowing.

**Status:** early development. Version 0.0.4 opens terminals: a real shell
on your servers, in the browser, that keeps running when the page reloads,
with every command kept as a block. Next come the block feed and tTerm's own
input line. The first stage ends with version 0.1, a real terminal to your
own servers and computers in the browser on your laptop. Then come accounts
and a server, then teams, then a public launch.

Related:

- [tTerm](https://github.com/tterm-net/tTerm), the Telegram bot
- [tterm-agent](https://github.com/tterm-net/tterm-agent), the agent for
  computers without a public address
- [tterm.net](https://tterm.net)

## Run it on your computer

For development, while there is no sign-in: the page answers only on the
computer it runs on.

You need Python 3.12 or newer and PostgreSQL 16 or newer. The defaults expect
PostgreSQL on port 5433 with a database named `tterm_web`; set
`TTERM_DATABASE_URL` to use another one (see `.env.example`).

```bash
createdb -h localhost -p 5433 tterm_web

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# The page. Release archives bring it built; from git, build it once:
(cd web && npm ci && npm run build)

python -m tterm.dev
```

Then open http://localhost:8090. Stop it with Ctrl+C.

To connect a server, the server must reach your machines' address: set
`TTERM_CONNECT_URL` to an address that leads to port 8091 on this computer,
for instance through a Cloudflare tunnel. Then "Add a machine" gives the
command to run on the server, and "Open a terminal" opens a shell on it.

The checks need the same PostgreSQL. They create throwaway databases there
and drop them at the end:

```bash
python -m tterm.tests
```

## How it is put together

```
tterm/
  core/          settings, the database and its migrations, people, projects,
                 machines, SSH, the server passport, terminals and the shell
                 on the other end of them
  api/page.py    the page and its API, for people; only this computer
  api/ws.py      the page's socket: every terminal on the page goes through it
  api/connect.py where machines talk to us: installer, registration, agents
  signer/        the signer: CA keys and short certificates, its own process
  templates/     the installer for servers, and the shell scripts terminals
                 put on them
  server.py      runs both apps, on two ports
  dev.py         python -m tterm.dev: the signer, then the core
  tests/         python -m tterm.tests
web/             the page: React, TypeScript, Vite, xterm.js
```

**How a server connects.** "Add a machine" makes a one-time command that
works for ten minutes. On the server it creates the user `tterm` with no
password, adds our certificate authority to that user's `authorized_keys`
(never touching `sshd_config`), gives it sudo in `/etc/sudoers.d/tterm` and
reports the server's host keys back. tTerm Web then signs in once over SSH,
with those host keys pinned, and reads what the server is: system, shell,
package manager, the commands it has. If anything fails, the reason shows
in the page and in the installer's output, and the same command can be run
again.

**A terminal** is a real login shell in a real terminal on the server,
bash or zsh as the server has it, reached over SSH with a certificate of its
own. It lives in tTerm Web, not in the browser: closing the tab, reloading
the page or a dropped connection leave it running, and the page takes up
where it was. Only the shell's own end, or tTerm Web stopping, ends it.

The shell also prints invisible markers, the way iTerm2, VS Code and Warp
do: before each prompt (the exit code, the directory, the git branch) and
when a command starts (its text). From them each command becomes a block:
its text, where it ran, how long it took, how it ended and its output, kept
in the database for as long as the project says. The scripts behind the
markers live on the server in `~/.tterm` of the user `tterm`; tTerm Web
writes them each time it opens a terminal there and starts the shell with
them, after the usual startup files. Every marker carries a one-time key
that the shell reads as the very first line, with echo off, so nothing a
program prints can pass for one. The key never touches a file, the
environment or a command line.

**The signer** holds the CA keys and nothing else, in a process of its own.
For every connection the core makes a throwaway key pair and asks the
signer to certify the public half for two minutes, for the user `tterm`
only, with a key id that names the person: the server's own `auth.log` then
shows who signed in. The signer keeps its own journal of every certificate.

The page and the machines' side are separate apps on separate ports, as they
will be separate addresses on the server: `app.tterm.net` for people and
`connect.tterm.net` for machines. Nothing of the page is reachable through the
machines' address.

No sign-in yet, so the page guards itself: it answers only when the browser
asks for `localhost` or `127.0.0.1` on its own port, which defeats DNS
rebinding, and a request that changes something must come from the page
itself, its socket included. The page loads nothing from other sites, fonts
included, and its security policy allows no inline scripts or styles; the
few style elements xterm.js writes carry a one-time number each copy of the
page gets.

## Contributing

Not open yet. Before the first outside pull request we will add a contributor
license agreement, so the project can keep its licensing options open.

## License

Copyright (C) 2026 Denis Gerasimenko

tTerm Web is free software: you can redistribute it and/or modify it under the
terms of the GNU Affero General Public License, version 3, as published by the
Free Software Foundation. See [LICENSE](LICENSE).

Using tTerm Web with your own machines places no obligations on you. The
license matters when you distribute a modified version or run one as a service
for others: then you must offer its source to the people who use it.

The page bundles the fonts Manrope and JetBrains Mono, both under the SIL Open
Font License 1.1, and xterm.js with its add-ons, under the MIT License.
