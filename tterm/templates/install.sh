#!/bin/sh
# tTerm Web: connecting this server.
#
# What this script does:
#   1. creates the user {{SSH_USER}}, with no password. If it already exists,
#      for instance from the tTerm bot, it is reused as it is;
#   2. adds one line to that user's authorized_keys: tTerm Web's certificate
#      authority, {{CA_ID}}. tTerm Web signs in with certificates that live
#      two minutes, so no keys stay on this server;
#   3. gives {{SSH_USER}} sudo without a password, in /etc/sudoers.d/{{SSH_USER}},
#      so that you can manage the server from tTerm Web;
#   4. tells tTerm Web the server is ready. tTerm Web then signs in once
#      over SSH, to check that it works and to see what the server is.
#
# What it does not do: it never edits /etc/ssh/sshd_config, never restarts
# sshd and opens no ports. Running it twice is safe.
#
# To take tTerm Web's access back: delete the line ending in "{{CA_ID}}"
# from ~{{SSH_USER}}/.ssh/authorized_keys. To remove everything, the bot's
# access included if you use the bot:
#   sudo userdel -r {{SSH_USER}} && sudo rm -f /etc/sudoers.d/{{SSH_USER}}

set -eu

SSH_USER="{{SSH_USER}}"
CA_KEY="{{CA_KEY}}"
CA_ID="{{CA_ID}}"
API_URL="{{API_URL}}"
TOKEN="{{TOKEN}}"

say() { printf '  %s\n' "$1"; }
die() { printf '\n  x %s\n\n' "$1" >&2; exit 1; }
# A value made safe to put between double quotes in JSON.
json_str() { printf '%s' "$1" | tr -d '\000-\037' | sed 's/\\/\\\\/g; s/"/\\"/g'; }

printf '\n  tTerm Web: connecting this server\n\n'

# ------------------------------------------------------------------ checks

[ "$(id -u)" -eq 0 ] || die "Root rights are needed: run the command with sudo."
command -v sshd >/dev/null 2>&1 || [ -x /usr/sbin/sshd ] || \
    die "The OpenSSH server is not installed. Install openssh-server and run the command again."
command -v curl >/dev/null 2>&1 || die "curl is needed to talk to tTerm Web."

if command -v apt-get >/dev/null 2>&1; then DISTRO=debian
elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then DISTRO=rhel
elif command -v apk >/dev/null 2>&1; then DISTRO=alpine
else die "This Linux is not one we know yet. Tell us which it is, and we will add it."
fi
say "System: $DISTRO"

# -------------------------------------------------------------------- user

if id "$SSH_USER" >/dev/null 2>&1; then
    say "User $SSH_USER exists, reusing it"
else
    case "$DISTRO" in
        alpine) adduser -D -s /bin/bash "$SSH_USER" >/dev/null ;;
        *)      useradd -m -s /bin/bash "$SSH_USER" >/dev/null ;;
    esac
    say "Created user $SSH_USER"
fi
# No password, ever: certificates only. "*" matches no password, yet unlike
# a locked account ("!") it does not stop key sign-in where sshd runs
# without PAM, as on Alpine.
case "$DISTRO" in
    alpine) printf '%s:*\n' "$SSH_USER" | chpasswd -e >/dev/null 2>&1 || true ;;
    *)      usermod -p '*' "$SSH_USER" >/dev/null 2>&1 || true ;;
esac

HOME_DIR=$(getent passwd "$SSH_USER" | cut -d: -f6)
[ -n "$HOME_DIR" ] || die "Could not find the home directory of $SSH_USER."

# ----------------------------------------------------------- trust the CA

# cert-authority in authorized_keys rather than TrustedUserCAKeys in
# sshd_config: the same trust in our certificates, but local to this user,
# and sshd needs no restart, so nobody can be locked out.
mkdir -p "$HOME_DIR/.ssh"
AUTH_KEYS="$HOME_DIR/.ssh/authorized_keys"
touch "$AUTH_KEYS"
if grep -qF "$CA_KEY" "$AUTH_KEYS" 2>/dev/null; then
    say "tTerm Web's CA is already trusted"
