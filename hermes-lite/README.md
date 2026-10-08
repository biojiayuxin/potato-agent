# Potato Hermes Lite

`hermes-lite/` is the Potato-owned, physically reduced Hermes distribution. It
is the only source tree for new Potato builds and runtime processes.

The full `hermes-agent/` tree and the old `packaging/hermes/` release pipeline
are legacy audit and rollback sources. Lite must not import from them, use them
as an editable-install fallback, or include them as build inputs.

## Runtime Contract

Supported surfaces are:

- Potato Web sessions through `python -m tui_gateway.entry`;
- the systemd compatibility guard `hermes gateway run --replace`;
- the sealed Potato profile and custom model provider;
- bundled, optional, managed, and user skills.

The profile contains a logical allowlist of 27 model tools. This is a maximum,
not a promise that every request contains all 27. Availability checks may hide
tools whose local backend is unavailable, including `vision_analyze`,
`browser_cdp`, and `browser_dialog`. A request may contain only a subset of the
allowlist and can never add tools outside it.

Classic Hermes CLI/TUI, dashboard, ACP, cron, MCP, messaging platforms,
external provider adapters, media generation, voice, web search, and automatic
dependency installation are outside this runtime.

Existing image attachments and the model's existing vision path remain in
place. Lite does not add a new native-image protocol or image attachment tool.
It also does not add `clarify`, `sudo`, or `secret` Web interactions. Approval
and interrupt remain part of the Potato session contract.

Local terminal commands, background tasks, helpers, and `execute_code` inherit
the user's `HOME` supplied by Interface. `HERMES_HOME` remains the directory for
configuration, sessions, skills, and memory. An existing `HERMES_HOME/home/`
directory does not override the subprocess `HOME`, including when resuming a
different profile. The session working directory and `TERMINAL_CWD` remain
independent of `HOME`, so a project can start in its own directory while `cd ~`
returns to the user's home. Existing files under `HERMES_HOME/home/` are retained;
tool configuration stored there may need to be migrated separately. Activate
this change with a new gateway process so old shell snapshots are discarded.

## Conversation Model Snapshots

Interface resolves each chat's stable model option from the protected catalog
and supplies a trusted snapshot at creation, cold recovery, and turn admission.
Gateway applies it at the shared execution entry, including plan, notification,
and delegation continuations. `session.model.set` is a backend-only RPC; browsers
cannot provide model snapshots, endpoints, or credentials. A busy chat cannot
change models, while other chats continue independently on the same connection.

The `potato_hermes_lite/session_models.py` adapter updates the client route,
API mode, reasoning effort, compressor window, and primary runtime metadata.
It renders the actual upstream model in the prompt and records public identity
snapshots in SQLite. Main calls and children inheriting the parent runtime use
the signed route for the admitted configuration. Upstream addresses and keys
remain in the local model proxy's protected file. These adaptations leave the
inherited turn loop and tool scheduling unchanged.

Deploy Interface, model proxy, and Lite together. Configuration fields, SQLite
meanings, compatibility aliases, and rollback constraints are documented in the
[model catalog contract](../interface/MODEL_CATALOG.md).

## Build And Verify

The source verifier uses `python -S -B -P` and explicit dependency paths to
prove that owned modules resolve from the isolated Lite tree. Builds and the
locked release artifacts target CPython 3.12 on Linux x86_64. First create and
populate the dedicated build venv and prepare a clean browser asset tree as
documented in sections 6.1 and 6.2 of the
[HPC Deployment Guide](../HPC_DEPLOYMENT.md), then run from
`hermes-lite/`:

```bash
BUILD_PYTHON=/opt/potato-hermes-lite-build-env/bin/python3
BROWSER_ASSETS=/var/tmp/potato-hermes-lite-browser-assets
RELEASE_OUTPUT=/var/tmp/potato-hermes-lite-release-candidate

"$BUILD_PYTHON" -c \
  'import platform,sys; assert sys.implementation.name == "cpython"; assert sys.version_info[:2] == (3, 12); assert sys.platform == "linux"; assert platform.machine() == "x86_64"'
test -x "$BROWSER_ASSETS/browser/bin/agent-browser"
test -x "$BROWSER_ASSETS/browser/chrome/chrome-linux64/chrome"
test -f "$BROWSER_ASSETS/browser/chrome/chrome-linux64/chrome_sandbox"
test ! -e "$BROWSER_ASSETS/browser/chrome/chrome-linux64/chrome-sandbox"
test ! -e "$RELEASE_OUTPUT"

(
umask 022
"$BUILD_PYTHON" -B scripts/verify_lite.py \
  --python "$BUILD_PYTHON"

"$BUILD_PYTHON" -B scripts/build_lite_release.py \
  --dry-run \
  --python "$BUILD_PYTHON" \
  --browser-assets "$BROWSER_ASSETS"

"$BUILD_PYTHON" -B scripts/build_lite_release.py \
  --python "$BUILD_PYTHON" \
  --browser-assets "$BROWSER_ASSETS" \
  --output "$RELEASE_OUTPUT"
)
```

Use the same `umask 022` for both reproducibility builds and the inactive
installer. A restrictive inherited mask can produce unreadable release
directories even when root's build and install probes pass; it can also change
ZIP permission metadata and the wheel hash. Keep the enclosing private build
directory at `0700`. Credentials and database backups retain their separate
private modes. Before cutover, verify the installed runtime is readable and
executable by an actual mapped account as described in section 6.4 of the guide.

