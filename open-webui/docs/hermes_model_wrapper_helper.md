# Hermes per-user model wrapper helper

This repository now includes a backend helper that generates import-ready Open WebUI model wrapper JSON from a YAML user mapping.

Helper path:
- `backend/open_webui/tools/hermes_model_wrapper_helper.py`

What it does:
- Generates one wrapper model per target user.
- Keeps compatibility with Open WebUI model concepts:
  - `id`
  - `base_model_id`
  - `name`
  - `meta`
  - `params`
  - `access_grants`
  - `is_active`
- Produces JSON shaped for the existing `/api/v1/models/import` endpoint.
- Supports private per-user access grants, plus optional shared read/write grants for extra users or groups.

Typical use case:
- Each Hermes instance is exposed through its own Open WebUI connection.
- Each connection should set a unique `prefix_id` such as `hermes-alice` or `hermes-bob`.
- Hermes can then advertise the same upstream model name `Hermes` on every connection without collisions.
- You generate separate Open WebUI wrapper entries per user so each person gets a distinct model id and private access grants while still seeing the shared display name `Hermes`.

## Input YAML schema

Top-level fields:
- `base_model_id` (required): upstream model id every wrapper should point to by default.
- `base_display_name` (optional): display name token used in generated wrapper names.
- `owner_user_id` (optional): metadata only, included in `--format full` output.
- `model_id_prefix` (optional): prefix used when generating wrapper ids. Default: `hermes-`.
- `model_id_template` (optional): custom id template using `{target_label}`, `{target_slug}`, `{base_model_id}`.
- `model_name_template` (optional): custom name template using `{target_label}`, `{base_name}`. Default: `Hermes`.
- `description_template` (optional): default meta description template using `{target_label}`.
- `meta_defaults` (optional): metadata merged into every wrapper.
- `params_defaults` (optional): params merged into every wrapper.
- `users` (required): list of wrapper targets.

Per-user fields:
- `user_id` (required): Open WebUI target user id for access grants.
- `label`, `username`, `email` (optional): used to build names/slugs.
- `slug` (optional): explicit slug for generated ids.
- `model_id` (optional): explicit wrapper id.
- `name` (optional): explicit wrapper display name.
- `base_model_id` (optional): override upstream target for only this wrapper.
- `owner_user_id` (optional): metadata only, included in `--format full` output.
- `is_active` (optional): defaults to `true`.
- `meta` (optional): merged into wrapper `meta`.
- `params` (optional): merged into wrapper `params`.
- `include_target_user_read` (optional): defaults to `true`.
- `include_target_user_write` (optional): defaults to `false`.
- `read_user_ids`, `write_user_ids` (optional): extra user grants.
- `read_group_ids`, `write_group_ids` (optional): extra group grants.

## Example

See:
- `docs/examples/hermes_model_wrapper_mapping.example.yaml`

## Dry-run validation

From repo root:

`python backend/open_webui/tools/hermes_model_wrapper_helper.py docs/examples/hermes_model_wrapper_mapping.example.yaml --dry-run`

This validates the YAML and prints a wrapper summary without emitting JSON.

## Generate import-ready JSON

`python backend/open_webui/tools/hermes_model_wrapper_helper.py docs/examples/hermes_model_wrapper_mapping.example.yaml --pretty --output /tmp/hermes-wrappers.json`

That output is ready for the existing import API because it emits:

`{"models": [...]}`

with each model containing:
- `id`
- `base_model_id`
- `name`
- `meta`
- `params`
- `access_grants`
- `is_active`

Note:
- `user_id` is intentionally omitted from default `import` output because `/api/v1/models/import` creates imported models under the importing user.
- If you want a fuller planning/export view including metadata owner hints, use `--format full`.

## Import into Open WebUI

Option 1: API

`curl -X POST "$OPEN_WEBUI_URL/api/v1/models/import" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data @/tmp/hermes-wrappers.json`

Option 2: Admin UI
- Open the model import flow in Open WebUI.
- Paste or upload the generated JSON payload.

## Access-grant behavior

The helper emits Open WebUI-native access grants such as:

`{"principal_type":"user","principal_id":"user_123","permission":"read"}`

and optionally group grants like:

`{"principal_type":"group","principal_id":"team-hermes-reviewers","permission":"read"}`

By default, each user entry gets:
- target user `read`
- no target user `write`

If you want the target user to manage the wrapper too, set:
- `include_target_user_write: true`

## Suggested workflow

1. Configure one Open WebUI connection per Hermes instance and give each connection a unique `prefix_id`.
2. Confirm the prefixed base model ids visible to Open WebUI, for example `hermes-alice.Hermes`.
3. Create a YAML mapping from users to wrapper ids/names/grants.
4. Run the helper with `--dry-run`.
5. Generate JSON with `--pretty --output ...`.
6. Import the JSON through `/api/v1/models/import`.
7. Verify the new wrapper ids appear and that access behaves as expected for target users.

## Limitations

- The helper generates/imports wrapper definitions; it does not directly query or modify the Open WebUI database.
- Imported models are owned by whichever user performs the import, matching current Open WebUI import behavior.
- `owner_user_id` in `--format full` output is informational unless you build a separate DB-side migration flow.