else
    printf 'cert-authority %s tterm-web %s\n' "$CA_KEY" "$CA_ID" >> "$AUTH_KEYS"
    say "tTerm Web's CA $CA_ID added"
fi
chown -R "$SSH_USER:$(id -gn "$SSH_USER")" "$HOME_DIR/.ssh"
chmod 700 "$HOME_DIR/.ssh"
chmod 600 "$AUTH_KEYS"

# -------------------------------------------------------------------- sudo

# A file of its own in sudoers.d: the change is visible, one rm takes it
# back, and nothing the admin set up is touched. Checked with visudo first:
# a broken file there breaks sudo for everyone, the admin included.
command -v visudo >/dev/null 2>&1 || \
    die "sudo is not installed. Install it (apt-get install sudo) and run the command again."
SUDOERS_FILE="/etc/sudoers.d/$SSH_USER"
TMP_SUDOERS=$(mktemp)
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$SSH_USER" > "$TMP_SUDOERS"
if visudo -c -f "$TMP_SUDOERS" >/dev/null 2>&1; then
    install -m 0440 -o root -g root "$TMP_SUDOERS" "$SUDOERS_FILE"
    rm -f "$TMP_SUDOERS"
    say "sudo granted to $SSH_USER (to take it back: rm $SUDOERS_FILE)"
else
    rm -f "$TMP_SUDOERS"
    die "Could not check the sudo rule, so nothing was changed in sudo."
fi

# -------------------------------------------------------------- register

# The port sshd really listens on, includes and all; the config file if
# sshd cannot say; 22 otherwise.
SSH_PORT=$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2; exit}' || true)
[ -n "${SSH_PORT:-}" ] || SSH_PORT=$(awk '/^[[:space:]]*Port[[:space:]]+[0-9]+/ {print $2; exit}' /etc/ssh/sshd_config 2>/dev/null || true)
[ -n "${SSH_PORT:-}" ] || SSH_PORT=22

HOST_KEYS=""
for f in /etc/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ecdsa_key.pub /etc/ssh/ssh_host_rsa_key.pub; do
    [ -r "$f" ] || continue
    KEY=$(cut -d' ' -f1-2 "$f")
    HOST_KEYS="${HOST_KEYS:+$HOST_KEYS,}\"$(json_str "$KEY")\""
done
[ -n "$HOST_KEYS" ] || die "No SSH host keys in /etc/ssh: is the OpenSSH server set up?"

HOSTNAME_VAL=$(hostname 2>/dev/null || cat /etc/hostname 2>/dev/null || echo server)
OS_INFO=$( (. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-}") || true)
[ -n "$OS_INFO" ] || OS_INFO=$(uname -s)

PAYLOAD=$(printf '{"token":"%s","hostname":"%s","os":"%s","ssh_port":%s,"ssh_user":"%s","host_keys":[%s]}' \
    "$TOKEN" "$(json_str "$HOSTNAME_VAL")" "$(json_str "$OS_INFO")" \
    "$SSH_PORT" "$SSH_USER" "$HOST_KEYS")

say "Telling tTerm Web the server is ready; it signs in once to check..."

RESP=$(mktemp)
# Over IPv4 first: tTerm Web connects back to the address this comes from,
# and an IPv6 address it may not reach.
CODE=$(curl -4 -sS -o "$RESP" -w '%{http_code}' -X POST "$API_URL/enroll" \
    -H 'Content-Type: application/json' --max-time 60 -d "$PAYLOAD" 2>/dev/null) || CODE=000
if [ "$CODE" = "000" ]; then
    CODE=$(curl -sS -o "$RESP" -w '%{http_code}' -X POST "$API_URL/enroll" \
        -H 'Content-Type: application/json' --max-time 60 -d "$PAYLOAD" 2>/dev/null) || CODE=000
fi
MESSAGE=$(cat "$RESP" 2>/dev/null || true)
rm -f "$RESP"

if [ "$CODE" = "200" ]; then
    printf '\n  %s\n\n' "$MESSAGE"
else
    [ -n "$MESSAGE" ] || MESSAGE="Could not reach tTerm Web at $API_URL (code $CODE)."
    printf '\n  x %s\n\n' "$MESSAGE" >&2
    exit 1
fi