The build interpreter must be the dedicated build venv. Do not use
`/opt/potato-hermes-lite/current/venv`, any immutable release venv, or the
legacy Hermes venv as the builder: production release venvs intentionally omit
build tooling, and the legacy environment is not a supported Lite build input.
A deployable build always supplies `--browser-assets`; omitting it is valid only
for a non-deployable diagnostic and must not feed the release installer.

Focused tests live in `tests/`, `tests_packaging/`, and `tests_e2e/`. The mock
provider E2E starts the real stdio gateway with isolated `HOME` and
`HERMES_HOME`; it covers prompt completion, resume, interrupt, and approval
denial and expiration without contacting a real provider. The expiration case
also verifies that a late response is rejected and the guarded command is not
executed. Model regressions additionally cover independent sessions, busy-state
rejection, frozen request parameters, catalog updates, prompt identity, and cold
recovery through a real local proxy and mock provider.

## Internal Session Visibility

The Lite session storage adapter records user/internal session types in
`potato_session_visibility` in each user's existing `state.db`. SQLite triggers
preserve this classification across compression and parent deletion, including
writes through retained Hermes connections. Linked legacy subagents are marked
when the adapter first opens the database for writing. Normal branches and user
compression continuations remain visible. Read-only access infers legacy
ancestry without migrating the database.

Deploy Interface and Lite together. Internal transcripts remain in storage for
agent use and audit; Interface blocks their list, detail, export, share, fork,
resume, and live-state access, including cached transcripts for deleted IDs.
The marker table intentionally has no cascading foreign key. Do not remove its
entries as part of session cleanup.

Old orphans cannot be reliably classified from their titles or message contents.
After confirming exact session IDs, preview a repair using the Lite interpreter
as the account that owns the database:

```bash
python -m potato_hermes_lite.session_visibility \
  --db /path/to/state.db --mark-internal CONFIRMED_SESSION_ID
```

The command is read-only by default. Add `--apply` only when the database change
is approved; it records the reviewed IDs and their descendants without deleting
messages. Production deployment and repairs require owner approval. Rolling
back to an older Interface/Lite pair also rolls back the visibility checks.

## Deployment Status

Deployments use immutable Lite releases through
`/opt/potato-hermes-lite/current`. Read that symlink and its `manifest.json` on
the target host for the active release, version, and wheel hash. The checkout's
version alone does not identify deployed code. Local test and YNNU production
deployments are independent; updating one does not update the other. The
[HPC Deployment Guide](../HPC_DEPLOYMENT.md) documents cutover and host requirements.

The approval protocol carries an exact request ID from
the Lite waiter through Gateway, Interface, and the browser, and emits an
`approval.expired` lifecycle event when that exact waiter times out. A lost or
late HTTP response cannot resolve or leave behind another queued approval. Do not
delete `hermes-agent/` or the legacy venv until the observation and rollback
retention period has passed. User `HERMES_HOME`, session databases, mappings,
and Interface databases are external state and must never be removed as part
of source cleanup or release switching.

Retained engine files originate from Nous Research Hermes Agent 0.16.0 and
remain covered by `LICENSE`. Potato-specific boundary code lives under
`potato_hermes_lite/`.


## Background delegation in Potato Web

Lite 10 selectively ports the delegation lifecycle reviewed at Nous Hermes
`f42f579cf8bac4918ac9599bece71618afadd846`. Ship the matching Interface update
with this runtime: Interface accepts completed results as managed assistant
turns, maintains the background runtime lease, and routes child approvals.

Top-level model calls to `delegate_task` return immediately when a gateway result
consumer is attached. The parent can do independent work and then end its turn;
results arrive between turns. Ordinary follow-up messages leave children running.
Python callers and nested orchestrators retain synchronous result collection.
Capacity exhaustion returns an explicit error. The default concurrent child limit
remains 3 per parent session, and the default child iteration budget remains 50.

```yaml
delegation:
  child_timeout_seconds: 0
  independent_completions: false
```

`child_timeout_seconds` no longer caps total runtime: a positive value is a
renewable inactivity window (minimum 30 seconds); nonpositive values disable
that optional window. The activity watchdog still stops a child after 450 seconds
without progress outside a tool or 1200 seconds inside a tool. Streaming text,
API activity and tool changes renew progress. Provider and tool timeouts remain
in effect. Existing explicit `600` configurations therefore mean ten minutes
without progress, not ten minutes since launch.

Use `delegate_task(action="list")` for owned tasks, `action="steer"` with
`subagent_id` and `message` to adjust an active task, `action="stop"` to request
its stop, and `action="result"` with its ID to read retained output. Steering is
applied at the next iteration boundary. Explicit session Stop/close stops the
background tree and suppresses automatic follow-up; saved results remain readable.
With `independent_completions: true`, tasks deliver separately, except that tasks
with the same optional `group` wait for all members of that group.

The private `$HERMES_HOME/potato-delegations.db` ledger saves final results and
periodic partial snapshots. Timeouts include available assistant text, recent
tool output, artifact paths and a child session ID instead of discarding all work.
Snapshots and notifications are bounded; only content already produced can be
recovered. A stuck thread keeps its capacity slot and resources until it exits.
Finished delivered/suppressed records are pruned after seven days when a manager
is opened; pending results are retained. An exited process's unfinished children
become `unknown`, never automatically re-executed. A crash during a parent delivery
turn can replay that notification; this is not exactly-once execution of external
effects or resumption of a stopped child.

The implementation rationale and upstream differences are recorded in
`../docs/reviews/hermes-delegation-upstream-2026-09-30.md`.
