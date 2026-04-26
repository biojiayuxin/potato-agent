# Approval Patch Notes

This repository adds dangerous-command approval support for the multi-user
Potato Agent web UI without modifying files inside `hermes-agent/`.

## Why this exists

Hermes already has a mature approval system in `tools/approval.py`, but the
`api_server` platform does not expose that workflow to browser clients. The
Lite frontend could receive normal streaming deltas and `hermes.tool.progress`
events, but it had no way to receive a pending approval request or submit the
user's decision back to the blocked agent thread.

This project needs to preserve the current runtime model:

- each web user maps to a dedicated Linux user
- each Linux user runs a dedicated Hermes systemd service
- the Hermes service remains the real execution boundary for chat, files,
  process registry, IM gateway bindings, and `HERMES_HOME`

Because of that, the implementation avoids running Hermes directly inside the
`interface` process.

## Implementation strategy

The approval flow is enabled by a runtime patch that is injected when each
per-user Hermes service starts.

### 1. Runtime patch injection

Files involved:

- `interface/hermes_sitecustomize.py`
- `interface/hermes_api_approval_patch.py`
- `interface/hermes_service.py`

`interface/hermes_service.py` now sets two extra environment variables in the
generated per-user systemd unit:

- `PYTHONPATH=<user hermes patch dir>`
- `POTATO_AGENT_ENABLE_APPROVAL_PATCH=1`

During user provisioning, `interface` writes a small user-readable bootstrap
module to:

- `~/.hermes/.potato-interface-patch/sitecustomize.py`

It also writes a copy of the runtime patch module to:

- `~/.hermes/.potato-interface-patch/hermes_api_approval_patch.py`

When Python starts Hermes, it automatically imports that `sitecustomize.py`
from the per-user patch directory. The bootstrap checks
`POTATO_AGENT_ENABLE_APPROVAL_PATCH` and, when enabled, imports and applies the
monkeypatch from the co-located `hermes_api_approval_patch.py` file.

This keeps the upstream `hermes-agent/` working tree untouched.

### 2. What the patch changes

`interface/hermes_api_approval_patch.py` monkeypatches
`gateway.platforms.api_server.APIServerAdapter` at runtime.

It adds three capabilities:

1. Register Hermes' existing approval callback around streaming chat runs.
2. Emit pending approvals as a new SSE event:
   `event: hermes.approval.required`
3. Add a new API server endpoint:
   `POST /v1/approvals/{approval_id}`

The patch keeps approval state in memory inside the running Hermes API server
adapter. Each approval record stores:

- `approval_id`
- `session_key`
- `session_id`
- `command`
- `description`
- `pattern_key`
- `pattern_keys`
- `created_at`
- `status`

When Hermes needs approval:

1. `tools.approval.register_gateway_notify(...)` is active for the current API
   server request.
2. Hermes calls the notify callback with `approval_data`.
3. The patch stores a pending approval record and pushes an SSE event into the
   live stream queue.
4. The agent thread blocks inside Hermes' normal approval mechanism until a
   decision is submitted.
5. `POST /v1/approvals/{approval_id}` calls
   `tools.approval.resolve_gateway_approval(...)` with the stored session key.

The supported decisions are:

- `once`
- `session`
- `always`
- `deny`

### 3. Interface-side forwarding

Files involved:

- `interface/app.py`

The `interface` service stays as a gateway/proxy layer only.

It now exposes:

- `POST /api/chat/approvals/{approval_id}`

That endpoint forwards the choice to the current user's Hermes service:

- `POST {user.target.api_base_url}/v1/approvals/{approval_id}`

The main chat streaming endpoint still proxies the user's dedicated Hermes API
server. Unknown SSE events were already passed through, so the new approval SSE
event reaches the browser without changing the overall transport model.

### 4. Lite frontend changes

Files involved:

- `interface/static/lite/index.html`
- `interface/static/lite/styles.css`
- `interface/static/lite/app.js`

The frontend now listens for:

- `event: hermes.approval.required`

When received, it opens a modal showing:

- approval reason
- the exact command
- four actions: `Allow once`, `Allow for session`, `Always allow`, `Deny`

Submitting one of those actions calls:

- `POST /api/chat/approvals/{approval_id}`

The original streaming request remains open while Hermes waits for approval.
Once the decision is submitted, Hermes resumes the blocked run and the same SSE
stream continues.

## Maintenance notes

This approach is intentionally low-intrusion, but it depends on Hermes internal
class and method names remaining compatible.

If you upgrade Hermes and approval stops working, check these first:

