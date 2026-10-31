# Database backups (NFR-18)

AquaPulse keeps everything important in one PostgreSQL (TimescaleDB) database:
accounts, homes, readings, bills, payments and the audit log. The `backup`
service makes a copy of it every day, by itself.

| SRS target (NFR-18) | How it is met |
|---|---|
| Daily automatic `pg_dump` backup | The `backup` service ([infra/backup/backup.sh](../infra/backup/backup.sh)) |
| Restore tested in CI, weekly | The CI stack job makes a backup, restores it into a test database and compares it, then really restores it and runs the smoke test again. CI runs on every push and every Monday |
| RPO ≤ 24 h (at most a day of data lost) | A new backup whenever the newest one is older than 24 hours |
| RTO ≤ 1 h (working again within an hour) | `make restore` is one command; CI prints how long the full restore took |

## How it works

- Every 10 minutes the service checks: is the newest backup older than
  `BACKUP_EVERY_HOURS` (24)? If so, it makes a new one. A Mac that was asleep or
  switched off catches up as soon as the stack runs again.
- Each backup is one file in the `backups/` folder of the project on your
  computer, for example `backups/aquapulse-20261031-020000.dump`. The folder is
  git-ignored, so backups never end up on GitHub.
- The file is read back once (`pg_restore --list`) before it counts. A broken or
  half-written file is deleted, never kept as "the latest".
- Only the newest `BACKUP_KEEP` (7) files are kept: one week.
- After each good backup, the System Admin's **System health** page shows the
  time under **Last backup**. If the last one is more than 2 days old, the page
  says so.

## Commands

```bash
make backup          # make a backup now
make backups         # list the backups, newest first
make restore-check   # prove the newest backup can be restored (changes nothing)
make restore FILE=aquapulse-20261031-020000.dump   # put a backup back (asks first)
```

`make restore-check` restores the backup into a separate test database, counts
the rows of the main tables in both, prints them side by side and deletes the
test database again. Your real data is not touched.

`make restore` **replaces all current data** with the backup. It asks you to type
`RESTORE`, then:

1. makes one more backup of the data as it is now, so the restore can be undone;
2. stops the API, ingestor, worker and broker-auth;
3. deletes the database, creates it again and loads the backup;
4. starts everything again and waits until it is all healthy.

## Things to know

- **Same versions.** A TimescaleDB backup must be restored with the same
  TimescaleDB version (2.31.0, PostgreSQL 16 here). The backup service uses the
  same image as the database, so this is automatic on the same AquaPulse
  version. Before upgrading the database image, make a backup first.
- **Keep a copy somewhere else.** The backups are on the same computer as the
  database. If the disk dies, both are gone. Copy the newest file to a USB drive
  or a cloud folder now and then.
- **Backups contain personal data** (names, emails, readings, bills). They are
  not encrypted by AquaPulse. If you copy them off your computer, put them in an
  encrypted place (an encrypted USB drive or disk image, for example).
- The database password is not in the backup. Restoring on another computer
  works with that computer's own `.env`.
- Change how often and how many with `BACKUP_EVERY_HOURS` and `BACKUP_KEEP` in
  `.env`, then `make up`.
