# Daily Updates

Daily Updates publishes LLM-filtered potato research from PubMed on the public
Potato Agent sign-in page. The web process only reads the resulting SQLite
database. A dedicated systemd oneshot owns collection, classification,
translation, and database writes.

## Runtime boundaries

- Live database: `/srv/daily_updates/data/daily_updates.sqlite`
- Legacy snapshots: `/srv/daily_updates/legacy/<timestamp>/`
- Collector identity: `potato-daily-updates`
- Data access group: `potato-daily-updates`
- Model principal: `daily-updates-service`
- Model endpoint: `http://127.0.0.1:8765/v1`
- Schedule: 08:00 Asia/Shanghai, with persistent catch-up after downtime

The Interface service is a read-only member of the data group. The collector
also receives the `potato-interface` group so it can read deployed Python code.
The model proxy receives the collector credential but is not a member of the
data group. Ordinary Hermes users receive neither permission.
The collector unit masks `/var/lib/potato-agent` and `/etc/potato-agent`, so its
source traversal group does not grant access to mapping, Interface state, or
credential source files; systemd exposes only the named credentials under its
private `$CREDENTIALS_DIRECTORY`.

Real upstream model keys remain exclusively in `model_proxy.yaml`. Never put a
model key, the service token, or a PubMed API key in a unit `Environment=` line,
an environment file, the repository, process arguments, or logs.

## Configuration

The collector reads these non-secret settings:

- `DAILY_UPDATES_DB_PATH`, defaulting to the live path above
- `DAILY_UPDATES_LLM_BASE_URL`, defaulting to the local model proxy
- `DAILY_UPDATES_PUBMED_EMAIL`, required by NCBI

It reads `daily-updates-model-proxy-token` from `$CREDENTIALS_DIRECTORY`. An
optional NCBI key uses the `daily-updates-pubmed-api-key` credential. Explicit
`*_FILE` paths are supported for development and controlled manual runs;
production rejects literal token/key environment values.

The service principal is restricted to the current primary model route. The
collector discovers that route through authenticated `GET /v1/models`, so an
upstream model name is not duplicated in the unit.

## Initial host setup

These are production system changes and require owner approval before use.

```bash
getent group potato-daily-updates >/dev/null || \
  groupadd --system potato-daily-updates
getent passwd potato-daily-updates >/dev/null || \
  useradd --system --gid potato-daily-updates \
    --home-dir /nonexistent --shell /usr/sbin/nologin \
    potato-daily-updates
# Remove membership left by an earlier draft of this runbook. The unit grants
# source traversal only while the collector is running.
if id -nG potato-daily-updates | tr ' ' '\n' | \
    grep -Fxq potato-interface; then
  gpasswd --delete potato-daily-updates potato-interface
fi
if id -nG potato-daily-updates | tr ' ' '\n' | \
    grep -Fxq potato-interface; then
  echo 'collector still has persistent potato-interface membership' >&2
  exit 1
fi

install -d -o root -g potato-daily-updates -m 0750 /srv/daily_updates
install -d -o potato-daily-updates -g potato-daily-updates -m 0750 \
  /srv/daily_updates/data
install -d -o root -g root -m 0700 \
  /srv/daily_updates/legacy

install -d -o root -g root -m 0700 /etc/potato-agent/credentials
TOKEN_CREDENTIAL=/etc/potato-agent/credentials/daily-updates-model-proxy-token
test ! -e "$TOKEN_CREDENTIAL"
python3 - "$TOKEN_CREDENTIAL" <<'PY'
import os
import secrets
import sys

path = sys.argv[1]
fd = os.open(
    path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o600,
)
try:
    os.write(fd, secrets.token_urlsafe(48).encode("ascii"))
finally:
    os.close(fd)
PY
chown root:root "$TOKEN_CREDENTIAL"
chmod 0600 "$TOKEN_CREDENTIAL"
test "$(stat -c '%U:%G:%a' "$TOKEN_CREDENTIAL")" = 'root:root:600'
```

Configure the required NCBI contact email in a root-owned unit drop-in. The
email is operational contact data, not an API credential.

