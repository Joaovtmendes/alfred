#!/usr/bin/env bash
# S1-09 — encrypted logical backup of the database (pg_dump → AES-256, passphrase from the environment).
#
#   BACKUP_PASSPHRASE=... DATABASE_URL_SYNC=postgresql://user:pass@host:port/db \
#       scripts/backup_db.sh backup  /path/alfred-YYYYmmdd.dump.enc
#   BACKUP_PASSPHRASE=... DATABASE_URL_SYNC=... scripts/backup_db.sh restore /path/alfred-....dump.enc
#
# The passphrase never touches disk or the command line (``-pass env:``). Keep it in a password
# manager, NOT only in Railway: a backup nobody can open is not a backup. Restore into an empty
# database (it uses --clean --if-exists, so it replaces what is there).
set -euo pipefail

mode="${1:-}"; file="${2:-}"
: "${BACKUP_PASSPHRASE:?set BACKUP_PASSPHRASE}"
: "${DATABASE_URL_SYNC:?set DATABASE_URL_SYNC (postgresql://...)}"
[ -n "$file" ] || { echo "usage: $0 backup|restore FILE" >&2; exit 2; }

case "$mode" in
  backup)
    umask 077
    pg_dump --format=custom --no-owner --dbname="$DATABASE_URL_SYNC" \
      | openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt -pass env:BACKUP_PASSPHRASE -out "$file"
    [ -s "$file" ] || { echo "backup is empty" >&2; rm -f "$file"; exit 1; }
    echo "encrypted backup written: $file ($(wc -c <"$file") bytes)"
    ;;
  restore)
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass env:BACKUP_PASSPHRASE -in "$file" \
      | pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL_SYNC"
    echo "restored from $file"
    ;;
  *) echo "usage: $0 backup|restore FILE" >&2; exit 2 ;;
esac
