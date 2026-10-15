#!/bin/sh
# tTerm Web: connecting this computer (macOS or Linux).
#
# What this does:
#   1. puts tTerm's agent in ~/.tterm-web, with a Python environment of its
#      own, and makes this computer a key of its own there, readable by you
#      only. The key goes nowhere but to the tTerm Web this command is from;
#      the token in the command only lets the computer join, once;
#   2. sets up autostart: launchd on macOS, systemd --user on Linux, so that
#      the agent starts when you log in;
#   3. starts the agent. It connects out to tTerm Web, and the computer
#      appears there in a few seconds.
#
# What it does not do:
#   - never asks for root, and refuses to run as root;
#   - opens no port: the connection always goes out from here;
#   - touches nothing outside ~/.tterm-web and its autostart entry. The tTerm
#     bot's agent in ~/.tterm, if you have one, stays as it is.
#
# Running it again updates the agent and keeps this computer as it is in
# tTerm Web; the computer's open terminals end, as the agent restarts.
# Remove everything: ~/.tterm-web/uninstall.sh
#
# The agent's source: https://github.com/tterm-net/tterm-agent

set -eu

HUB="${TTERM_HUB:-{{HUB}}}"
TOKEN="{{TOKEN}}"
SRC="${TTERM_SRC:-{{SRC}}}"
DIR="${HOME%/}/.tterm-web"
LABEL="net.tterm.web-agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UNIT="$HOME/.config/systemd/user/tterm-web-agent.service"

say() { printf '  %s\n' "$1"; }
die() { printf '\n  x %s\n\n' "$1" >&2; exit 1; }

printf '\n  tTerm Web: connecting this computer\n\n'

# ------------------------------------------------------------------ checks

[ "$(id -u)" -ne 0 ] || die "Do not run this as root or with sudo. The agent
    works with your own permissions, so it can do no more than you can."

# A terminal of tTerm Web on this computer runs through the agent this
# updates: stopping it would end the terminal, and this script with it.
if [ -n "${TTERM_AGENT_DIR:-}" ] && [ "$TTERM_AGENT_DIR/agent.py" -ef "$DIR/agent.py" ]; then
    die "This terminal runs through the agent this command updates, so it
    would end halfway. Run the command in a terminal on the computer itself
    (Terminal on a Mac)."
fi

case "$(uname -s)" in
    Darwin) PLATFORM=macos ;;
    Linux)  PLATFORM=linux ;;
    *)      die "Only macOS and Linux are supported. On Windows, WSL2 works
    as a Linux computer." ;;
esac
say "System: $PLATFORM"

command -v curl >/dev/null 2>&1 || die "curl is needed to fetch the agent."

PY=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version=$("$candidate" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null </dev/null || echo 0)
        if [ "$version" -ge 311 ]; then PY=$(command -v "$candidate"); break; fi
    fi
done
[ -n "$PY" ] || die "Python 3.11 or newer is needed.
    macOS:  brew install python@3.12, or the installer from python.org
    Linux:  sudo apt install python3 python3-venv"
say "Python: $("$PY" --version 2>&1)"

# ---------------------------------------------------------------- download

# What can fail on the network comes first, while the agent that runs now
# goes on: an update that fails leaves it as it was.
mkdir -p "$DIR"
chmod 700 "$DIR"