```bash
install -d -o root -g root -m 0755 \
  /etc/systemd/system/potato-daily-updates.service.d
PUBMED_CONTACT_EMAIL=pubmed-contact@example.org
if [[ "$PUBMED_CONTACT_EMAIL" == 'pubmed-contact@example.org' ]]; then
  echo 'replace the example PubMed contact email before continuing' >&2
  exit 1
fi
if [[ ! "$PUBMED_CONTACT_EMAIL" =~ ^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$ ]]; then
  echo 'invalid PubMed contact email (percent is not supported in a systemd Environment= value)' >&2
  exit 1
fi
SITE_DROPIN=/etc/systemd/system/potato-daily-updates.service.d/20-site.conf
test ! -e "$SITE_DROPIN"
(
  set -o noclobber
  umask 022
  printf '[Service]\nEnvironment=DAILY_UPDATES_PUBMED_EMAIL=%s\n' \
    "$PUBMED_CONTACT_EMAIL" >"$SITE_DROPIN"
)
chown root:root "$SITE_DROPIN"
chmod 0644 "$SITE_DROPIN"
grep -Fxq "Environment=DAILY_UPDATES_PUBMED_EMAIL=$PUBMED_CONTACT_EMAIL" \
  "$SITE_DROPIN"
```

Replace the example address before running the block.

If an NCBI API key is needed, install it as
`/etc/potato-agent/credentials/daily-updates-pubmed-api-key` with owner/mode
`root:root 0600`, then add this line to the same `[Service]` section:

```ini
LoadCredential=daily-updates-pubmed-api-key:/etc/potato-agent/credentials/daily-updates-pubmed-api-key
```

Install the packaged units only after the group, user, directories, credential,
and site drop-in exist:

```bash
install -D -o root -g root -m 0644 \
  /srv/potato_agent/packaging/systemd/potato-model-proxy.service \
  /etc/systemd/system/potato-model-proxy.service
install -D -o root -g root -m 0644 \
  /srv/potato_agent/packaging/systemd/potato-interface.service \
  /etc/systemd/system/potato-interface.service
install -D -o root -g root -m 0644 \
  /srv/potato_agent/packaging/systemd/potato-daily-updates.service \
  /etc/systemd/system/potato-daily-updates.service
install -D -o root -g root -m 0644 \
  /srv/potato_agent/packaging/systemd/potato-daily-updates.timer \
  /etc/systemd/system/potato-daily-updates.timer
install -d -o root -g root -m 0755 \
  /etc/systemd/system/potato-interface.service.d \
  /etc/systemd/system/potato-model-proxy.service.d \
  /etc/systemd/system/potato-daily-updates.service.d
install -o root -g root -m 0644 \
  /srv/potato_agent/packaging/systemd/potato-interface-daily-updates.conf \
  /etc/systemd/system/potato-interface.service.d/50-daily-updates.conf
install -o root -g root -m 0644 \
  /srv/potato_agent/packaging/systemd/potato-model-proxy-daily-updates.conf \
  /etc/systemd/system/potato-model-proxy.service.d/50-daily-updates.conf
systemd-analyze verify \
  /etc/systemd/system/potato-model-proxy.service \
  /etc/systemd/system/potato-interface.service \
  /etc/systemd/system/potato-daily-updates.service \
  /etc/systemd/system/potato-daily-updates.timer
systemctl daemon-reload
SITE_DROPIN=/etc/systemd/system/potato-daily-updates.service.d/20-site.conf
EXPECTED_PUBMED_ENVIRONMENT=$(grep -E \
  '^Environment=DAILY_UPDATES_PUBMED_EMAIL=[^[:space:]]+@[^[:space:]]+$' \
  "$SITE_DROPIN")
test -n "$EXPECTED_PUBMED_ENVIRONMENT"
systemctl show potato-daily-updates.service --property=Environment --value | \
  grep -Fq "${EXPECTED_PUBMED_ENVIRONMENT#Environment=}"
```

