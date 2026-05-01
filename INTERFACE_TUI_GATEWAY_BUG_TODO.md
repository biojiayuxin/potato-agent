# Interface TUI Gateway Bug TODO

## Scope

This document tracks the concrete user-facing bugs discovered after switching
the Lite frontend chat path to `tui_gateway` mode.

These issues should be fixed before treating the `tui_gateway` integration as
the default stable interface runtime.

## Priority Order

1. Session identity / title / ghost draft bug
2. Per-session busy state instead of global busy state
3. History rendering parity with pre-refactor interface
4. Remove bridge debug noise from the user UI
5. Session-switch performance on long conversations

## 1. New Chat Title / Ghost Draft Bug

### Symptom

When the user creates a new chat, sends a task, and receives a reply:

1. the chat title does not update immediately
2. after refresh, the chat gets a real title
3. but an extra empty `New chat` appears in the sidebar

The user reported reproducing this twice.

### Likely Cause

The current UI session model still mixes these different identities and states:

1. draft UI chat id
2. persistent Hermes session id
3. live `tui_gateway` session id

The current draft-to-persisted promotion logic was written for the old
`api_server` path and likely leaves stale draft UI entries behind when a `tui`
session gets its first persisted state/title.

### Required Fix

1. Make the draft-to-persisted transition explicit for `tui` mode
2. Ensure there is only one canonical sidebar entry after first assistant reply
3. Persist and immediately surface the resolved title in the current UI state
4. Eliminate any stale draft entry once the persistent session id is known

### Acceptance Criteria

1. New chat starts as `New chat`
2. After first completed reply, sidebar updates to the resolved title without refresh
3. Refresh does not create a duplicate empty `New chat`
4. Exactly one sidebar entry exists for the conversation before and after refresh

## 2. History Rendering Parity Regression

### Symptom

After a conversation finishes and the page is refreshed, the displayed tool-call
progress and tool-call rendering differs from the pre-refactor interface view.

The user expects the post-refresh display to match the old interface behavior.

### Likely Cause

The old interface used a specific normalization pipeline built around:

1. `display_store`
2. `_build_fallback_display_messages(...)`
3. the Lite frontend's normalized assistant/tool/progress display shape

The current `tui_gateway` mode uses a minimal conversion of resumed messages and
does not yet reconstruct the same display semantics for:

1. tool progress lines
2. reasoning blocks
3. tool-call summaries
4. merged assistant/tool context segments

### Required Fix

1. Rebuild the `tui` history hydration path so it produces the same Lite display
   shape the old interface produced
2. Reuse the old normalization rules for merged assistant progress/tool states
3. Ensure refresh/resume produces the same rendered conversation structure as the
   old `api_server` path

### Acceptance Criteria

1. Refreshing a completed conversation preserves tool progress presentation
2. Refreshed view visually matches the pre-refactor interface style
3. Tool call sections, progress sections, reasoning sections, and merged message
   grouping remain stable before and after refresh

## 3. Remove `TUI gateway connected` Debug UI

### Symptom

The chat panel currently shows debug status text such as:

1. `TUI gateway connected`
2. snippets of streaming output in that status line

The user does not want to see these messages.

### Likely Cause

The minimal bridge bring-up used a visible status line for debugging and this
has not yet been removed.

### Required Fix

1. Remove the visible debug status line from normal user mode
2. Keep internal logging if needed, but do not surface transport state in the
   main chat UI
3. If a debug mode is still useful, gate it behind a developer-only flag that is
   off by default

### Acceptance Criteria

1. No `TUI gateway connected` text appears during normal use
2. No bridge transport debug text appears while the assistant streams
3. User-visible status remains limited to actual product UX, not backend plumbing

## 4. Session Switch Performance Is Too Slow

### Symptom

Switching between conversations is very slow, especially for long-context chats.

### Likely Cause

Possible causes include a combination of:

1. repeated `session.resume` calls for already-live chats
2. unnecessary full-history reloads from disk on every switch
3. extra UI normalization work on large transcripts
4. duplicate backend fetches and redundant sidebar/message refreshes

### Required Fix

1. Reuse live `tui_gateway` sessions when already present
2. Avoid full resume/reload when the session is already live in the browser bridge
3. Cache normalized message state per persistent session where safe
4. Minimize repeated full re-renders for long transcripts

### Acceptance Criteria

1. Switching to an already-open long conversation feels near-instant
2. No obvious multi-second stall on each switch for long chats
3. Live session reuse is observable in code and not defeated by accidental resets

## 5. Busy State Still Global, Not Per Conversation

### Symptom

Response occupancy is still effectively global instead of bound to the currently
running conversation.

The user explicitly wants running-state ownership to be per conversation.

### Likely Cause

The old Lite frontend still uses page-global state for:

1. `isSending`
2. `currentAbortController`
3. approval state and/or streaming ownership assumptions

Even after switching chat submission to `tui_gateway`, the composer logic still
mostly behaves as a single global active request pipeline.

### Required Fix

1. Replace global busy state with per-session running state
2. Bind stop/interrupt to the active live `tui_gateway` session only
3. Allow switching to another conversation and submitting there while one
   conversation is still running
4. Keep same-session protection: one live conversation cannot submit a second
   foreground turn while it is already running

### Acceptance Criteria

1. If conversation A is running, conversation B can still submit a new task
2. The stop button only interrupts the currently viewed running conversation
3. Same conversation cannot submit twice concurrently
4. Sidebar and main view correctly indicate which conversation is actually busy

## Suggested Fix Sequence

Implement in this order:

1. Fix draft/persistent session identity promotion
2. Fix per-session busy state ownership
3. Remove debug bridge status UI
4. Restore refresh/resume rendering parity
5. Optimize session switching performance

## Notes For Implementation

1. Do not modify Hermes source code
2. Prefer fixing identity/state ownership first, because the rendering and
   performance bugs are likely downstream of incorrect session mapping
3. Re-test with the same `user_test` flow after each fix:
   - create new chat
   - send prompt
   - wait for reply
   - switch chats
   - refresh page
   - verify sidebar and transcript consistency