say "Downloading the agent..."
curl -fsSL "$SRC" -o "$DIR/agent.py.new" </dev/null || die "Could not download the agent from $SRC"
AGENT_VERSION=$(sed -n 's/^__version__ = "\([0-9][0-9.]*\)"$/\1/p' "$DIR/agent.py.new" | head -n 1)
[ -n "$AGENT_VERSION" ] || { rm -f "$DIR/agent.py.new"; die "What came from $SRC is not the agent."; }
MAJOR=${AGENT_VERSION%%.*}
REST=${AGENT_VERSION#*.}
MINOR=${REST%%.*}
if [ "$MAJOR" -eq 0 ] && [ "$MINOR" -lt 13 ]; then
    rm -f "$DIR/agent.py.new"
    die "The agent at $SRC is $AGENT_VERSION; this installer needs 0.13.0 or newer."
fi

# Kept between runs, unless the Python it was made with is gone.
if ! "$DIR/venv/bin/python" -c 'import sys' >/dev/null 2>&1 </dev/null; then
    rm -rf "$DIR/venv"
    "$PY" -m venv "$DIR/venv" </dev/null || die "Could not make the Python environment.
    On Debian and Ubuntu: sudo apt install python3-venv"
fi
say "Installing what the agent needs..."
"$DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
    --upgrade websockets </dev/null || die "Could not install websockets"
# Optional: with it the agent shows as tterm-agent in the system's lists,
# without it as python.
"$DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
    setproctitle </dev/null 2>/dev/null || true

# ----------------------------------------------------------------- install

# Run again for the same tTerm Web, the computer keeps its key and stays the
# one it was. For another tTerm Web, or once the computer was removed (its
# agent then forgot the key), it gets a new one.
KEY=""
if [ -f "$DIR/env" ]; then
    say "The agent is here already: updating it"
    OLD_HUB=$(sed -n 's/^TTERM_HUB=//p' "$DIR/env" 2>/dev/null | head -n 1)
    if [ "$OLD_HUB" = "$HUB" ]; then
        KEY=$(sed -n 's/^TTERM_KEY=//p' "$DIR/env" 2>/dev/null | head -n 1)
        case "$KEY" in
            *[!A-Za-z0-9_-]*) KEY="" ;;
        esac
        [ "${#KEY}" -ge 32 ] || KEY=""
    fi
fi