Daily Updates access is deliberately packaged as feature drop-ins instead of
being embedded in the two base units. The normal Lite cutover can therefore
deploy compatible code before this setup is complete without making the live
Interface or model proxy depend on a not-yet-created group or credential.

### Model proxy credential rotation

Never replace the shared credential while only one consumer is being restarted.
Use a maintenance window, stop new collector activations, retain a private
rollback copy, atomically install the new source, then restart the proxy before
starting the collector:

```bash
set -euo pipefail
systemctl stop potato-daily-updates.timer
systemctl stop potato-daily-updates.service

TOKEN_CREDENTIAL=/etc/potato-agent/credentials/daily-updates-model-proxy-token
NEXT_CREDENTIAL="${TOKEN_CREDENTIAL}.next"
PREVIOUS_CREDENTIAL="${TOKEN_CREDENTIAL}.previous"
test -f "$TOKEN_CREDENTIAL"
test ! -e "$NEXT_CREDENTIAL"
test ! -e "$PREVIOUS_CREDENTIAL"
python3 - "$NEXT_CREDENTIAL" <<'PY'
import os
import secrets
import sys

path = sys.argv[1]
fd = os.open(
    path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o600,
)
try:
    os.write(fd, secrets.token_urlsafe(48).encode("ascii"))
finally:
    os.close(fd)
PY
chown root:root "$NEXT_CREDENTIAL"
chmod 0600 "$NEXT_CREDENTIAL"
cp --reflink=auto --preserve=mode,ownership,timestamps -- \
  "$TOKEN_CREDENTIAL" "$PREVIOUS_CREDENTIAL"
chmod 0600 "$PREVIOUS_CREDENTIAL"
mv -T -- "$NEXT_CREDENTIAL" "$TOKEN_CREDENTIAL"

systemctl restart potato-model-proxy.service
systemctl is-active --quiet potato-model-proxy.service
systemctl start potato-daily-updates.service
systemctl start potato-daily-updates.timer
rm -f -- "$PREVIOUS_CREDENTIAL"
```

If proxy restart or the manual collector run fails, leave the timer stopped and
restore the rollback copy before restarting the proxy:

```bash
TOKEN_CREDENTIAL=/etc/potato-agent/credentials/daily-updates-model-proxy-token
PREVIOUS_CREDENTIAL="${TOKEN_CREDENTIAL}.previous"
test -f "$PREVIOUS_CREDENTIAL"
mv -T -- "$PREVIOUS_CREDENTIAL" "$TOKEN_CREDENTIAL"
chown root:root "$TOKEN_CREDENTIAL"
chmod 0600 "$TOKEN_CREDENTIAL"
systemctl restart potato-model-proxy.service
systemctl is-active --quiet potato-model-proxy.service
systemctl start potato-daily-updates.timer
```

## Unified database transfer

Use this path when another host will receive an already unified
`daily_updates.sqlite`. Do not run the legacy three-database migration afterward;
that migration is only for the original Knowledge Hub databases.

The live database is `/srv/daily_updates/data/daily_updates.sqlite`, owned by
`potato-daily-updates:potato-daily-updates` with mode `0640`. A raw copy of the
live file while the collector is running is not an acceptable snapshot. Stop the
source timer and collector, then use the SQLite online backup API to create a
standalone root-only transfer file. The backup API must complete
`PRAGMA integrity_check` and `PRAGMA foreign_key_check` on both the source and
the transfer file. Restore the source timer only if it was active before the
maintenance window.

The snapshot procedure in the legacy migration section below demonstrates the
required `sqlite3.Connection.backup` pattern. For a unified database, apply that
same pattern to the single live database instead of the three legacy names. Do
not transfer a live `-journal` file, and do not copy the main file independently
of an active journal.

On the destination:

1. Complete the initial host setup and unit/drop-in installation above, but
   leave `potato-daily-updates.timer` stopped.
2. Stop `potato-daily-updates.service`, preserve any existing live database as a
   root-only rollback snapshot, and refuse the cutover if a live journal remains.
3. Transfer the standalone snapshot to a root-only staging path. Verify
   `integrity_check`, `foreign_key_check`, and the required `papers`,
   `processing_state`, and `job_runs` tables before publishing it.
