# Site Announcements

The `/admin` announcement form manages one site-wide plain text announcement,
with a maximum of 500 Unicode characters. Start and end inputs and the status
summary use Beijing time (Asia/Shanghai); API timestamps use timezone-aware ISO
8601 and storage uses UTC Unix timestamps in the existing Interface database.

- **Publish / Republish** replaces the record with a new ID and clears withdrawal.
  A future start replaces the previous notice immediately, leaving no public
  notice until the new start time.
- **Save** preserves the ID and withdrawal state. Browsers that dismissed this
  ID remain dismissed even when its message or schedule changes.
- **Withdraw** keeps the record available for editing and hides it publicly.
- Start is inclusive; end is exclusive. An empty end lasts until withdrawal.
  State is evaluated on each request, survives restarts, and needs no timer job.

## API

| Method | Path | Body |
| --- | --- | --- |
| GET | `/api/announcement` | Public, no authentication |
| GET | `/admin/api/announcement` | Administrator session required |
| POST | `/admin/api/announcement/publish` | `message`, optional `starts_at`, optional `ends_at` |
| PUT | `/admin/api/announcement` | Same content fields plus current `id` |
| POST | `/admin/api/announcement/withdraw` | Current `id` |

Omitted or null `starts_at` means the server's current time. Omitted or null
`ends_at` means no end. Dates must include their timezone, for example
`2026-10-01T08:00:00+08:00`. End must be later than start. Invalid input returns
422. Saving or withdrawing a replaced ID returns 409; reload before editing.
Writes reuse the existing administrator authentication and same-origin check.

Responses contain `server_time` and `announcement` (an object or null).
Public objects contain only `id`, `message`, `starts_at`, and `ends_at`.
Admin objects also contain `withdrawn`, `updated_at`, and `status` (`scheduled`,
`active`, `ended`, or `withdrawn`). A null admin record means unpublished.
Responses prohibit caching. Public polling neither resolves a user session nor
refreshes foreground activity, login lifetime, or runtime idle lifetime.

## Browser Behavior

Nine public HTML entries and the admin page load the shared announcement assets.
The versioned legal document is unchanged. Notice text and preview use
`textContent`. Dismissal keys are scoped by ID in `localStorage`; storage events
synchronize tabs, and in-memory dismissal remains when storage is unavailable.

Visible pages query immediately and every 30 seconds, as well as on page restore,
visibility return, or network recovery. Requests have a 10-second timeout and do
not overlap. Failed queries keep an unexpired notice; its server-based deadline
still hides it. Network latency is counted conservatively toward expiry. A
successful query synchronizes replacement and withdrawal, so another browser
normally sees those changes within 30 seconds while visible.

The fixed banner measures its height into `--announcement-height`. Full-height
workspaces, drawers, and Spatial views reserve the remaining viewport height.
Very long or multiline notices scroll inside the banner to keep tools usable.
Table headers inside independent scrolling containers retain their own offsets.

## Verification

Install Interface requirements and `pytest pytest-asyncio` in an isolated venv.
Run from the repository root with an isolated `POTATO_AGENT_STATE_DIR`:

```bash
python -m pytest -q interface/test_*.py
```

Browser tests are opt-in and use a temporary local server, temporary SQLite
database, real announcement and administrator routes, and mocked tool APIs.
They never start an agent runtime. Install `playwright` and its Chromium browser,
then run:

```bash
POTATO_ANNOUNCEMENT_BROWSER_TESTS=1 \
POTATO_ANNOUNCEMENT_SCREENSHOTS=/tmp/potato-announcement-screenshots \
python -m pytest -q interface/test_announcement_browser.py
```

`POTATO_PLAYWRIGHT_EXECUTABLE` can select an existing Chromium executable.
Screenshots cover the home page, workspace, mobile drawers, Spatial layout, and
admin form at 1440, 390, and 320 pixel widths. Spatial data is mocked; these tests
check layout and controls rather than scientific results.

This change adds the `site_announcement` table through the normal idempotent
Interface schema initialization. It adds no service, systemd setting, or runtime
dependency. Announcement expiry does not stop services or the server.
