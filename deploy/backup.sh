#!/bin/sh
# The database, every night, as the user postgres: a dump a day, the last
# two weeks of them, in /var/backups/tterm-web. The CA keys and auth.key
# are not in it. The tterm-web-backup timer runs this.
set -eu

DIR=/var/backups/tterm-web

main() {
    umask 077
    name="$DIR/tterm_web-$(date +%Y-%m-%d).dump"
    pg_dump --format=custom --file="$name.part" tterm_web
    mv "$name.part" "$name"
    find "$DIR" -name 'tterm_web-*.dump' -mtime +14 -delete
}

main
