---
sidebar_position: 14
title: "API Server"
description: "Expose hermes-agent as an OpenAI-compatible API for any frontend"
---

# API Server

The API server exposes hermes-agent as an OpenAI-compatible HTTP endpoint. Any frontend that speaks the OpenAI format — Open WebUI, LobeChat, LibreChat, NextChat, ChatBox, and hundreds more — can connect to hermes-agent and use it as a backend.

Your agent handles requests with its full toolset (terminal, file operations, web search, memory, skills) and returns the final response. When streaming, tool progress indicators appear inline so frontends can show what the agent is doing.

## Quick Start

### 1. Enable the API server

Add to `~/.hermes/.env`:

```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=change-me-local-dev
# Optional: only if a browser must call Hermes directly
# API_SERVER_CORS_ORIGINS=http://localhost:3000
```

### 2. Start the gateway

```bash
hermes gateway
```

You'll see:

```
[API Server] API server listening on http://127.0.0.1:8642
```

### 3. Connect a frontend

Point any OpenAI-compatible client at `http://localhost:8642/v1`:

```bash
# Test with curl
curl http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "Content-Type: application/json" \
  -d '{"model": "hermes-agent", "messages": [{"role": "user", "content": "Hello!"}]}'
```

Or connect Open WebUI, LobeChat, or any other frontend — see the [Open WebUI integration guide](/docs/user-guide/messaging/open-webui) for step-by-step instructions.

## Endpoints

### POST /v1/chat/completions

Standard OpenAI Chat Completions format. Stateless — the full conversation is included in each request via the `messages` array.

**Request:**
```json
{
  "model": "hermes-agent",
  "messages": [
    {"role": "system", "content": "You are a Python expert."},
    {"role": "user", "content": "Write a fibonacci function"}
  ],
  "stream": false
}
```

**Response:**
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "hermes-agent",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Here's a fibonacci function..."},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 50, "completion_tokens": 200, "total_tokens": 250}
}
```

**Streaming** (`"stream": true`): Returns Server-Sent Events (SSE) with token-by-token response chunks. When streaming is enabled in config, tokens are emitted live as the LLM generates them. When disabled, the full response is sent as a single SSE chunk.

**Tool progress in streams**: When the agent calls tools during a streaming request, brief progress indicators are injected into the content stream as the tools start executing (e.g. `` `💻 pwd` ``, `` `🔍 Python docs` ``). These appear as inline markdown before the agent's response text, giving frontends like Open WebUI real-time visibility into tool execution.

### POST /v1/responses

OpenAI Responses API format. Supports server-side conversation state via `previous_response_id` — the server stores full conversation history (including tool calls and results) so multi-turn context is preserved without the client managing it.

**Request:**
```json
{
  "model": "hermes-agent",
  "input": "What files are in my project?",
  "instructions": "You are a helpful coding assistant.",
  "store": true
}
```

**Response:**
```json
{
  "id": "resp_abc123",
  "object": "response",
  "status": "completed",
  "model": "hermes-agent",
  "output": [
    {"type": "function_call", "name": "terminal", "arguments": "{\"command\": \"ls\"}", "call_id": "call_1"},
    {"type": "function_call_output", "call_id": "call_1", "output": "README.md src/ tests/"},
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Your project has..."}]}
  ],
  "usage": {"input_tokens": 50, "output_tokens": 200, "total_tokens": 250}
}
```

#### Multi-turn with previous_response_id

Chain responses to maintain full context (including tool calls) across turns:

```json
{
  "input": "Now show me the README",
  "previous_response_id": "resp_abc123"
}
```

The server reconstructs the full conversation from the stored response chain — all previous tool calls and results are preserved.

#### Named conversations

Use the `conversation` parameter instead of tracking response IDs:

```json
{"input": "Hello", "conversation": "my-project"}
{"input": "What's in src/?", "conversation": "my-project"}
{"input": "Run the tests", "conversation": "my-project"}
```

The server automatically chains to the latest response in that conversation. Like the `/title` command for gateway sessions.

### GET /v1/responses/\{id\}

Retrieve a previously stored response by ID.

### DELETE /v1/responses/\{id\}

Delete a stored response.

### GET /v1/models

Lists the agent as an available model. The advertised model name defaults to the [profile](/docs/user-guide/features/profiles) name (or `hermes-agent` for the default profile). Required by most frontends for model discovery.

### GET /health

Health check. Returns `{"status": "ok"}`. Also available at **GET /v1/health** for OpenAI-compatible clients that expect the `/v1/` prefix.

## System Prompt Handling

When a frontend sends a `system` message (Chat Completions) or `instructions` field (Responses API), hermes-agent **layers it on top** of its core system prompt. Your agent keeps all its tools, memory, and skills — the frontend's system prompt adds extra instructions.

This means you can customize behavior per-frontend without losing capabilities:
- Open WebUI system prompt: "You are a Python expert. Always include type hints."
- The agent still has terminal, file tools, web search, memory, etc.

## Authentication

Bearer token auth via the `Authorization` header:

```
Authorization: Bearer ***
```

Configure the key via `API_SERVER_KEY` env var. If you need a browser to call Hermes directly, also set `API_SERVER_CORS_ORIGINS` to an explicit allowlist.

:::warning Security
The API server gives full access to hermes-agent's toolset, **including terminal commands**. When binding to a non-loopback address like `0.0.0.0`, `API_SERVER_KEY` is **required**. Also keep `API_SERVER_CORS_ORIGINS` narrow to control browser access.

The default bind address (`127.0.0.1`) is for local-only use. Browser access is disabled by default; enable it only for explicit trusted origins.
:::

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_SERVER_ENABLED` | `false` | Enable the API server |
| `API_SERVER_PORT` | `8642` | HTTP server port |
| `API_SERVER_HOST` | `127.0.0.1` | Bind address (localhost only by default) |
| `API_SERVER_KEY` | _(none)_ | Bearer token for auth |
| `API_SERVER_CORS_ORIGINS` | _(none)_ | Comma-separated allowed browser origins |
| `API_SERVER_MODEL_NAME` | _(profile name)_ | Model name on `/v1/models`. Defaults to profile name, or `hermes-agent` for default profile. |

