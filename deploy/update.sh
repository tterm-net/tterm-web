#!/bin/sh
# tTerm Web on this server, brought up to what is in git. As root:
#
#   sh /opt/tterm-web/deploy/update.sh
#
# Terminals end, as the core restarts; agents connect again by themselves.
# Read as a whole before it runs (main at the end): git pull may change
# this very file.
set -eu

SRC=/opt/tterm-web

as_web() { runuser -u tterm-web -- env HOME=/var/lib/tterm-web PIP_NO_CACHE_DIR=1 "$@"; }

main() {
    [ "$(id -u)" = 0 ] || { echo "Run it as root."; exit 1; }
    as_web git -C "$SRC" pull -q
    as_web "$SRC/venv/bin/pip" install -q -r "$SRC/requirements.txt"
    sh "$SRC/deploy/logs.sh"
    install -m 644 "$SRC/deploy/tterm-signer.service" "$SRC/deploy/tterm-web.service" \
        "$SRC/deploy/tterm-web-backup.service" "$SRC/deploy/tterm-web-backup.timer" \
        /etc/systemd/system/
    systemctl daemon-reload
    # nginx from the same names the core has.
    APP=$(sed -n 's|^TTERM_PAGE_URL=https://||p' /etc/tterm-web/core.env)
    CONNECT=$(sed -n 's|^TTERM_CONNECT_URL=https://||p' /etc/tterm-web/core.env)
    sed -e "s/APP_NAME/$APP/g" -e "s/CONNECT_NAME/$CONNECT/g" \
        "$SRC/deploy/nginx.conf" > /etc/nginx/sites-available/tterm-web
    nginx -t -q
    systemctl reload nginx
    systemctl restart tterm-signer tterm-web
    sleep 3
    systemctl is-active tterm-signer tterm-web
    grep __version__ "$SRC/tterm/__init__.py"
}

main
