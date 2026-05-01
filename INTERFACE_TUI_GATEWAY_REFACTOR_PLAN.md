# Interface TUI Gateway Refactor Plan

## Goal

Replace the current `interface -> Hermes API server` chat path with an
`interface -> per-user tui_gateway bridge` chat path, while keeping durable
background capabilities on each user's existing Hermes systemd service.

This plan is intentionally written as an execution document. It defines the
target architecture, the hard constraints we must preserve, the interface
surface we will build against, and the phased rollout order.

## Non-Negotiable Constraints

1. Do not modify Hermes source code.
2. Each authenticated user must continue to run inside their own Linux user,
   `HOME`, `HERMES_HOME`, and workspace directory.
3. Chat sessions must be isolated from other users and from sibling sessions of
   the same user.
4. Durable background capabilities must remain attached to the user's Hermes
   systemd service.
5. The interface must not become the primary long-lived multi-user runtime for
   cron, background process recovery, or gateway watcher orchestration.

## Final Architecture

### Two Runtime Channels

The system will use two different Hermes runtime channels for different jobs.

1. Interactive channel: per-user `tui_gateway`
2. Durable channel: per-user Hermes systemd service (`hermes gateway run`)

### Interactive Channel Responsibilities

Use `tui_gateway` for:

1. Multi-session foreground chat
2. Session creation, listing, resume, history retrieval
3. Streaming assistant output
4. Approval prompts and approval responses
5. Session interrupt
6. Session-scoped commands like `/steer`
7. Short-lived login-scoped background agent tasks via `prompt.background`
8. Session-scoped subagent observability and interruption

### Durable Channel Responsibilities

Keep the existing per-user systemd Hermes service for:

1. Cron ticking and cron execution
2. Long-lived gateway runtime behavior
3. Background process watcher recovery from `processes.json`
4. Durable `terminal(background=true, notify_on_complete=true)` lifecycle
5. Existing runtime wake/sleep semantics already tied to service state

## Isolation Model

### User Isolation

Every `tui_gateway` subprocess must run as the mapped Linux user, not as the
interface process user with only environment overrides.

Required launch properties:

1. process user: `user.target.linux_user`
2. working directory: `user.target.workdir`
3. `HOME=user.target.home_dir`
4. `HERMES_HOME=user.target.hermes_home`

Recommended spawn shape:

1. `runuser -u <linux_user> -- env HOME=... HERMES_HOME=... python -m tui_gateway.entry`

This preserves all existing OS-level user isolation and ensures Hermes uses the
same config, state, credentials, and filesystem boundaries as the user's
service.

### Session Isolation

Within a single user's `tui_gateway` process, Hermes already maintains separate
interactive sessions.

Important properties we rely on:

1. `session.create`, `session.resume`, `session.list`, `session.history`
2. `prompt.submit` only blocks the same live session, not all sessions
3. approvals are keyed to the session's `session_key`
4. subagent controls are session-aware

This gives us the required same-user multi-session concurrency without changing
Hermes.

### Durable Isolation

Durable background behavior remains on the per-user systemd service. This keeps
cron and process watcher recovery attached to the same `HERMES_HOME` and the
same service that Hermes expects.

## Why We Are Not Using the Current API Server Chat Path

The current API server chat path is not the right integration surface for this
product requirement.

Reasons:

1. It is OpenAI-compatible, but not strongly session-native for our use case.
2. Its chat execution path uses a non-session-specific `task_id` internally.
3. Many Hermes tools isolate runtime state using `task_id`.
4. Allowing true concurrent chat sessions through that surface risks cross-
   session tool-state leakage.

Conclusion: the API server remains useful for compatibility surfaces, but not as
the primary backend for this multi-session interface.

## Bridge Design

### New Interface Backend Component

Add a new interface-side component responsible for managing one live
`tui_gateway` subprocess per logged-in user workspace.

Suggested module name:

1. `interface/tui_gateway_bridge.py`

### Bridge Responsibilities

The bridge owns:

1. subprocess lifecycle
2. JSON-RPC request/response routing
3. event stream fanout
4. per-user live session registry
5. shutdown and cleanup
6. restart/reconnect handling when the subprocess exits

### One Bridge Per Logged-In User

The interface process should maintain a registry like:

1. `user_id -> bridge instance`

Each bridge instance owns exactly one `tui_gateway` subprocess running as that
user.

### Transport

`tui_gateway` speaks newline-delimited JSON-RPC over stdio.

The bridge must:

1. write JSON-RPC requests to stdin
2. read JSON-RPC responses and async event notifications from stdout
3. serialize writes
4. correlate responses by `id`
5. fan out `event` notifications to frontend subscribers

### Frontend Transport

The browser should not speak stdio JSON-RPC directly.

Add an interface-owned browser-facing transport, preferably WebSocket.

Suggested new browser transport:

1. `GET /api/tui/ws`

The browser talks to the interface WebSocket.
The interface WebSocket talks to the per-user bridge.

## Session Identity Model

There are two different session ids we must track.

