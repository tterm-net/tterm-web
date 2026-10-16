#!/bin/sh
# How long this server keeps tTerm Web's logs: 30 days, as the Privacy
# Policy says. setup.sh and update.sh run it, as root; again, it changes
# nothing.
#
# The journal keeps 30 days, in files of a day each: a file goes when all
# of it is older, so a day more at most. rsyslog, which Ubuntu runs too,
# gets nothing of tTerm Web's: its copy in /var/log/syslog would stay five
# weeks.
set -eu

[ "$(id -u)" = 0 ] || { echo "Run it as root." >&2; exit 1; }

# Writes a file only when it would change; true when it did.
put() {
    if [ -f "$1" ] && [ "$(cat "$1")" = "$2" ]; then
        return 1
    fi
    printf '%s\n' "$2" > "$1"
}

install -d -m 755 /etc/systemd/journald.conf.d
if put /etc/systemd/journald.conf.d/tterm-web.conf "[Journal]
MaxRetentionSec=30day
MaxFileSec=1day"; then
    systemctl restart systemd-journald
fi

if [ -d /etc/rsyslog.d ]; then
    if put /etc/rsyslog.d/20-tterm-web.conf "# tTerm Web's logs stay in the journal alone, which keeps them 30 days.
if \$programname == 'tterm-web' or \$programname == 'tterm-signer' then stop"; then
        systemctl try-restart rsyslog 2>/dev/null || true
    fi
fi
