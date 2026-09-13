# Portal and Chat Workspace

`/` and `/lite` restore account information and display the portal. `/chat`
uses the same Lite HTML, JavaScript, and styles. When Web Locks are available,
it starts the runtime only after acquiring `potato-chat-workspace-v1`. Successful interactive
sign-in and Quick Start enter `/chat`. The Potato Agent module navigation and
the small-screen notice's return link open `/lite` without starting the runtime.
Example buttons enter `/chat`; legacy `/#share=...` and `/lite#example=...` links
are normalized in the browser.

The lock covers one origin and browser profile. Other profiles, private windows,
browsers, and devices are independent. HTTPS and localhost use Web Locks;
background tabs retain them. This exclusive mode also requires BroadcastChannel
and IndexedDB. The portal remains usable when these capabilities are unavailable.

HTTP origins without Web Locks use independent chat tabs. There is no browser
lease, heartbeat, cross-tab takeover, or shared draft handoff in this mode.
Opening another chat leaves existing chats and their drafts in place. Requests
are deduplicated per tab using session storage. BroadcastChannel, when available,
still propagates sign-out and account changes; authentication polling remains
active. Backend account inactivity and task protections are unchanged. Drafts
remain in memory and are not restored after a full page reload or crash.

In exclusive mode, entering chat moves the workspace into the current tab. BroadcastChannel
requests a handoff from the existing owner; a separate transfer lock
serializes contenders. The owner saves its current session, composer draft,
uploaded attachment metadata, composer mode, scroll positions, file browser,
and preview tabs (including local failed turns in an unsaved chat) before
releasing the workspace lock and replacing its URL
with `/lite`. It remains signed in. No browser tab-focus request is used.

The temporary handoff is stored in the origin's IndexedDB under the account
identity. The recipient reads it only after acquiring the workspace lock and
validating authentication, then removes it after successful restoration.
Failed runtime startup or a closing recipient leaves the saved handoff
available to the next entry. Storage failure keeps the original workspace
and draft in place. Uploads and pending submissions finish before transfer.
Backend tasks continue; sessions, live tasks, and approvals reload from the
existing APIs in the recipient. File previews reload from their paths.

With Web Locks, an unresponsive owner is never forcibly unlocked. After 20 seconds, entry
stops waiting and offers retry. Closing or crashing the owner releases the
browser lock. History and BFCache restoration revalidate ownership without
requesting takeover; a retired tab stays on the portal until explicit entry.

Incoming shares and examples carry request IDs. Drafts, attachments, active
tasks, and approvals require confirmation in the current, receiving tab
before switching content. Cancellation preserves the restored content.
Examples only populate the composer. A pending request rejects conflicting
requests with a retry status. The last 128 completed request receipts are
kept per account in browser local storage, when available; request payloads
stay in tab storage until acknowledged. Existing server-side share import
idempotency and retry handling remain in use. Sign-out, session expiration,
and account changes invalidate the old workspace and saved handoff.

## Validation

Browser tests mock every API and never start a real agent runtime:

```bash
POTATO_WORKSPACE_BROWSER_TESTS=1 python -m pytest interface/test_chat_workspace_browser.py
POTATO_WORKSPACE_BROWSER_TESTS=1 python -m pytest interface/test_chat_workspace_http_browser.py
POTATO_EXAMPLE_BROWSER_TESTS=1 python -m pytest interface/test_agent_examples.py
POTATO_NAVIGATION_BROWSER_TESTS=1 python -m pytest interface/test_navigation_browser.py
python -m pytest interface/test_chat_workspace_routes.py interface/test_lite_*.py
```

Set `POTATO_PLAYWRIGHT_EXECUTABLE` for an existing Chromium installation and
`POTATO_WORKSPACE_SCREENSHOTS` to retain desktop and mobile screenshots.

## Release

This change adds a page route and static assets. It adds no business API,
database migration, systemd change, or runtime orchestration change. Static
Lite JavaScript uses `20260910-http-tabs`, examples use `20260910-http-fallback`,
and styles use `20260910-sidebar-home`. Shared navigation uses
`20260912-portal-link`.
Previously open chat tabs running older JavaScript must be refreshed or closed to participate
in the handoff protocol. Deploying this checkout to `/srv/potato_agent` still
requires owner approval.
