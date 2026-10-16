#!/bin/sh
# Brevo's API key into the core's settings, without it showing on the
# screen or staying in the shell's history. As root, in a terminal of your
# own (SSH), never through a chat:
#
#   sh /opt/tterm-web/deploy/brevo-key.sh
set -eu

ENV=/etc/tterm-web/core.env

main() {
    [ "$(id -u)" = 0 ] || { echo "Run it as root."; exit 1; }
    [ -f "$ENV" ] || { echo "No $ENV: run setup.sh first."; exit 1; }
    printf "Brevo API key (paste it; it does not show): "
    trap 'stty echo' EXIT INT TERM
    stty -echo
    read -r KEY
    stty echo
    echo
    case "$KEY" in
        xkeysib-*) ;;
        *) echo "That is not a Brevo API key: those begin with xkeysib-."; exit 1 ;;
    esac
    tmp=$(mktemp "$ENV.XXXXXX")
    grep -v '^TTERM_BREVO_KEY=' "$ENV" > "$tmp" || true
    printf 'TTERM_BREVO_KEY=%s\n' "$KEY" >> "$tmp"
    chown root:tterm-web "$tmp"
    chmod 640 "$tmp"
    mv "$tmp" "$ENV"
    systemctl restart tterm-web
    sleep 3
    systemctl is-active tterm-web
    echo "Done: the codes for signing in go out by email now."
}

main