4. Install it as a temporary file in `/srv/daily_updates/data` with owner/mode
   `potato-daily-updates:potato-daily-updates 0640`, then atomically rename it to
   `daily_updates.sqlite` on the same filesystem.
5. Run the collector manually, verify the public API and permissions, then
   enable the timer as described in the cutover section.

This transfer moves the publication history and collector checkpoints together.
Running both the old Knowledge Hub scheduler and the new collector is still
forbidden, including when they run on different hosts against separately copied
databases.

## Legacy database migration

Before taking snapshots, stop or disable every Potato Knowledge Hub process
that imports its Flask application and starts the embedded APScheduler. Check
other hosts and process managers as well as this host. Two active schedulers
would duplicate PubMed and LLM work.

Keep the three source databases unchanged. After the old writer has stopped,
use the SQLite online backup API to install root-only snapshots below a new
timestamped legacy directory. This example uses the existing Knowledge Hub
databases on this host and refuses to reuse a destination:

```bash
set -euo pipefail
LEGACY_SOURCE=/home/jiayuxin/tmp/potato-knowledge-hub/data
LEGACY_SNAPSHOT=/srv/daily_updates/legacy/$(date -u +%Y%m%dT%H%M%SZ)
test -d "$LEGACY_SOURCE"
test ! -e "$LEGACY_SNAPSHOT"
install -d -o root -g root -m 0700 "$LEGACY_SNAPSHOT"
umask 077
/opt/interface-env/bin/python - "$LEGACY_SOURCE" "$LEGACY_SNAPSHOT" <<'PY'
import os
import sqlite3
import sys
from pathlib import Path

source_root = Path(sys.argv[1]).resolve(strict=True)
snapshot_root = Path(sys.argv[2]).resolve(strict=True)
names = ("pubmed_repo.db", "pubmed_results.db", "pubmed_results_CN.db")

for name in names:
    candidate = source_root / name
    if candidate.is_symlink():
        raise SystemExit(f"source must not be a symlink: {candidate}")
    source_path = candidate.resolve(strict=True)
    if source_path.parent != source_root or not source_path.is_file():
        raise SystemExit(f"invalid legacy source: {candidate}")
    snapshot_path = snapshot_root / name
    if snapshot_path.exists():
        raise SystemExit(f"snapshot already exists: {snapshot_path}")

    with sqlite3.connect(
        f"{source_path.as_uri()}?mode=ro", uri=True, timeout=30.0
    ) as source:
        source.execute("PRAGMA query_only = ON")
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit(f"source integrity check failed: {source_path}")
        with sqlite3.connect(str(snapshot_path), timeout=30.0) as destination:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SystemExit(f"snapshot integrity check failed: {snapshot_path}")
    os.chmod(snapshot_path, 0o600)
PY
chown root:root "$LEGACY_SNAPSHOT"/*.db
chmod 0600 "$LEGACY_SNAPSHOT"/*.db
for database in \
  "$LEGACY_SNAPSHOT/pubmed_repo.db" \
  "$LEGACY_SNAPSHOT/pubmed_results.db" \
  "$LEGACY_SNAPSHOT/pubmed_results_CN.db"; do
  test "$(stat -c '%U:%G:%a' "$database")" = 'root:root:600'
done
```

Record `LEGACY_SNAPSHOT` in the maintenance log. In a new shell, set it to that
exact directory and verify all three snapshots again before migration.

Run a read-only analysis first. Without `--apply`, this command does not create
or change the target:

```bash
LEGACY_SNAPSHOT=/srv/daily_updates/legacy/REPLACE_WITH_SNAPSHOT_TIMESTAMP
test -d "$LEGACY_SNAPSHOT"
/opt/interface-env/bin/python -m interface.daily_updates_job migrate \
  --repo-db "$LEGACY_SNAPSHOT/pubmed_repo.db" \
  --results-db "$LEGACY_SNAPSHOT/pubmed_results.db" \
  --results-cn-db "$LEGACY_SNAPSHOT/pubmed_results_CN.db" \
  --target /srv/daily_updates/data/daily_updates.sqlite
```

