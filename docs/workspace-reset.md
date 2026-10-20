# Workspace reset

The chat settings menu exposes **Reset workspace** only for verified, dedicated
Potato accounts when `INTERFACE_WORKSPACE_RESET_ENABLED=1`. The user must type
`RESET` and confirm permanent deletion. This is a workspace reset, not account
deletion. Login credentials, Linux identity, usage and quota records survive.
Files, uploads, chats, memories, custom environments, owned share links and the
workspace's server archives are removed. Public data and copies previously
imported into other accounts remain. The new workspace uses the current platform
defaults; external services and files outside the managed home are outside scope.

## Eligibility and rollout

New Linux accounts created by provisioning receive a root-owned enrollment record
under `/var/lib/potato-agent/workspace-identities`. Binding an existing Linux user
does not enroll it. The owner has confirmed that existing **Linux account names
starting with `hmx`** belong to the regular Potato registration flow. Enable reset
for these existing accounts in bulk with:

```sh
cd /srv/potato_agent
sudo /opt/interface-env/bin/python -B -m interface.workspace_reset enroll-hmx
```

This migration checks the Linux account name in the mapping (not the website login
name), validates each home and UID, and writes the same root-owned enrollment
record used for new registrations. Repeating it skips already enabled accounts;
conflicting identity records are never overwritten. It reports aggregate counts
and error codes, returning a nonzero exit status if any account could not be
enrolled. Retry after busy workspaces finish their current operation. Enrollment
does not reset files or chats, and users still need to type `RESET` to confirm a
reset. The settings action becomes available on refresh, without a service restart.

Other existing accounts still require administrator verification that they are
dedicated to Potato, followed by:

```sh
cd /srv/potato_agent
sudo /opt/interface-env/bin/python -B -m interface.workspace_reset enroll USERNAME --confirm-dedicated-account
```

Enable the optional systemd drop-in in
`packaging/systemd/potato-interface-workspace-reset.conf` and restart Interface.
The standard `interface.serve` entry point explicitly runs one ASGI worker because
chat ownership, bridge management and the request drain barrier are process-local.
Do not run multiple Interface workers or replicas sharing these workspaces.
The existing restricted privileged helper performs the filesystem operations;
no arbitrary client-supplied filesystem path is accepted.

To test manually, use a newly registered or newly created Quick Start workspace.
Upload a disposable file, create a chat, optionally start a task, and select
Settings → Reset workspace. Cancel once to verify preservation, then type `RESET`
and confirm. Verify that the new workspace is empty, can chat/upload/share again,
and has the default environment. Also refresh during a reset to check progress
recovery. Do not use an account containing important files for acceptance testing.

## Execution and recovery

Confirmation tokens expire after five minutes. Submission is idempotent and
increments the workspace generation. All workspace HTTP requests and sockets are
gated; old requests drain before filesystem changes. The service stops known
gateway descendants, runtime services and recorded background processes. Unknown
UID processes, external crontabs/at jobs and lingering user services cause a
refusal before destructive changes; an administrator must stop those first.

Reset jobs live in the Interface database. Root keeps a separate durable journal
and quarantined home under `.potato-workspace-resets/JOB_ID` beside the home.
This directory is root-only, and the old home is deleted before reporting success.
Symlinks are never followed during deletion; homes containing mountpoints are
refused. Mapping, retention and user locks prevent administrative cleanup races.

Before the commit boundary, initialization failures restore the original home.
After that boundary, failures keep the workspace locked and recovery only moves
forward. Interface restarts resume queued/running jobs. A blocked job needs the
underlying issue resolved, followed by:

```sh
sudo /opt/interface-env/bin/python -B -m interface.workspace_reset retry USERNAME
```

Never manually remove the maintenance job or quarantine while a reset is pending.
Completed resets cannot be undone. Disabling the feature prevents new submissions
but does not disable recovery of already accepted requests. For a code rollback,
first ensure there are no queued/running/blocked jobs; older code cannot enforce
their maintenance gates. Logs contain task IDs, stages and error codes, not chat
content or private filesystem paths.

## Validation

Run the Interface suite with a private `POTATO_AGENT_STATE_DIR`,
`INTERFACE_AUTH_DB` and `POTATO_AGENT_LIFECYCLE_LOCK_DIR`, so tests cannot access
production state. Focused regressions are `interface/test_workspace_reset.py` and
`interface/test_workspace_reset_browser.py`; enable the latter with
`POTATO_WORKSPACE_BROWSER_TESTS=1`. `POTATO_WORKSPACE_SCREENSHOTS` saves review images.
The fixtures use temporary databases and directories, without real API keys,
privileged account creation or production workspace deletion.
