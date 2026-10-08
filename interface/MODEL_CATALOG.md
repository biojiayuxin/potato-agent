# Model configuration contract

The only editable model definition is the protected runtime file
`/var/lib/potato-agent/config/model_proxy.yaml`. It is not a repository file.
Its owner/mode remain `root:potato-model-proxy 0640`. Real upstream addresses,
API keys, and the catalog signing key must not be copied into user homes,
`users_mapping.yaml`, browser responses, conversation databases, or logs.

## Schema

The catalog uses `schema_version: 2`, `primary_option_id`, `default_option_id`,
`backends`, `options`, and a persistent random `catalog_signing_key` generated
during initialization (or by the earlier transition release). Existing `listen`
settings are preserved.

Each backend defines `model`, `base_url`, `api_key`, and `api_mode`.
Each option defines `display_name`, `backend`, `reasoning_effort`, and
`context_length`. Dictionary order determines the frontend order. Multiple
options may reference the same backend while using different reasoning or
context settings. `default_option_id` explicitly chooses the default; runtime
code does not infer Fast from an upstream model name.

The initial migration chooses Fast when present and otherwise the primary
option. Later defaults follow `default_option_id` explicitly. New and imported
chats, and historical chats without a saved selection, use this default.
Branches inherit their source's selected option and then save independently.
Draft choices become durable on first submission; unsent drafts are not a
server-side saved model setting.

Stable option IDs such as `deep`, `fast`, and `deep-backup` never change when
their upstream models change. SQLite and browser APIs accept only these IDs.
User bootstrap and explicit auxiliary model settings also use these stable IDs.
The proxy accepts stable IDs and authenticated execution snapshots; bare
upstream model names and old route aliases are not accepted. Retired
`legacy_routes` / `legacy_ids` option fields fail catalog validation.

## Consumers and admission

`interface.model_catalog` validates the schema and creates public snapshots.
Interface obtains snapshots through the fixed privileged helper operation
`get-model-catalog`. This operation accepts no path or user-supplied config and
returns no addresses or keys. Browser model APIs omit the internal route token.
Other Interface clients, including daily updates, read the request API mode
from public proxy model metadata on each completion. They do not read backend
addresses or credentials.

At turn admission Interface resolves the selected option once. Gateway applies
that snapshot to the owning Agent. Main calls, continuation calls and children
inheriting that runtime use an authenticated route containing public snapshot
fields. The proxy verifies its HMAC before selecting a backend and enforcing
the frozen upstream model, API mode, and reasoning effort. Snapshot validation
does not depend on an in-memory cache, so a proxy restart does not invalidate it.

Signatures bind to the backend endpoint without exposing the endpoint or its
hash. API key rotation is supported. When changing an endpoint, create a new
backend ID and retain the old backend until admitted work has finished.
Removing a backend/option or rotating the signing key invalidates its old
routes: requests fail closed rather than silently choosing a different model.

The Lite instance adapter renders the actual upstream model and selected option
in the system prompt and runtime status. On cold recovery it invalidates an
obsolete stored prompt before inherited prompt restoration can reuse it.
Inherited Hermes orchestration and provider transports remain unchanged.

## SQLite meanings

| Store | Meaning |
| --- | --- |
| Interface `session_model_state.model_id` | Stable selected option ID |
| Interface `model_revision` | Selection revision, independent of config changes |
| Interface `session_model_runs` | Immutable public snapshot for an admitted Interface turn |
| Lite `sessions.model` | Actual upstream model for the session's latest applied snapshot |
| Lite `sessions.model_config.potato_model` | Public model identity and configuration revision |
| Lite `potato_model_runs` | Public snapshots including automatic/delegated turns |
| Proxy usage `route_model` | Stable option ID for catalog requests |
| Proxy usage `upstream_model`, `config_revision` | Actual outbound model parameter and signed configuration revision |

Historical execution records do not change when the catalog changes. Neither
snapshot table contains credentials, real endpoints, or signed route tokens.
Legacy usage/session records retain their historical meanings.

`model_revision` tracks a chat's selection, while `config_revision` identifies
the resolved configuration. Changing a backend model or reasoning setting can
change the latter without changing a chat's selected ID or selection revision.
Use proxy usage records to inspect the actual outbound model parameter. Old
assistant messages and natural-language self-reports are not configuration
records and are not rewritten by a model upgrade.

## Initialization and updates

Use `configure_model_catalog.py` for schema-v2 catalogs. The default operation
prints a public preview without API addresses, credentials, or signed routes.
Existing installations must already have `hermes.model_catalog: true` and
canonical session and user configuration option IDs before deploying this code. The earlier transition
release is required to upgrade a pre-catalog installation; this version no
longer converts old mapping/proxy formats or SQLite selection IDs.
For artifact delivery, migration ordering and rollback checkpoints, follow the
[YNNU upgrade runbook](../docs/YNNU_MODEL_UPGRADE.md).