# The agent that runs now stops here, so that two of them never fight over
# this computer.
if [ "$PLATFORM" = macos ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
else
    systemctl --user stop tterm-web-agent.service 2>/dev/null || true
fi
if [ -f "$DIR/agent.pid" ]; then
    kill "$(cat "$DIR/agent.pid")" 2>/dev/null || true
    rm -f "$DIR/agent.pid"
fi
mv "$DIR/agent.py.new" "$DIR/agent.py"
say "Agent: $AGENT_VERSION"

# A name of your choosing for a new computer, if TTERM_NAME says one;
# otherwise the agent takes the one in the system's settings.
NAME=$(printf '%s' "${TTERM_NAME:-}" | tr -cd 'A-Za-z0-9._-')

if [ -z "$KEY" ]; then
    KEY=$("$DIR/venv/bin/python" -c 'import secrets
k = secrets.token_urlsafe(24)
print("A" + k[1:] if k.startswith("-") else k)' </dev/null)
fi

# The settings, the key among them: for this user only, and not in the
# autostart entry, which other programs may read.
(
    umask 077
    {
        printf 'TTERM_HUB=%s\n' "$HUB"
        printf 'TTERM_TOKEN=%s\n' "$TOKEN"
        printf 'TTERM_KEY=%s\n' "$KEY"
        if [ -n "$NAME" ]; then printf 'TTERM_NAME=%s\n' "$NAME"; fi
    } > "$DIR/env.new"
)
mv "$DIR/env.new" "$DIR/env"
chmod 600 "$DIR/env"
say "Settings and this computer's key: $DIR/env"

# What this run's agent says, apart from the runs before.
if [ -f "$DIR/agent.log" ]; then mv "$DIR/agent.log" "$DIR/agent.log.1"; fi

# --------------------------------------------------------------- autostart

NOTE=""
if [ "$PLATFORM" = macos ]; then
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/venv/bin/python</string>
    <string>$DIR/agent.py</string>
    <string>--env-file</string>
    <string>$DIR/env</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DIR/agent.log</string>
  <key>StandardErrorPath</key><string>$DIR/agent.log</string>
</dict></plist>
EOF
    launchctl load "$PLIST" || die "Could not set up autostart"
    UNSTART="rm -f \"$PLIST\""
    STOP="launchctl remove $LABEL 2>/dev/null || true"
    say "Autostart: launchd ($LABEL)"
elif command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    mkdir -p "$(dirname "$UNIT")"
    cat > "$UNIT" <<EOF
[Unit]
Description=tTerm Web agent
After=network-online.target

[Service]
Type=simple
ExecStart=$DIR/venv/bin/python $DIR/agent.py --env-file $DIR/env
Restart=on-failure
RestartSec=5
StandardOutput=append:$DIR/agent.log
StandardError=append:$DIR/agent.log

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now tterm-web-agent.service >/dev/null 2>&1 \
        || die "Could not start the service"
    UNSTART="systemctl --user disable tterm-web-agent.service 2>/dev/null || true
rm -f \"$UNIT\"
systemctl --user daemon-reload 2>/dev/null || true"
    STOP="systemctl --user stop tterm-web-agent.service 2>/dev/null || true"
    say "Autostart: systemd --user (tterm-web-agent)"
    # Without lingering, user services stop at logout and wait for a login.
    if command -v loginctl >/dev/null 2>&1 && ! loginctl show-user "$(id -un)" \
            --property=Linger 2>/dev/null | grep -q '=yes'; then
        NOTE="It runs while you are logged in. For a computer you reach only over
    SSH, to have it run without a login too: sudo loginctl enable-linger $(id -un)"
    fi
else
    # No user services here (WSL without systemd, a container): the agent
    # starts now, in the background, and again with ~/.tterm-web/start.sh.
    cat > "$DIR/start.sh" <<EOF
#!/bin/sh
nohup "$DIR/venv/bin/python" "$DIR/agent.py" --env-file "$DIR/env" \\
    >> "$DIR/agent.log" 2>&1 </dev/null &
echo \$! > "$DIR/agent.pid"
EOF
    chmod 700 "$DIR/start.sh"
    "$DIR/start.sh"
    UNSTART=":"
    STOP="[ -z \"\$PID\" ] || kill \"\$PID\" 2>/dev/null || true"
    NOTE="No autostart on this system: after a restart, run ~/.tterm-web/start.sh"
    say "Autostart: none here, see below"
fi

# --------------------------------------------------------------- uninstall

cat > "$DIR/uninstall.sh" <<EOF
#!/bin/sh
# Removes tTerm Web's agent from this computer: autostart, the agent and
# this computer's key. In tTerm Web the computer stays listed until you
# remove it there: its card, Remove. The agent stops last, so that run from
# one of its own terminals this still does everything.
PID=\$(cat "$DIR/agent.pid" 2>/dev/null)
$UNSTART
rm -rf "$DIR"
# The shell scripts tTerm Web's terminals put in ~/.tterm. The folder itself
# goes only if nothing else is in it: the tTerm bot's agent lives there.
rm -f "$HOME/.tterm/bash.sh"
rm -rf "$HOME/.tterm/zsh"
rmdir "$HOME/.tterm" 2>/dev/null || true
echo "tTerm Web's agent is removed from this computer."
$STOP
EOF
chmod 700 "$DIR/uninstall.sh"

# -------------------------------------------------------------- connected?

say "Starting the agent..."
i=0
RESULT=""
while [ $i -lt 30 ]; do
    if grep -q 'connected as' "$DIR/agent.log" 2>/dev/null; then
        RESULT=connected
        break
    fi
    if grep -q 'access revoked' "$DIR/agent.log" 2>/dev/null; then
        RESULT=refused
        break
    fi
    sleep 1
    i=$((i + 1))
done

case "$RESULT" in
    connected)
        printf '\n  Done: the computer is connected. Go back to tTerm Web.\n'
        ;;
    refused)
        printf '\n  x tTerm Web did not take the agent:\n' >&2
        grep 'access revoked' "$DIR/agent.log" | tail -n 1 | sed 's/^/    /' >&2
        printf '\n' >&2
        exit 1
        ;;
    *)
        printf '\n  The agent is installed but has not connected yet. What it says:\n'
        tail -n 3 "$DIR/agent.log" 2>/dev/null | sed 's/^/    /'
        ;;
esac
[ -z "$NOTE" ] || printf '\n  %s\n' "$NOTE"
printf '\n    Log:    %s/agent.log\n' "$DIR"
printf '    Remove: %s/uninstall.sh\n\n' "$DIR"
