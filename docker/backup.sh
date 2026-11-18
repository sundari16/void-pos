#!/bin/sh
# Nightly logical backup of the venue database into /backups (mounted from ./backups).
#
#   BACKUP_AT         local time of day to run, HH:MM (default 03:00, in $TZ)
#   BACKUP_KEEP_DAYS  delete dumps older than this many days (default 14)
#
# Takes one backup on start so a fresh install proves it works, then one a day. A dump is
# written under a temporary name and renamed when complete, so a crash never leaves a
# truncated file that looks like a good backup. Restore: see docs/self-host.md.
set -eu

AT="${BACKUP_AT:-03:00}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
DIR=/backups

backup() {
    stamp=$(date +%Y%m%d-%H%M%S)
    partial="$DIR/.void_pos-$stamp.dump.partial"
    if pg_dump --format=custom --file="$partial"; then
        mv "$partial" "$DIR/void_pos-$stamp.dump"
        echo "backup: wrote void_pos-$stamp.dump"
    else
        rm -f "$partial"
        echo "backup: pg_dump failed; will try again at the next run" >&2
    fi
    find "$DIR" -maxdepth 1 -name 'void_pos-*.dump' -mtime +"$KEEP_DAYS" -print -delete
}

seconds_until_next_run() {
    now=$(date +%s)
    next=$(date -d "$AT" +%s)
    if [ "$next" -le "$now" ]; then
        next=$((next + 86400))
    fi
    echo $((next - now))
}

mkdir -p "$DIR"
backup
while true; do
    sleep "$(seconds_until_next_run)"
    backup
done
