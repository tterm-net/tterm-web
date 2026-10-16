#!/bin/sh
# tTerm Web on a fresh Ubuntu 24.04 server. As root:
#
#   sh setup.sh app.example.com connect.example.com you@example.com
#
# The page's name, the machines' name, and the address Let's Encrypt
# writes to about the certificate. Both names must already lead to this
# server, straight (in Cloudflare: DNS only, without the proxy). Running it
# again is safe: what is there stays, the code is brought up to date.
# What it sets up, and why: deploy/README.md. Read as a whole before it
# runs (main at the end), so a newer copy pulled meanwhile cannot mix in.
set -eu

APP=${1:-}
CONNECT=${2:-}
EMAIL=${3:-}
REPO=${TTERM_REPO:-https://github.com/tterm-net/tterm-web.git}
SRC=/opt/tterm-web
ETC=/etc/tterm-web

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\n%s\n' "$*" >&2; exit 1; }
# As the core's user, with nothing of root's environment that matters.
as_web() { runuser -u tterm-web -- env HOME=/var/lib/tterm-web PIP_NO_CACHE_DIR=1 "$@"; }
as_postgres() { (cd / && runuser -u postgres -- "$@"); }

main() {
[ -n "$APP" ] && [ -n "$CONNECT" ] && [ -n "$EMAIL" ] ||
    fail "Usage: sh setup.sh app.example.com connect.example.com you@example.com"
[ "$(id -u)" = 0 ] || fail "Run it as root."
. /etc/os-release
[ "${ID:-}" = ubuntu ] || fail "This is for Ubuntu 24.04; this server has ${PRETTY_NAME:-something else}."

say "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3 python3-venv postgresql nginx certbot ufw \
    unattended-upgrades

say "Users"
# The signer and the core, each a user of its own; the core reaches the
# signer's socket through the group tterm-sign, never its keys.
getent group tterm-sign >/dev/null || groupadd --system tterm-sign
id -u tterm-signer >/dev/null 2>&1 ||
    useradd --system --gid tterm-sign --home-dir /var/lib/tterm-signer \
        --no-create-home --shell /usr/sbin/nologin tterm-signer
id -u tterm-web >/dev/null 2>&1 ||
    useradd --system --user-group --home-dir /var/lib/tterm-web \
        --no-create-home --shell /usr/sbin/nologin tterm-web
usermod -a -G tterm-sign tterm-web
install -d -o tterm-web -g tterm-web -m 700 /var/lib/tterm-web

say "The code, in $SRC"
if [ -d "$SRC/.git" ]; then
    as_web git -C "$SRC" pull -q
else
    install -d -o tterm-web -g tterm-web -m 755 "$SRC"
    as_web git clone -q "$REPO" "$SRC"
fi
[ -f "$SRC/web/dist/index.html" ] || fail "The page is not in $SRC/web/dist."
[ -x "$SRC/venv/bin/python" ] || as_web python3 -m venv "$SRC/venv"
as_web "$SRC/venv/bin/pip" install -q -r "$SRC/requirements.txt"

say "The database"
as_postgres psql -qtAc "SELECT 1 FROM pg_roles WHERE rolname = 'tterm-web'" |
    grep -q 1 || as_postgres psql -qc 'CREATE ROLE "tterm-web" LOGIN'
as_postgres psql -qtAc "SELECT 1 FROM pg_database WHERE datname = 'tterm_web'" |
    grep -q 1 || as_postgres createdb -O tterm-web tterm_web

say "Settings, in $ETC/core.env"
install -d -o root -g tterm-web -m 750 "$ETC"
if [ ! -f "$ETC/core.env" ]; then
    umask 027
    cat > "$ETC/core.env" <<EOF
# tTerm Web's core. After a change: systemctl restart tterm-web
TTERM_PAGE_URL=https://$APP
TTERM_CONNECT_URL=https://$CONNECT
TTERM_PAGE_HOST=127.0.0.1
TTERM_PAGE_PORT=8090
TTERM_CONNECT_HOST=127.0.0.1
TTERM_CONNECT_PORT=8091
TTERM_DATABASE_URL=postgresql://tterm-web@/tterm_web?host=/var/run/postgresql
TTERM_DATA_DIR=/var/lib/tterm-web
TTERM_SIGNER_SOCKET=/run/tterm-signer/signer.sock
TTERM_MAIL_FROM="tTerm <login@tterm.net>"
# Brevo's API key: sh $SRC/deploy/brevo-key.sh puts it here. Without it the
# codes for signing in go to the core's log: journalctl -u tterm-web
TTERM_BREVO_KEY=
EOF
    chown root:tterm-web "$ETC/core.env"
    chmod 640 "$ETC/core.env"
    umask 022
fi

say "The services"
install -m 644 "$SRC/deploy/tterm-signer.service" "$SRC/deploy/tterm-web.service" \
    "$SRC/deploy/tterm-web-backup.service" "$SRC/deploy/tterm-web-backup.timer" \
    /etc/systemd/system/
install -d -o postgres -g postgres -m 700 /var/backups/tterm-web
systemctl daemon-reload
systemctl enable -q --now tterm-signer tterm-web tterm-web-backup.timer
systemctl restart tterm-signer tterm-web

say "Logs: kept 30 days"
sh "$SRC/deploy/logs.sh"

say "The certificate for $APP and $CONNECT"
install -d -m 755 /var/www/letsencrypt
CONF=/etc/nginx/sites-available/tterm-web
if [ ! -f "/etc/letsencrypt/live/$APP/fullchain.pem" ]; then
    # Port 80 only, for Let's Encrypt to check the names.
    cat > "$CONF" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $APP $CONNECT;
    location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }
    location / { return 404; }
}
EOF
    ln -sf "$CONF" /etc/nginx/sites-enabled/tterm-web
    rm -f /etc/nginx/sites-enabled/default
    nginx -t -q
    systemctl reload nginx
    certbot certonly --webroot -w /var/www/letsencrypt -d "$APP" -d "$CONNECT" \
        --cert-name "$APP" --email "$EMAIL" --agree-tos --no-eff-email \
        --non-interactive ||
        fail "Let's Encrypt could not check $APP and $CONNECT. Do both lead to this server, without Cloudflare's proxy?"
fi
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx

say "nginx"
sed -e "s/APP_NAME/$APP/g" -e "s/CONNECT_NAME/$CONNECT/g" \
    "$SRC/deploy/nginx.conf" > "$CONF"
ln -sf "$CONF" /etc/nginx/sites-enabled/tterm-web
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl reload nginx

say "Firewall: SSH, 80 and 443"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

say "Done"
sleep 3
systemctl is-active tterm-signer tterm-web nginx postgresql
grep __version__ "$SRC/tterm/__init__.py"
echo "Open https://$APP"
}

main
