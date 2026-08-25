# Admin observability deployment

The `/admin` workbench is read-only. Token totals cover fully consumed successful
model proxy responses and are not billing-grade data. System metrics are sampled
in memory every five seconds; only daily storage snapshots are retained.

## Production authorization boundary

Installing credentials or units, changing sudoers, enabling the storage timer,
promoting the first administrator, restarting services, and changing `/srv` or
system-level state require separate owner approval. Repository deployment alone
must not perform those operations.

## Pre-deployment

1. Back up the Interface auth DB and model proxy `usage.db` with SQLite's backup
   API, then run `PRAGMA integrity_check` against both backups.
2. Create a dedicated random admin usage token of at least 32 bytes in the
   approved secret-management system. It must not match any user model proxy
   token or the Daily Updates service token.
3. Materialize the credential as
   `/etc/potato-agent/credentials/admin-usage-token`, owned by `root:root` with
   mode `0400`. Do not put the token in a unit `Environment=` line.
4. Confirm the auth DB remains in `/var/lib/potato-agent/data` with directory
   mode `0700` and database/sidecar mode `0600`. Confirm `usage.db` remains under
   the model proxy-owned `0700` directory with mode `0600`.

## Deployment order

1. Deploy the code, privileged helper wrapper, and the two storage snapshot unit
   templates. Validate the helper and units from the deployment tree before
   installing them.
2. Install `potato-storage-snapshot.service` and
   `potato-storage-snapshot.timer`, but do not enable the timer until a manual
   oneshot run succeeds.
3. Promote the initial formal account:

   ```bash
   /opt/interface-env/bin/python /srv/potato_agent/manage_interface_users.py \
     set-role LOGIN admin
   ```

4. Restart `potato-model-proxy.service`, verify its health and internal endpoint
   authentication, then restart `potato-interface.service`.
5. Run `potato-storage-snapshot.service` once. Confirm the output contains only
   aggregate counts and no usernames or home paths, then enable and start the
   timer.

## Verification

- Verify `/admin` sets `potato_admin_token` with `HttpOnly`, `Secure`,
  `SameSite=Strict`, and `Path=/admin`; ordinary and temporary users must receive
  the same authentication error.
- Verify `/admin/api/overview` still returns current accounts and storage when
  the proxy is stopped, with usage fields marked unavailable rather than zero.
- Verify `systemctl show` still reports `ProtectProc=invisible` and
  `ProcSubset=pid` for Interface and model proxy services.
- Verify the model proxy process cannot read the auth DB and the Interface
  process cannot read `usage.db` directly.
- Verify the timer reports `Persistent=yes` and the next trigger is 04:00 in
  `Asia/Shanghai`.

## Rollback

Stop and disable `potato-storage-snapshot.timer`, restore the previous code and
unit versions, and restart the proxy followed by Interface. The new auth DB
tables can remain in place; previous versions ignore them. Restore a database
backup only when integrity checks or migration verification failed, not for a
routine code rollback.