For the example Knowledge Hub snapshot described above, the report must show 889
processed PMIDs, 487 relevant English papers, 454 translations, 33 pending
translations, and 9 retryable legacy LLM failures.
Apply only after reviewing that report:

```bash
LEGACY_SNAPSHOT=/srv/daily_updates/legacy/REPLACE_WITH_SNAPSHOT_TIMESTAMP
test -d "$LEGACY_SNAPSHOT"
umask 077
/opt/interface-env/bin/python -m interface.daily_updates_job migrate \
  --repo-db "$LEGACY_SNAPSHOT/pubmed_repo.db" \
  --results-db "$LEGACY_SNAPSHOT/pubmed_results.db" \
  --results-cn-db "$LEGACY_SNAPSHOT/pubmed_results_CN.db" \
  --target /srv/daily_updates/data/daily_updates.sqlite \
  --apply
chown potato-daily-updates:potato-daily-updates \
  /srv/daily_updates/data/daily_updates.sqlite
chmod 0640 /srv/daily_updates/data/daily_updates.sqlite
```

Do not delete the legacy snapshots after cutover. They are the data rollback
baseline.

## Cutover and verification

Restart the proxy first so it recognizes the service principal, then run the
collector manually before enabling the timer:

```bash
systemctl restart potato-model-proxy.service
systemctl start potato-daily-updates.service
systemctl status potato-daily-updates.service --no-pager
journalctl -u potato-daily-updates.service --since today --no-pager
systemctl restart potato-interface.service
systemctl enable --now potato-daily-updates.timer
systemctl list-timers potato-daily-updates.timer --no-pager
```

The collector journal may contain counts, durations, statuses, and sanitized
error categories. It must not contain credentials, prompts, abstracts, article
summaries, or per-user paths.

Verify application and filesystem boundaries:

```bash
curl -fsS 'http://127.0.0.1:3000/api/daily-updates?limit=1'
systemctl show potato-interface.service \
  --property=SupplementaryGroups --value
sudo -u potato-interface -g potato-daily-updates test -r \
  /srv/daily_updates/data/daily_updates.sqlite
sudo -u potato-daily-updates test -w \
  /srv/daily_updates/data/daily_updates.sqlite
sudo -u potato-model-proxy test ! -r \
  /srv/daily_updates/data/daily_updates.sqlite
sudo -u hmx_example test ! -r \
  /srv/daily_updates/data/daily_updates.sqlite
if systemctl show potato-daily-updates.service potato-model-proxy.service \
  --property=Environment --value | grep -Eq 'API_KEY|TOKEN'; then
  echo 'secret-like variable found in service environment' >&2
  exit 1
fi
```

Replace `hmx_example` with a mapped ordinary Hermes Linux user. In the browser,
verify public English results, the Chinese switch, incremental scrolling,
external PubMed links, the mobile login-first order, and every authentication
form state.

## Rollback

```bash
systemctl disable --now potato-daily-updates.timer
systemctl stop potato-daily-updates.service
rm -f \
  /etc/systemd/system/potato-interface.service.d/50-daily-updates.conf \
  /etc/systemd/system/potato-model-proxy.service.d/50-daily-updates.conf
if id -nG potato-daily-updates | tr ' ' '\n' | \
    grep -Fxq potato-interface; then
  gpasswd --delete potato-daily-updates potato-interface
fi
systemctl daemon-reload
if id -nG potato-daily-updates | tr ' ' '\n' | \
    grep -Fxq potato-interface; then
  echo 'collector still has persistent potato-interface membership' >&2
  exit 1
fi
```

Then restore the previous Interface and model proxy release and restart both
services. The explicit `-g potato-daily-updates` read check above emulates the
Interface unit's feature-only supplementary group; it does not grant persistent
membership to the service account. Preserve the unified database and all legacy
snapshots. Re-enable the Knowledge Hub scheduler only when intentionally
returning ownership of the job to that application; never run both schedulers
concurrently.
