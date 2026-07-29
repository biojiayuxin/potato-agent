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

## Build And Verify

The source verifier uses `python -S -B -P` and explicit dependency paths to
prove that owned modules resolve from the isolated Lite tree. Builds and the
locked release artifacts target CPython 3.12 on Linux x86_64. First create and
populate the dedicated build venv and prepare a clean browser asset tree as
documented in sections 6.1 and 6.2 of the root README, then run from
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
```

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
executed.

## Deployment Status

Production runs immutable Lite releases through
`/opt/potato-hermes-lite/current`. Production currently runs
`0.19.0+potato.lite.3` from release
`20260729T070500Z-0.19.0-potato.lite.3-f2336202`; the security cutover and
host-specific requirements are documented in the root README. Its approval protocol carries an exact request ID from
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
