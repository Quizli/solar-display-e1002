# Operations

This runbook covers the production Synology deployment managed with Docker Compose.

## Deployment

```bash
cd /volume1/docker/solar-display-e1002
git switch main
git pull --ff-only origin main
sudo docker compose up -d --build --remove-orphans
```

## Health check

```bash
sudo docker compose exec -T dashboard-publisher python3 -m dashboard health --text
```

The command is read-only and makes no network request. Exit code `0` means healthy,
`1` means usable but degraded, and `2` means unhealthy.

## Status and logs

```bash
sudo docker compose exec -T dashboard-publisher python3 -m dashboard status
sudo docker compose logs --tail=100 collector dashboard-publisher
```

## Restart

```bash
sudo docker compose restart collector dashboard-publisher web
```

## Data locations

```text
./data/solar.db
./data/sun-data.json
./publish/dashboard.svg
./publish/dashboard.json
```

`data` and `publish` are persistent host bind mounts; rebuilding or replacing a
container does not remove their contents.

The existing publisher writes JSON every 15 seconds and SVG every 300 seconds by
default. After deployment, verify both files without exposing configuration:

```bash
sudo docker compose exec -T dashboard-publisher python3 -m dashboard once
sudo docker compose exec -T web python3 -c 'import json; json.load(open("/usr/share/nginx/html/dashboard.json"))'
```

## Backup

Include `data/solar.db` in the normal Synology backup or snapshot. For an
additional transactionally consistent manual backup while the application is
running, execute this single PuTTY-compatible command from the repository root:

```bash
sudo docker compose exec -T collector python3 -c 'import sqlite3; source=sqlite3.connect("/data/solar.db"); target=sqlite3.connect("/data/solar-backup.db"); source.backup(target); target.close(); source.close()'
```

`/data` is the persistent `./data` bind mount, so the backup appears on the NAS
as `data/solar-backup.db`. SQLite's `backup()` creates a consistent backup while
the application is running. Include the resulting file in the protected
Synology backup after the command completes.

## Restore

1. Stop the stack with `sudo docker compose down`.
2. Preserve the current database, for example with `mv data/solar.db data/solar.db.before-restore`.
3. Copy the selected backup to `data/solar.db` and retain the ownership and permissions expected by `SOLAR_UID` and `SOLAR_GID`.
4. Start the stack with `sudo docker compose up -d`.
5. Run `sudo docker compose exec -T dashboard-publisher python3 -m dashboard health --text`.

## Common cases

- **`TECH` during the first five minutes of a new hour:** this is expected until a completed five-minute aggregate supplies the hour's fact anchor.
- **Historical facts:** comparisons need several completed local days; the normal hourly facts remain available while history accumulates.
- **Stale data:** run the health command, then inspect collector and publisher logs. Stale solar data is unhealthy and the last good SVG is retained.
- **Fronius connection failure:** check the collector logs, NAS connectivity, and the locally configured endpoint; do not put its value in tickets or documentation.
- **E1002/browser shows an older SVG:** compare the SVG age in health output, restart `dashboard-publisher` and `web` if needed, then reload or clear the browser cache.
- **Schema version:** `python3 -m dashboard health` reports `schema_version`; the current version is `4`.