1. `gateway.platforms.api_server.APIServerAdapter`
2. `APIServerAdapter._run_agent`
3. `APIServerAdapter._write_sse_chat_completion`
4. `APIServerAdapter.connect`
5. `tools.approval.register_gateway_notify`
6. `tools.approval.resolve_gateway_approval`
7. `tools.approval.set_current_session_key`

If any of those signatures or behaviours change upstream, update only:

- `interface/hermes_api_approval_patch.py`

The rest of the project should remain stable.

## Deployment checklist

To deploy this safely and ensure newly created users automatically get the
approval patch, follow this order:

1. Deploy the updated repository contents.
2. Restart the `interface` backend process.
3. Only after that, create or register new users.

This order matters because user provisioning happens inside the running
`interface` Python process. Updating files on disk is not enough — if the old
process is still running, it will keep using the old in-memory version of
`interface/hermes_service.py` and generate outdated per-user systemd units.

### Important: frontend refresh is irrelevant

Refreshing the browser does **not** affect user creation or service generation.
The critical action is restarting the `interface` backend process so it loads
the latest provisioning code.

### Recommended production pattern

Run `interface` under a fixed supervisor such as systemd instead of a manually
started `uvicorn` process. On each deploy:

1. sync repository code
2. restart `interface`
3. create users as needed

This avoids stale in-memory code when provisioning new users.

### Important: existing users do not auto-refresh patch files

The approval patch files are copied into each user's own `HERMES_HOME` during
provisioning or whenever `install_user_files(...)` is called.

That means updating this repository alone does **not** automatically update the
already-copied files under existing users' homes.

After changing any of these files:

- `interface/hermes_sitecustomize.py`
- `interface/hermes_api_approval_patch.py`
- `interface/hermes_service.py`

you must re-run `install_user_files(...)` for existing users and restart their
Hermes services if you want those users to pick up the new patch behaviour.

One supported way to do that is to use:

- `configure_hermes_model.py --apply-to-users`

because that path already calls `install_user_files(...)` for each mapped user
and restarts running Hermes services.

If you do not want to change model settings, you can still use the same helper
logic from a maintenance script — the key point is that existing users need a
refresh pass after patch code changes.

### Hermes upgrade compatibility model

Updating `hermes-agent/` source alone should not require changing the deployment
shape of this approval feature. The deployment contract is:

1. `interface` owns patch generation and user-file installation.
2. Each user's `HERMES_HOME` stores a copied bootstrap + patch module.
3. Hermes continues to start normally with:
   `hermes gateway run --replace`
4. The patch is applied at Python startup through the per-user
   `sitecustomize.py` bootstrap.

So after pulling a newer Hermes version, the operational steps are:

1. update this repository and the deployed Hermes code
2. restart `interface`
3. refresh existing users so their copied patch files are rewritten
4. restart the affected Hermes services

As long as the Hermes internals listed in the "Maintenance notes" section have
not changed incompatibly, this remains enough to re-deploy approval support.

### Why new users automatically get approval support

All supported user-creation paths converge on the same provisioning helper:

- `provision_interface_user.py`
- `bind_existing_linux_user.py`
- signup jobs created by `interface/app.py`

Each of these calls `interface.hermes_service.install_user_files(...)`.

That helper now always does three things for the target user:

1. writes `~/.hermes/.potato-interface-patch/sitecustomize.py`
2. writes `~/.hermes/.potato-interface-patch/hermes_api_approval_patch.py`
3. generates the per-user systemd unit with:
   - `PYTHONPATH=~/.hermes/.potato-interface-patch`
   - `POTATO_AGENT_ENABLE_APPROVAL_PATCH=1`

So as long as the running `interface` process is the updated version, every
newly created user automatically gets the approval patch.

### Fast post-create verification

For a newly created user `<name>`, verify these two things:

1. systemd unit contains:

   `Environment=PYTHONPATH=/home/hmx_<name>/.hermes/.potato-interface-patch`

2. patch files exist:

   - `/home/hmx_<name>/.hermes/.potato-interface-patch/sitecustomize.py`
   - `/home/hmx_<name>/.hermes/.potato-interface-patch/hermes_api_approval_patch.py`

If both are present, approval patch injection is deployed correctly for that
user.

## Behavioural boundaries

This implementation does not:

- persist approval UI state across browser refreshes
- maintain a multi-approval queue UI
- change Hermes' dangerous-command rules
- replace the per-user Hermes runtime with in-process execution inside
  `interface`

Those constraints are deliberate to preserve compatibility with the current
multi-user deployment model.