```sh
sudo /opt/interface-env/bin/python -B /srv/potato_agent/configure_model_catalog.py \
  --check-session-db /var/lib/potato-agent/data/interface.db --check-user-configs
sudo /opt/interface-env/bin/python -B /srv/potato_agent/configure_model_catalog.py \
  --option deep --model NEW_MODEL
sudo /opt/interface-env/bin/python -B /srv/potato_agent/configure_model_catalog.py \
  --option deep --model NEW_MODEL --apply
```

Replace `NEW_MODEL` with an upstream-supported model name. Option updates also
accept `--display-name`, `--reasoning-effort`, and `--context-length`. Changing
the default, API mode, or endpoint requires a validated atomic edit of the
protected catalog; these are not CLI flags. Preview/apply validates local
configuration and does not contact the upstream to check model availability.
`--check-session-db` and `--check-user-configs` are read-only deployment checks;
they never rewrite SQLite or user files. The latter verifies bootstrap and
explicit auxiliary models against the catalog's stable IDs.

Before upgrading an installation that still uses old proxy routes, privately
back up and rewrite each referenced user configuration to its corresponding
stable ID, preserving the selected option and unrelated settings. Remove
retired alias fields from the protected catalog only when active work uses
catalog snapshots or affected runtimes have drained and restarted. Keep backend
definitions and the signing key unchanged to preserve admitted snapshots.

On an empty installation use `--initialize-from PRIVATE_FILE`, adding `--apply`
after reviewing the public preview. The input must be a protected schema-v2
file, read with the same ownership, permission, and no-symlink checks as the
runtime catalog. A missing signing key is generated securely. Initialization
refuses an existing catalog, legacy mapping definitions, or mapped users.
It creates the catalog and mapping marker without invoking an old migration
script. See section 12 of the [deployment guide](../HPC_DEPLOYMENT.md).

The command creates private configuration backups and restores previous files
on write failure. It does not back up or modify conversation databases.
An option update retains IDs and creates a new backend when changing one
option's model, so other options sharing that backend are unaffected. New turns
read the new values; admitted signed turns retain their original model and
settings. No service restart is required for ordinary model/config changes.

## Removed compatibility code and remaining dependencies

The Lite frontend uses the server's `default_id`, labels, and option order.
It does not guess defaults from old upstream names. User-wide model selection
has been removed: `/api/models/active`, its helper operations, and `active_id` /
`is_active` fields no longer exist. Clients use the session-specific model API.

`configure_model_proxy.py`, `update_fast_model.py`, their tests, old-format
parsers, the `--migrate` option, and SQLite option-ID conversion are removed.
`ModelOption` carries no endpoint/key fields or private configuration serializer.
The retired `configure_hermes_model.py` is also absent. Cutover removes deployed
copies of these retired tools and validates the new catalog contract before
stopping services. Route-alias resolution, authorization, and bootstrap matching
are removed as well; bootstrap matching never guesses an option from a shared
upstream model name.

These components still have current responsibilities:

| Component | Why it remains |
| --- | --- |
| `cleanup_hermes_user_keys.py` | Repairs private user config through the local proxy and removes stray keys. It now uses only the current catalog. |
| `migrate_model_proxy_usage.py` | The release cutover still calls it when establishing the separate usage/quota database; it does not configure models. |

The user-key repair command preserves a valid bootstrap option ID and otherwise
uses the catalog default. It does not infer an option from the upstream model,
API address, or other stale runtime settings.

The old per-user HTTP model readiness probe and disabled fallback-credential
tests have been removed. Runtime startup uses service readiness and the Gateway
handshake. Inherited Lite provider helpers remain runtime dependencies;
`hermes-agent/` remains the upstream audit and rollback baseline.

The standalone `gene_function_prediction` pipeline has its own environment-based
LLM client and is outside the Potato conversation/Gateway configuration path.
Configure that pipeline explicitly when running it.

## Rollback boundary

The release cutover backs up code and runtime state. Any catalog migration must
finish before the final cutover and has a separate pre-migration backup. Its
configuration and SQLite changes are not automatically undone by switching the
Lite symlink. Older code cannot read
schema v2 and may not recognize the new stable IDs. A rollback needs a reviewed,
matching configuration and selection-ID migration as well as the old code.
Do not restore an entire old chat database over conversations created after
cutover. Retain the protected configuration/SQLite backups and the previous
release until validation and the rollback retention period are complete.
