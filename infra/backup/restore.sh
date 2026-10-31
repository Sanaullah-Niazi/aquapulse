#!/bin/sh
# ---------------------------------------------------------------
# restore.sh (M12, NFR-18): put a backup back into PostgreSQL. Runs inside
# the `backup` container (it has the matching PostgreSQL tools).
#
#   restore.sh check [file]   restore into a SEPARATE test database, compare
#                             it with the live one, then delete it.
#                             Changes nothing. (make restore-check)
#   restore.sh restore file   REPLACE the live database with the backup.
#                             Only through `make restore`, which asks first,
#                             stops the services and makes one more backup.
#
# [file] is a name in ./backups; without one, the newest backup is used.
# TimescaleDB needs two extra calls around pg_restore
# (timescaledb_pre_restore / timescaledb_post_restore), done here.
# ---------------------------------------------------------------
set -eu

dir=/backups
export PGHOST=db PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" PGOPTIONS="-c client_min_messages=warning"
live="$POSTGRES_DB"
mode="${1:-}"
file="${2:-}"

sql() { # sql DATABASE "statements"
  psql --quiet --no-psqlrc -v ON_ERROR_STOP=1 -d "$1" -c "$2" >/dev/null
}
value() { # value DATABASE "query"  -> one value
  psql --no-psqlrc -v ON_ERROR_STOP=1 -At -d "$1" -c "$2"
}

if [ -z "$file" ]; then
  file=$(ls -1 "$dir"/aquapulse-*.dump 2>/dev/null | sort -r | head -n 1 || true)
  [ -n "$file" ] || { echo "No backup in ./backups yet. Make one with: make backup" >&2; exit 1; }
else
  file="$dir/$(basename "$file")" # only files in ./backups
fi
[ -f "$file" ] || { echo "Backup not found: $file" >&2; exit 1; }

restore_into() { # restore_into DATABASE
  sql postgres "DROP DATABASE IF EXISTS \"$1\" WITH (FORCE)"
  sql postgres "CREATE DATABASE \"$1\""
  sql "$1" "CREATE EXTENSION IF NOT EXISTS timescaledb"
  sql "$1" "SELECT timescaledb_pre_restore()"
  # --no-owner/--no-acl are not used: the owner and the API role's rights
  # come back exactly as they were (both roles exist on this server).
  if ! pg_restore --dbname="$1" --exit-on-error "$file" 2>"$dir/.restore-error"; then
    echo "pg_restore failed:" >&2
    cat "$dir/.restore-error" >&2
    sql "$1" "SELECT timescaledb_post_restore()" || true
    return 1
  fi
  sql "$1" "SELECT timescaledb_post_restore()"
  rm -f "$dir/.restore-error"
}

case "$mode" in
check)
  test_db="${live}_restore_check"
  echo "Restoring $(basename "$file") into a separate test database..."
  restore_into "$test_db"
  status=0
  # Tables that only change through people: must match exactly (CI makes the
  # backup just before this check). Readings and the audit log keep growing,
  # so the copy may have fewer, but never none when the live one has some.
  for table in users buildings homes devices tariffs bills payments; do
    a=$(value "$live" "SELECT count(*) FROM $table")
    b=$(value "$test_db" "SELECT count(*) FROM $table")
    if [ "$a" = "$b" ]; then mark="✅"; else mark="❌"; status=1; fi
    printf '  %s %-14s live %8s   restored %8s\n' "$mark" "$table" "$a" "$b"
  done
  for table in telemetry tank_readings audit_log; do
    a=$(value "$live" "SELECT count(*) FROM $table")
    b=$(value "$test_db" "SELECT count(*) FROM $table")
    if [ "$b" -le "$a" ] && { [ "$a" -eq 0 ] || [ "$b" -gt 0 ]; }; then mark="✅"; else mark="❌"; status=1; fi
    printf '  %s %-14s live %8s   restored %8s  (keeps growing)\n' "$mark" "$table" "$a" "$b"
  done
  a=$(value "$live" "SELECT count(*) FROM timescaledb_information.hypertables")
  b=$(value "$test_db" "SELECT count(*) FROM timescaledb_information.hypertables")
  if [ "$a" = "$b" ]; then mark="✅"; else mark="❌"; status=1; fi
  printf '  %s %-14s live %8s   restored %8s\n' "$mark" "hypertables" "$a" "$b"
  sql postgres "DROP DATABASE \"$test_db\" WITH (FORCE)"
  if [ "$status" -eq 0 ]; then
    echo "Restore check passed: the backup can be restored. (Test database deleted again.)"
  else
    echo "Restore check FAILED (see ❌ above). If accounts, homes or bills were changed" >&2
    echo "after this backup was made, make a fresh one (make backup) and check again." >&2
  fi
  exit "$status"
  ;;
restore)
  [ "${CONFIRM:-}" = "RESTORE" ] || { echo "Use: make restore FILE=... (it asks first)" >&2; exit 1; }
  echo "Replacing the database '$live' with $(basename "$file")..."
  restore_into "$live"
  echo "Database restored from $(basename "$file")."
  ;;
*)
  echo "Use: restore.sh check [file]  or  restore.sh restore file" >&2
  exit 1
  ;;
esac