### config.yaml

```yaml
# Not yet supported — use environment variables.
# config.yaml support coming in a future release.
```

## Security Headers

All responses include security headers:
- `X-Content-Type-Options: nosniff` — prevents MIME type sniffing
- `Referrer-Policy: no-referrer` — prevents referrer leakage

## CORS

The API server does **not** enable browser CORS by default.

For direct browser access, set an explicit allowlist:

```bash
API_SERVER_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

When CORS is enabled:
- **Preflight responses** include `Access-Control-Max-Age: 600` (10 minute cache)
- **SSE streaming responses** include CORS headers so browser EventSource clients work correctly
- **`Idempotency-Key`** is an allowed request header — clients can send it for deduplication (responses are cached by key for 5 minutes)

Most documented frontends such as Open WebUI connect server-to-server and do not need CORS at all.

## Compatible Frontends

Any frontend that supports the OpenAI API format works. Tested/documented integrations:

| Frontend | Stars | Connection |
|----------|-------|------------|
| [Open WebUI](/docs/user-guide/messaging/open-webui) | 126k | Full guide available |
| LobeChat | 73k | Custom provider endpoint |
| LibreChat | 34k | Custom endpoint in librechat.yaml |
| AnythingLLM | 56k | Generic OpenAI provider |
| NextChat | 87k | BASE_URL env var |
| ChatBox | 39k | API Host setting |
| Jan | 26k | Remote model config |
| HF Chat-UI | 8k | OPENAI_BASE_URL |
| big-AGI | 7k | Custom endpoint |
| OpenAI Python SDK | — | `OpenAI(base_url="http://localhost:8642/v1")` |
| curl | — | Direct HTTP requests |

## Multi-User Setup with Linux Accounts

For a real multi-user Open WebUI deployment, run one Hermes API server per user and bind each instance to a dedicated Linux account.

The production isolation boundary should be:
- one Open WebUI user
- one Linux user such as `hmx_alice`
- one Hermes home such as `/home/hmx_alice/.hermes`
- one workdir such as `/home/hmx_alice/work`
- one systemd unit such as `hermes-alice.service`

Hermes already has the isolation primitives needed for this:
- `HERMES_HOME` scopes config, sessions, memory, logs, and skills.
- `HERMES_HOME/home/` becomes subprocess `HOME`, isolating git/ssh/gh/npm state.
- `terminal.cwd` sets the default workspace for tool calls.
- Running the service as `User=hmx_alice` gives the real filesystem and process-level isolation.

### Fast path: generate a deployment bundle

Use the helper script with a single users-mapping YAML:

```bash
python3 scripts/generate_openwebui_multiuser.py \
  --mapping /path/to/users_mapping.yaml \
  --output-dir /tmp/hermes-multiuser
