"""The server passport: what a machine is, learned over SSH right after it
connects. The page shows it; later the input line uses the list of commands
to tell a command from a question, and to complete words.
"""
from __future__ import annotations

#: Plain POSIX sh, run as the tterm user without a terminal. Every line is
#: key=value; after the marker, one command name per line.
SCRIPT = r"""
LC_ALL=C; export LC_ALL
os=""; os_id=""; os_version=""
if [ -r /etc/os-release ]; then
  os=$(. /etc/os-release; printf '%s' "${PRETTY_NAME:-}")
  os_id=$(. /etc/os-release; printf '%s' "${ID:-}")
  os_version=$(. /etc/os-release; printf '%s' "${VERSION_ID:-}")
fi
[ -n "$os" ] || os=$(uname -s)
printf 'os=%s\n' "$os"
printf 'os_id=%s\n' "$os_id"
printf 'os_version=%s\n' "$os_version"
printf 'kernel=%s\n' "$(uname -r)"
printf 'arch=%s\n' "$(uname -m)"
printf 'hostname=%s\n' "$(hostname 2>/dev/null)"
for pm in apt-get dnf yum apk pacman zypper; do
  if command -v "$pm" >/dev/null 2>&1; then printf 'package_manager=%s\n' "$pm"; break; fi
done
printf 'shell=%s\n' "$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f7)"
for s in bash zsh fish; do
  command -v "$s" >/dev/null 2>&1 && printf 'shells=%s\n' "$s"
done
printf 'cpus=%s\n' "$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null)"
printf 'memory_kb=%s\n' "$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null)"
df -Pk / 2>/dev/null | awk 'NR==2 {print "disk_total_kb=" $2; print "disk_free_kb=" $4}'
if sudo -n true 2>/dev/null; then echo sudo=yes; else echo sudo=no; fi
if [ -d /run/systemd/system ]; then echo systemd=yes; else echo systemd=no; fi
echo '--commands--'
if command -v bash >/dev/null 2>&1; then
  bash -c 'compgen -c' 2>/dev/null | LC_ALL=C sort -u | head -n 5000
fi
"""

MARKER = "--commands--"

_INTS = ("cpus", "memory_kb", "disk_total_kb", "disk_free_kb")
_FLAGS = ("sudo", "systemd")
_TEXT = ("os", "os_id", "os_version", "kernel", "arch", "hostname",
         "package_manager", "shell")


def parse(output: str) -> dict:
    """The passport from the script's output. Unknown lines are ignored;
    whatever is missing is simply absent."""
    head, _, tail = output.partition(f"\n{MARKER}\n")
    if not tail and output.startswith(MARKER):
        head, tail = "", output[len(MARKER) + 1:]
    passport: dict = {"shells": []}
    for line in head.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if key in _TEXT and value:
            passport[key] = value[:200]
        elif key in _INTS:
            if value.isdigit():
                passport[key] = int(value)
        elif key in _FLAGS:
            passport[key] = value == "yes"
        elif key == "shells" and value and value not in passport["shells"]:
            passport["shells"].append(value)
    passport["commands"] = sorted({
        c.strip() for c in tail.splitlines()
        if c.strip() and len(c) <= 100 and c.isprintable()})[:5000]
    return passport


def summary(passport: dict) -> dict:
    """What the page shows: everything but the long list of commands."""
    out = {k: v for k, v in passport.items() if k != "commands"}
    out["commands_count"] = len(passport.get("commands", []))
    return out
