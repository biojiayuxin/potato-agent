#!/usr/bin/env python3
"""Generate Open WebUI model wrapper import payloads from a per-user YAML map.

This helper creates private per-user wrapper models that point at an existing
base model via `base_model_id`, and optionally includes explicit access grants
for the target user and/or groups. The output can be imported through the
existing `/api/v1/models/import` endpoint or reviewed as JSON first.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PROFILE_IMAGE_URL = '/static/favicon.png'
DEFAULT_DESCRIPTION_TEMPLATE = 'Private Hermes wrapper for {target_label}'
DEFAULT_MODEL_ID_PREFIX = 'hermes-'
DEFAULT_MODEL_NAME_TEMPLATE = 'Hermes'


class ConfigError(ValueError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open('r', encoding='utf-8') as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f'Mapping file not found: {path}') from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f'Invalid YAML in {path}: {exc}') from exc

    if not isinstance(data, dict):
        raise ConfigError('Top-level YAML structure must be a mapping/object.')
    return data


def slugify(value: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', value.strip().lower())
    slug = re.sub(r'-+', '-', slug).strip('-')
    if not slug:
        raise ConfigError(f'Could not derive a slug from value: {value!r}')
    return slug


def ensure_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ConfigError(f'{field_name} must be a list when provided.')


def unique_grants(grants: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, str]] = []
    for grant in grants:
        key = (grant['principal_type'], grant['principal_id'], grant['permission'])
        if key in seen:
            continue
        seen.add(key)
        result.append(grant)
    return result


def build_access_grants(entry: dict[str, Any], target_user_id: str) -> list[dict[str, str]]:
    grants: list[dict[str, str]] = []

    include_target_user_read = entry.get('include_target_user_read', True)
    include_target_user_write = entry.get('include_target_user_write', False)

    if include_target_user_read:
        grants.append(
            {
                'principal_type': 'user',
                'principal_id': target_user_id,
                'permission': 'read',
            }
        )
    if include_target_user_write:
        grants.append(
            {
                'principal_type': 'user',
                'principal_id': target_user_id,
                'permission': 'write',
            }
        )

    for user_id in ensure_list(entry.get('read_user_ids'), 'read_user_ids'):
        grants.append(
            {
                'principal_type': 'user',
                'principal_id': str(user_id),
                'permission': 'read',
            }
        )

    for user_id in ensure_list(entry.get('write_user_ids'), 'write_user_ids'):
        grants.append(
            {
                'principal_type': 'user',
                'principal_id': str(user_id),
                'permission': 'write',
            }
        )

    for group_id in ensure_list(entry.get('read_group_ids'), 'read_group_ids'):
        grants.append(
            {
                'principal_type': 'group',
                'principal_id': str(group_id),
                'permission': 'read',
            }
        )

    for group_id in ensure_list(entry.get('write_group_ids'), 'write_group_ids'):
        grants.append(
            {
                'principal_type': 'group',
                'principal_id': str(group_id),
                'permission': 'write',
            }
        )

    return unique_grants(grants)


def build_meta(config: dict[str, Any], entry: dict[str, Any], target_label: str) -> dict[str, Any]:
    meta_defaults = deepcopy(config.get('meta_defaults') or {})
    if not isinstance(meta_defaults, dict):
        raise ConfigError('meta_defaults must be an object/mapping when provided.')

    meta = deepcopy(meta_defaults)
    entry_meta = entry.get('meta') or {}
    if not isinstance(entry_meta, dict):
        raise ConfigError('entry.meta must be an object/mapping when provided.')
    meta.update(entry_meta)

    meta.setdefault('profile_image_url', DEFAULT_PROFILE_IMAGE_URL)
    description_template = config.get('description_template') or DEFAULT_DESCRIPTION_TEMPLATE
    meta.setdefault('description', description_template.format(target_label=target_label))
    return meta


def build_params(config: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    params_defaults = deepcopy(config.get('params_defaults') or {})
    if not isinstance(params_defaults, dict):
        raise ConfigError('params_defaults must be an object/mapping when provided.')

    params = deepcopy(params_defaults)
    entry_params = entry.get('params') or {}
    if not isinstance(entry_params, dict):
        raise ConfigError('entry.params must be an object/mapping when provided.')
    params.update(entry_params)
    return params


def build_model_id(prefix: str, template: str | None, target_label: str, target_slug: str, base_model_id: str) -> str:
    if template:
        model_id = template.format(target_label=target_label, target_slug=target_slug, base_model_id=base_model_id)
    else:
        model_id = f'{prefix}{target_slug}'

    if len(model_id) > 256:
        raise ConfigError(f'Generated model id exceeds 256 characters: {model_id}')
    return model_id


def build_name(template: str, target_label: str, base_display_name: str) -> str:
    return template.format(target_label=target_label, base_name=base_display_name)


def generate_payload(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    base_model_id = config.get('base_model_id')
    if not isinstance(base_model_id, str) or not base_model_id:
        raise ConfigError('base_model_id is required and must be a non-empty string.')

    users = config.get('users')
    if not isinstance(users, list) or not users:
        raise ConfigError('users is required and must be a non-empty list.')

    model_id_prefix = str(config.get('model_id_prefix', DEFAULT_MODEL_ID_PREFIX))
    model_id_template = config.get('model_id_template')
    if model_id_template is not None and not isinstance(model_id_template, str):
        raise ConfigError('model_id_template must be a string when provided.')

    model_name_template = str(config.get('model_name_template', DEFAULT_MODEL_NAME_TEMPLATE))
    base_display_name = str(config.get('base_display_name', base_model_id))
    owner_user_id = str(config.get('owner_user_id', 'system'))

    payload_models: list[dict[str, Any]] = []
    seen_model_ids: set[str] = set()

    for index, entry in enumerate(users, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f'users[{index}] must be an object/mapping.')

        target_user_id = entry.get('user_id')
        if not isinstance(target_user_id, str) or not target_user_id:
            raise ConfigError(f'users[{index}].user_id is required and must be a non-empty string.')

        target_label = str(entry.get('label') or entry.get('username') or entry.get('email') or target_user_id)
        target_slug = str(entry.get('slug') or slugify(target_label))

        model_id = str(
            entry.get('model_id')
            or build_model_id(model_id_prefix, model_id_template, target_label, target_slug, base_model_id)
        )
        if model_id in seen_model_ids:
            raise ConfigError(f'Duplicate generated model id: {model_id}')
        seen_model_ids.add(model_id)

        model_name = str(entry.get('name') or build_name(model_name_template, target_label, base_display_name))
        payload_models.append(
            {
                'id': model_id,
                'base_model_id': str(entry.get('base_model_id') or base_model_id),
                'name': model_name,
                'meta': build_meta(config, entry, target_label),
                'params': build_params(config, entry),
                'access_grants': build_access_grants(entry, target_user_id),
                'is_active': bool(entry.get('is_active', True)),
                'user_id': str(entry.get('owner_user_id') or owner_user_id),
            }
        )

    return {'models': payload_models}


def build_import_payload(payload: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    models: list[dict[str, Any]] = []
    for model in payload['models']:
        import_model = deepcopy(model)
        import_model.pop('user_id', None)
        models.append(import_model)
    return {'models': models}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Generate private per-user Hermes model wrappers for Open WebUI from YAML.'
    )
    parser.add_argument('mapping', type=Path, help='Path to the mapping YAML file.')
    parser.add_argument(
        '--format',
        choices=('import', 'full'),
        default='import',
        help='Output import-ready payload or full payload including user_id metadata.',
    )
    parser.add_argument(
        '--output',
        type=Path,
        help='Write JSON output to this path instead of stdout.',
    )
    parser.add_argument(
        '--pretty',
        action='store_true',
        help='Pretty-print JSON output.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate and summarize the generated wrappers instead of emitting JSON.',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_yaml(args.mapping)
        full_payload = generate_payload(config)
        output_payload = full_payload if args.format == 'full' else build_import_payload(full_payload)
    except ConfigError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    if args.dry_run:
        print(f'Generated {len(full_payload["models"])} wrapper model(s) from {args.mapping}')
        for model in full_payload['models']:
            grant_summary = (
                ', '.join(
                    f'{grant["principal_type"]}:{grant["principal_id"]}:{grant["permission"]}'
                    for grant in model['access_grants']
                )
                or 'owner-only'
            )
            print(f'- {model["id"]} -> base={model["base_model_id"]} name={model["name"]!r} grants=[{grant_summary}]')
        return 0

    json_kwargs = {'ensure_ascii': False}
    if args.pretty:
        json_kwargs['indent'] = 2

    rendered = json.dumps(output_payload, **json_kwargs)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    else:
        print(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