1. Live bridge session id: short-lived `tui_gateway` RPC session handle
2. Persistent Hermes session id: the real persisted Hermes session key

### Important Distinction

`tui_gateway` returns a transient RPC `session_id` handle when it creates or
resumes a session. This is the id used in subsequent RPC calls to that running
gateway process.

However, resumed history is anchored to a persistent Hermes session key inside
Hermes state.

Therefore the interface must maintain a mapping layer.

### Required Interface Mapping Layer

Maintain something like:

1. `ui_session_id -> live_gateway_session_id`
2. `ui_session_id -> persistent_hermes_session_id`

The browser should not need to understand Hermes's transient bridge session ids.

### Session Flow

#### New chat

1. frontend requests new chat
2. bridge sends `session.create`
3. interface allocates its own stable UI chat id
4. interface stores mapping to live gateway session id
5. persistent Hermes session id becomes known after the conversation is written
   or after we explicitly reconcile against Hermes session history

#### Resume existing chat

1. sidebar item corresponds to persistent Hermes session id
2. bridge sends `session.resume` with that persistent id
3. bridge returns a new live gateway session id
4. interface updates mapping for the existing UI chat id

### Consequence

The interface data model must stop assuming one id is sufficient for both UI and
backend chat state.

## Interface HTTP API Changes

### Keep Existing Runtime Endpoints

Keep these concepts:

1. sign-in
2. session cookie auth
3. `/api/runtime/start`
4. runtime sleep/revocation model

These are still useful for controlling access to the user's workspace runtime.

### Chat Endpoints to Replace or Deprecate

The following current chat endpoints stop being the primary frontend chat path:

1. `/api/chat/completions`
2. `/api/chat/approvals/{approval_id}`

They can remain temporarily for compatibility while the frontend migrates, but
the new frontend should prefer the `tui_gateway` WebSocket path.

### New Interface Backend Endpoints

Add browser-facing interface endpoints roughly along these lines:

1. `GET /api/tui/ws`
2. optional `POST /api/tui/session/create`
3. optional `POST /api/tui/session/resume`
4. optional `GET /api/tui/session/list`

Preferred approach: put almost all interactive operations over the WebSocket so
the frontend has a single transport for commands and events.

## Frontend Refactor Plan

### Replace Chat Completion Fetch Path

Remove the direct `/api/chat/completions` SSE workflow from the main chat UX.

Replace it with a WebSocket-driven state machine:

1. open authenticated socket to interface
2. send JSON command to submit prompt for a specific live session
3. stream back `message.delta`, `tool.progress`, `approval.request`,
   `message.complete`, and error events

### Multi-Session State Model

The frontend must maintain per-chat runtime state.

Required per-chat fields:

1. UI chat id
2. live gateway session id
3. persistent Hermes session id
4. current message list
5. running flag
6. pending approval state
7. background task notices
8. last known usage/info snapshot

### Busy Semantics

Busy must be per live session, not global.

Rules:

1. one live chat can be `running`
2. sibling chats remain sendable
3. stop button only interrupts the currently viewed running chat

### Frontend Event Types to Handle

At minimum, consume these `tui_gateway` events:

1. `message.start`
2. `message.delta`
3. `message.complete`
4. `error`
5. `tool.progress`
6. `approval.request`
7. `session.info`
8. `background.complete`

Possible later additions:

1. `status.update`
2. subagent-related events

## Approval Flow

### Interactive Chat Approvals

Approvals for foreground chat should move to `tui_gateway`'s approval event and
response interface.

Use:

1. event: `approval.request`
2. command: `approval.respond`

This removes the need for the current HTTP approval forwarding endpoint to be
the primary path.

### Approval State Ownership

Approval state must be stored per live session on the interface/frontend side.
Never keep a single global approval modal state for the whole page.

## Interrupt Flow

Use `session.interrupt` for foreground chat cancellation.

Rules:

1. interrupt must target only the active live session
2. interrupt must not cancel sibling chats
3. interrupt should also clear any pending approval for that live session

## Sidebar and History Model

### Sidebar Source of Truth

Use Hermes persisted session history for the sidebar. The best source is the
session listing capability exposed through the interactive bridge.

Preferred path:

1. `session.list`

For an opened chat:

1. if already live in the bridge, reuse mapping
2. otherwise call `session.resume`
3. hydrate browser messages from the resume payload or `session.history`

## Background Tasks

### Interactive Background Agent Tasks

Use `prompt.background` for login-scoped, short-to-medium-lived background agent
tasks that belong to the current interactive bridge.

Do not treat them as durable service-owned tasks.

### Durable Background Processes

Do not move durable background process semantics into `tui_gateway`.

Keep these on the service side:

1. `terminal(background=true, notify_on_complete=true)` when durability matters
2. recovered process watchers
3. long-lived process notifications across runtime restarts

This means the interface may eventually expose a separate background-process UI
that queries service-owned durable process state instead of overloading the chat
bridge.

## Cron

### Principle

Use `tui_gateway` only to manage cron definitions from the interface.
Execution must remain on the user's Hermes systemd service.

### Recommended Interface Behavior

