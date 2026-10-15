# tTerm Web

tTerm in the browser: a real terminal with command blocks, for people who work
with servers and for their teams. Projects, roles, a journal of who ran what,
and watching a colleague's terminal, always with them knowing.

**Status:** early development. Version 0.0.8 adds a computer with one
command that installs [tterm-agent](https://github.com/tterm-net/tterm-agent)
and starts it whenever you log in, removes machines and ends terminals, and
remembers folded blocks and a cleared feed. Since 0.0.7 the page is the one
of the mockups: tabs of open terminals from any project, "+" for a terminal
on any machine, a palette on Cmd+K, and a computer's terminals live through
its sleep, waiting while it is away and going on where they were. Since 0.0.6 a
laptop without a public address gets the same terminals as a server, and
since 0.0.5 a terminal is a feed of blocks, one per command, typed into
tTerm's own input line: history from all your devices, Tab that asks the
machine, the directory and the git branch as buttons, and a question before
a command that would destroy something. The classic terminal is one click
away. The first stage ends with version 0.1, a real terminal to your own
servers and computers in the browser on your laptop. Then come accounts and
a server, then teams, then a public launch.

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
command to run on the server, and "Open a terminal" opens a shell on it. A
computer gets a command that installs the agent instead. To try an agent
that is not released yet, the same dialog has the command that starts it by
hand from a checkout of
[tterm-agent](https://github.com/tterm-net/tterm-agent), or point the
installer at it: `export TTERM_SRC=file://$HOME/tterm-agent/agent.py` before
running the command.

The checks need the same PostgreSQL. They create throwaway databases there
and drop them at the end. With tterm-agent checked out next to this
repository, they run the real agent too:

```bash
python -m tterm.tests
```

## How it is put together

```
tterm/
  core/          settings, the database and its migrations, people, projects,
                 machines, SSH, the server passport, terminals and the shell
                 on the other end of them, Tab, the commands held back
  api/page.py    the page and its API, for people; only this computer
  api/ws.py      the page's socket: every terminal on the page goes through it
  api/connect.py where machines talk to us: installer, registration, and
                 the agents of computers, which stay connected
  signer/        the signer: CA keys and short certificates, its own process
  templates/     the installers for servers and computers, and the shell
                 scripts terminals put on them
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

**The feed.** The page shows those blocks as a feed, as Warp does. The
command running now is drawn by xterm.js, so colours, progress bars and
password prompts work as in any terminal, and keys go straight to it. When
it ends, its screen is read back into text you can select, search and copy,
and a full-screen program such as vim or htop gets the whole window while it
runs. Finished blocks come from the database, so a reload shows the feed at
once and a running command is drawn again from what tTerm Web kept of it.

**The input line** is the page's own, not the shell's: several lines, the
mouse, history from every device you used, a grey suggestion from it. Enter
hands the whole command to the shell at its prompt as a bracketed paste, so
nothing in it is read as a key binding. Tab, and the lists behind the
directory and branch buttons, come from the server itself over a quiet side
channel of the same SSH connection: a small sh script that never touches the
terminal, its screen or its history. A handful of commands that destroy
something for good (a recursive delete, a wiped disk, a dropped database, a
shutdown) wait for a yes first. The classic mode is the terminal of 0.0.4:
every key goes to the shell, with its own Tab and history, and nothing is
checked. bash older than 4.4 cannot say where a command starts, so there
only the classic mode works.

**A computer** has no address to reach, so its agent reaches us: it opens a
WebSocket to the machines' side and keeps it, and every terminal on that
computer goes through it. "Add a machine", then "Computer", gives a command
to run there as yourself: `curl -fsSL …/c/TOKEN | sh`. It puts the agent in
`~/.tterm-web` with a Python environment of its own, makes the computer a
key of its own (in `~/.tterm-web/env`, readable by you only; tTerm Web keeps
only its hash), and starts the agent whenever you log in: launchd on macOS,
systemd on Linux, or in the background until the next restart where there
is neither. The token in the command lets the computer join once within ten
minutes. The same command run there again, with a new token, updates the
agent and keeps the key, so the computer stays the machine it was; its open
terminals end, as the agent restarts. `~/.tterm-web/uninstall.sh` removes
it all; the tTerm bot's agent in `~/.tterm` is never touched. Through that connection the
agent starts the shell a terminal asks for, in a terminal of the page's size,
and passes the bytes both ways untouched; it also runs the small sh scripts
behind Tab and the lists, and the one that puts the shell scripts in
`~/.tterm`. A computer is online while its agent runs, and the page shows
it, or when it was last seen. The agent is still a pipe: markers, blocks
and the rest stay here, as for a server. A computer installed afresh (its
`~/.tterm-web` gone) is a new machine, even under the same name: a name is
whatever a computer calls itself. A second agent with a key already
connected is turned away while the first one answers.

**A computer that sleeps.** When its agent's connection goes (the lid is
closed, the network blinks), the agent keeps the shells running and the
last megabyte each of them printed, and tTerm Web keeps the terminals,
away: they say they wait for the computer and take no keys. When the agent
is back, each terminal is taken back from the byte tTerm Web has of it:
nothing lost, nothing twice, and a full-screen program goes on as it was.
Terminals wait up to 12 hours; the agent keeps its shells for an hour of the
computer being awake without the link, and time asleep does not count. A
terminal the agent no longer has (it was restarted) ends and says why; one
stopped with Ctrl+C says goodbye, and its terminals end at once.

**Removing.** The owner and admins of a project can remove a machine, from
its card or from the sidebar, after a question. Its terminals end and say
why, and it leaves the lists; the journal keeps what happened. A computer's
agent is told, forgets its key and stops; that key never works again, so a
key that leaked is dead once its computer is removed. A server
stops being signed in to, and the page gives the command that takes tTerm
Web's certificate authority out of its `authorized_keys`, unless the same
server is in another project too. Ending a terminal
from the sidebar or the palette ends its shell, after a question too.

The machines' side is open to the internet, so `/agent` keeps strangers
short: the hello is small and comes at once, only a few connections from
one address may wait to say it, an address whose unknown tokens keep failing
waits a while, and no message is larger than a script's answer.

**The page** keeps tabs of the terminals you work with, from any project,
in this browser. Every tab's terminal is on the page at once, the others
hidden behind the one in front, so switching is instant and none of them is
told of a new size. Closing a tab leaves its terminal running, in the
sidebar under its machine, where a click brings the tab back. "+" opens a
terminal on any machine, and Cmd+K on a Mac, Ctrl+Shift+K elsewhere, jumps
to any terminal or machine or does what the page can; plain Ctrl stays the
terminal's. Folded blocks, and a feed cleared with `clear` or Ctrl+L, stay
so after Classic and back or a reload, in this browser. A tab's dot is its machine: green on line, a turning ring while
a command runs, a hollow ring while the computer is away. When the page
loses tTerm Web, a strip says so until it is back.

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
