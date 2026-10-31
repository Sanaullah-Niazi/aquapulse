#!/bin/sh
# ---------------------------------------------------------------
# backup.sh (M12, NFR-18): daily database backups, run by the `backup`
# service in docker-compose.yml. In simple words:
#
#   - Every 10 minutes it checks: is the newest backup older than a day
#     (BACKUP_EVERY_HOURS)? Then it makes a new one. So a Mac that was
#     asleep or switched off catches up as soon as the stack runs again.
#   - A backup is one file in ./backups on your computer, made with
#     pg_dump (the standard PostgreSQL tool), e.g. aquapulse-20261031-0200.dump
#   - Each file is read back once (pg_restore --list) before it counts,
#     so a broken or half-written file never replaces a good one.
#   - Only the newest BACKUP_KEEP files (default 7) are kept.
#   - It writes the time into the database, so the System Admin page shows
#     "Last backup" (FR-55).
#
# Usage inside the container:
#   backup.sh          the loop (the service's command)
#   backup.sh once     one backup now (make backup)
# A restore is restore.sh, next to this file (make restore / restore-check).
# ---------------------------------------------------------------
set -eu

dir=/backups
keep="${BACKUP_KEEP:-7}"
every_minutes=$((${BACKUP_EVERY_HOURS:-24} * 60))
export PGHOST=db PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" PGDATABASE="$POSTGRES_DB"

backup_once() {
  name="aquapulse-$(date -u +%Y%m%d-%H%M%S).dump"
  part="$dir/.$name.partial"
  # -Fc: PostgreSQL's compressed "custom" format, which pg_restore reads.
  pg_dump --format=custom --file="$part" 2>"$dir/.last-error" || {
    echo "backup: pg_dump failed: $(cat "$dir/.last-error")" >&2
    rm -f "$part"
    return 1
  }
  pg_restore --list "$part" >/dev/null 2>"$dir/.last-error" || {
    echo "backup: the new file cannot be read back, so it was deleted" >&2
    rm -f "$part"
    return 1
  }
  mv "$part" "$dir/$name"
  rm -f "$dir/.last-error"
  size=$(wc -c <"$dir/$name" | tr -d ' ')

  # Keep the newest $keep backups, delete older ones.
  ls -1 "$dir"/aquapulse-*.dump | sort -r | tail -n +"$((keep + 1))" | while read -r old; do
    rm -f "$old"
  done

  # Tell the System Admin page (same table the ingestor and worker use).
  psql --quiet --no-psqlrc -v ON_ERROR_STOP=1 -v file="$name" -v size="$size" <<'SQL' >/dev/null
INSERT INTO service_heartbeats (service, seen_at, started_at, details)
VALUES ('backup', now(), now(), jsonb_build_object('file', :'file', 'size_bytes', :'size'::bigint))
ON CONFLICT (service) DO UPDATE SET seen_at = excluded.seen_at, details = excluded.details;
SQL
  echo "backup: $name ($size bytes), keeping the newest $keep"
}

mkdir -p "$dir"
if [ "${1:-}" = "once" ]; then
  backup_once
  exit
fi

echo "backup: a new backup whenever the newest is older than $((every_minutes / 60)) h; keeping $keep"
while true; do
  touch /tmp/backup-alive # for the health check
  # find -mmin: is there a backup newer than $every_minutes minutes?
  if [ -z "$(find "$dir" -maxdepth 1 -name 'aquapulse-*.dump' -mmin "-$every_minutes" | head -n 1)" ]; then
    backup_once || echo "backup: will try again in 10 minutes" >&2
  fi
  sleep 600
done