```

Example output includes:
- per-user `.env`
- per-user `config.yaml`
- per-user systemd units with `User=<linux_user>`
- a root-only `apply_host.sh`

The generated `.env` files can advertise the same model name `Hermes` on every Hermes instance. To avoid collisions inside Open WebUI, configure a unique Open WebUI connection `prefix_id` per connection, such as `hermes-alice` and `hermes-bob`.

### Manual setup

```bash
# Create one Linux user per WebUI user
useradd -m -s /bin/bash hmx_alice
useradd -m -s /bin/bash hmx_bob

mkdir -p /home/hmx_alice/work /home/hmx_bob/work
mkdir -p /home/hmx_alice/.hermes/home /home/hmx_bob/.hermes/home
chown -R hmx_alice:hmx_alice /home/hmx_alice
chown -R hmx_bob:hmx_bob /home/hmx_bob
chmod 700 /home/hmx_alice /home/hmx_bob
chmod 700 /home/hmx_alice/work /home/hmx_bob/work

cat > /home/hmx_alice/.hermes/.env <<'EOF'
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8643
API_SERVER_KEY=alice-secret
API_SERVER_MODEL_NAME=Hermes
EOF

cat > /home/hmx_bob/.hermes/.env <<'EOF'
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8644
API_SERVER_KEY=bob-secret
API_SERVER_MODEL_NAME=Hermes
EOF
```

Each user also needs a `config.yaml` with `terminal.cwd` pointing at their own workdir.

### systemd per user

```bash
/etc/systemd/system/hermes-alice.service

[Unit]
Description=Hermes Agent for Alice
After=network.target

[Service]
Type=simple
User=hmx_alice
Group=hmx_alice
WorkingDirectory=/home/hmx_alice
Environment=HOME=/home/hmx_alice
Environment=HERMES_HOME=/home/hmx_alice/.hermes
ExecStart=/usr/local/bin/hermes gateway run --replace
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Duplicate that for Bob with `hmx_bob`, `8644`, and `hermes-bob.service`, then:

```bash
systemctl daemon-reload
systemctl enable --now hermes-alice.service hermes-bob.service
```

Why this matters: `terminal.cwd` controls the default workspace, but only `User=hmx_alice` gives operating-system-level protection against cross-user reads and writes.

### Open WebUI connections

Add one OpenAI-compatible connection per user in Open WebUI:
- Alice → `http://host.docker.internal:8643/v1` with `alice-secret`, `prefix_id=hermes-alice`
- Bob → `http://host.docker.internal:8644/v1` with `bob-secret`, `prefix_id=hermes-bob`

Each Hermes instance can advertise the same `/v1/models` id `Hermes`, and Open WebUI will resolve them as:
- `hermes-alice.Hermes`
- `hermes-bob.Hermes`

Those prefixed base model ids are what your private wrapper models should point at. The wrappers can then use unique ids like `hermes-alice` and `hermes-bob` while both display the same user-facing name `Hermes`.

### Operational notes

- Keep `API_SERVER_HOST=127.0.0.1` unless you intentionally need remote network access.
- Use a distinct `API_SERVER_KEY` per profile so one leaked key only affects one user.
- Keep `API_SERVER_HOST=127.0.0.1` unless you intentionally need remote network access.
- Use a distinct `API_SERVER_KEY` per user.
- Use a unique Open WebUI `prefix_id` per connection whenever multiple Hermes instances advertise the same base model name.
- The helper script is dry-run by default and can also write a deployment bundle with `--output-dir`.


## Limitations

- **Response storage** — stored responses (for `previous_response_id`) are persisted in SQLite and survive gateway restarts. Max 100 stored responses (LRU eviction).
- **No file upload** — vision/document analysis via uploaded files is not yet supported through the API.
- **Model field is cosmetic** — the `model` field in requests is accepted but the actual LLM model used is configured server-side in config.yaml.
