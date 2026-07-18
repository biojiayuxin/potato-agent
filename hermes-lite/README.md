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
prove that owned modules resolve from the isolated Lite tree:

```bash
python3 scripts/verify_lite.py \
  --python /opt/hermes-agent-venv/bin/python3

python3 scripts/build_lite_release.py \
  --dry-run \
  --python /opt/hermes-agent-venv/bin/python3

python3 scripts/build_lite_release.py \
  --python /opt/hermes-agent-venv/bin/python3 \
  --output /tmp/potato-hermes-lite-release
```

The legacy venv in these examples supplies pinned dependencies only. It is not
a source root. Use the Lite release venv through `--python` once that venv is
available.

Focused tests live in `tests/`, `tests_packaging/`, and `tests_e2e/`. The mock
provider E2E starts the real stdio gateway with isolated `HOME` and
`HERMES_HOME`; it covers prompt completion, resume, interrupt, and approval
denial and expiration without contacting a real provider. The expiration case
also verifies that a late response is rejected and the guarded command is not
executed.

## Deployment Status

Production runs immutable Lite releases through
`/opt/potato-hermes-lite/current`. The current release is
`0.16.0+potato.lite.4`; its approval protocol carries an exact request ID from
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