1. cron management UI talks to service-owned cron management endpoints or to a
   service-side compatibility path
2. interface shows cron status as durable service state
3. no expectation that login-scoped `tui_gateway` keeps cron alive

### Important Constraint

Do not move cron ticking into the login-scoped bridge. That would couple cron to
browser sessions and defeat the point of the systemd runtime.

## Runtime Start / Sleep Interaction

### Current Issue to Solve During Refactor

Today the runtime wake/sleep model is built around the service and API chat
lease. Once chat moves to `tui_gateway`, interactive activity must not be
invisible to runtime management.

### Required Design Decision

We must add interface-owned activity tracking for live bridges so the user's
service is not slept while the user is actively chatting.

Two acceptable approaches:

1. while a per-user bridge exists and has any running session, treat the user as
   active
2. add an explicit interface lease/heartbeat for live bridge activity

Preferred approach:

1. interface-owned lease keyed by user id and bridge liveness

This preserves the service sleep model without requiring Hermes changes.

## Bridge Lifecycle

### Startup

When the user enters workspace:

1. ensure the durable Hermes service is ready using existing runtime wake logic
2. start or attach to the user's `tui_gateway` bridge subprocess
3. open browser WebSocket
4. create or resume live interactive sessions on demand

### Shutdown

When the user signs out or the browser disconnects:

1. close browser WebSocket
2. if no browser clients remain for that user, terminate the bridge subprocess
3. keep the durable systemd service lifecycle independent

### Reconnect

If the browser reconnects while the bridge is still alive:

1. reuse the existing per-user bridge
2. rebind subscribers to bridge events
3. rehydrate visible chats from live or persisted state

## Implementation Phases

### Phase 1: Backend Bridge Skeleton

Deliverables:

1. `interface/tui_gateway_bridge.py`
2. per-user bridge registry
3. secure subprocess launch as mapped Linux user
4. stdio JSON-RPC transport
5. WebSocket endpoint between browser and interface

Acceptance:

1. can start bridge for a user
2. can send `session.create`
3. can receive `gateway.ready`
4. can submit `prompt.submit` and stream back text

### Phase 2: Frontend Chat Migration

Deliverables:

1. replace `/api/chat/completions` path
2. websocket-driven chat runtime state
3. per-session running state
4. per-session interrupt

Acceptance:

1. one running chat does not block sibling chats
2. same chat still blocks re-submit while running
3. stop only interrupts the correct chat

### Phase 3: Session Mapping and Resume

Deliverables:

1. UI chat id vs live bridge id vs persistent Hermes id mapping
2. sidebar migration to bridge-backed session listing
3. resume existing persisted chat through `session.resume`

Acceptance:

1. reopening a saved chat resumes correct history
2. multi-chat navigation works without losing live state

### Phase 4: Approval and Tool Progress

Deliverables:

1. `approval.request` modal integration
2. `approval.respond` command path
3. tool progress rendering from bridge events

Acceptance:

1. dangerous command approval works end-to-end
2. approval only affects the originating chat

### Phase 5: Runtime Activity and Sleep Safety

Deliverables:

1. bridge-aware runtime activity tracking in interface
2. ensure active interactive chats prevent unintended service sleep

Acceptance:

1. user is not logged out while actively chatting through the bridge
2. idle users still sleep correctly when bridge is gone and no durable work is active

### Phase 6: Durable Background and Cron UX Separation

Deliverables:

1. clear UI distinction between interactive background tasks and durable service
   background/cron features
2. cron management page that explicitly targets durable service behavior
3. optional durable background process status page backed by service-side state

Acceptance:

1. users can understand which tasks survive logout and which do not
2. cron remains functional without any browser client connected

## What We Will Not Do

1. We will not patch Hermes internals.
2. We will not keep using the API server as the main chat backend.
3. We will not move cron ticking into the login-scoped bridge.
4. We will not treat `prompt.background` as a durable service-owned task.
5. We will not collapse user isolation into a shared global bridge process.

## Open Questions to Resolve Before Coding Phase 1

1. Should the browser talk to the bridge through WebSocket only, or WebSocket
   plus a few REST helpers for bootstrap?
2. Should one user be allowed multiple browser clients against the same bridge
   concurrently?
3. Do we want the sidebar session list to come entirely from `tui_gateway`
   session methods, or keep interface-owned display metadata layered on top?
4. What is the inactivity timeout policy for the bridge subprocess itself after
   the last browser client disconnects?

## Recommended Answers to Open Questions

1. WebSocket as the primary runtime transport, with REST only for auth/runtime
   bootstrap.
2. Yes, allow multiple browser clients for one user, but only one bridge
   subprocess per user.
3. Keep interface-owned display metadata layered on top of Hermes session list.
4. Terminate the bridge shortly after the last browser client disconnects unless
   we later add a reconnect grace window.

## Immediate Next Step

Start with Phase 1 and do not touch frontend behavior until the bridge can:

1. launch correctly as the mapped Linux user
2. create a live session
3. submit a prompt
4. stream `message.delta` and `message.complete`

Until those four capabilities work reliably, do not start the bigger UI rewrite.
