#!/usr/bin/env python3
"""Generate per-user Hermes deployment artifacts for Open WebUI.

The preferred workflow is to read a single users-mapping YAML and generate
Linux-user-based deployment artifacts. A legacy `--user` mode is kept for quick
experiments, but it only provides profile/cwd isolation and does not create the
strong Linux-account isolation required for production multi-user deployments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hermes_cli.profiles import _PROFILE_ID_RE

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8643
DEFAULT_MODEL_NAME = "Hermes"
DEFAULT_HERMES_BIN = "/usr/local/bin/hermes"
DEFAULT_WORKSPACE_BASE = Path("/home")
DEFAULT_TIMEOUT = 180
ENV_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class CliError(ValueError):
    pass


def _get_env_placeholder_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = ENV_PLACEHOLDER_RE.fullmatch(value.strip())
    if match is None:
        return None
    return match.group(1)


def _resolve_env_placeholders(value: Any, field_name: str = "mapping") -> Any:
    if isinstance(value, dict):
        return {
            key: _resolve_env_placeholders(subvalue, f"{field_name}.{key}")
            for key, subvalue in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_env_placeholders(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]

    env_name = _get_env_placeholder_name(value)
    if env_name is None:
        return value

    env_value = os.getenv(env_name)
    if not env_value:
        raise CliError(
            f"{field_name} references environment variable {env_name!r}, but it is not set"
        )
    return env_value


@dataclass(frozen=True)
class UserSpec:
    username: str
    linux_user: str
    home_dir: Path
    hermes_home: Path
    workdir: Path
    port: int
    api_key: str
    host: str
    model_name: str
    systemd_service: str

    @property
    def profile(self) -> str:
        return self.username

    @property
    def env_path(self) -> Path:
        return self.hermes_home / ".env"

    @property
    def config_path(self) -> Path:
        return self.hermes_home / "config.yaml"

    @property
    def service_path(self) -> Path:
        return Path("/etc/systemd/system") / self.systemd_service


def _shell_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def _parse_user_entry(raw: str) -> tuple[str, str | None]:
    raw = raw.strip()
    if not raw:
        raise CliError("empty --user entry")
    if ":" in raw:
        name, api_key = raw.split(":", 1)
        name = name.strip()
        api_key = api_key.strip() or None
    else:
        name, api_key = raw, None
    if not _PROFILE_ID_RE.match(name):
        raise CliError(
            f"invalid profile/user name {name!r}; expected [a-z0-9][a-z0-9_-]{{0,63}}"
        )
    return name, api_key


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise CliError(f"{field_name} must be a mapping/object")


def _require_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise CliError(f"{field_name} must be a list")


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:
        raise CliError(f"mapping file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise CliError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise CliError("top-level YAML structure must be a mapping/object")
    return _resolve_env_placeholders(data, str(path))


def _build_specs_from_mapping(mapping: dict[str, Any]) -> list[UserSpec]:
    users = _require_list(mapping.get("users"), "users")
    if not users:
        raise CliError("users must be a non-empty list")

    hermes_cfg = _require_mapping(mapping.get("hermes"), "hermes")
    default_host = str(hermes_cfg.get("api_server_host") or DEFAULT_HOST)
    default_model_name = str(
        hermes_cfg.get("api_server_model_name") or DEFAULT_MODEL_NAME
    )
    start_port = int(mapping.get("start_port") or DEFAULT_PORT)

    specs: list[UserSpec] = []
    seen_ports: set[int] = set()

    for index, raw_user in enumerate(users, start=1):
        if not isinstance(raw_user, dict):
            raise CliError(f"users[{index}] must be a mapping/object")

        username = str(raw_user.get("username") or "").strip()
        if not _PROFILE_ID_RE.match(username):
            raise CliError(f"users[{index}].username must be a valid profile id")

        linux_user = str(raw_user.get("linux_user") or f"hmx_{username}")
        home_dir = Path(str(raw_user.get("home_dir") or f"/home/{linux_user}"))
        hermes_home = Path(str(raw_user.get("hermes_home") or (home_dir / ".hermes")))
        workdir = Path(str(raw_user.get("workdir") or (home_dir / "work")))
        port = int(raw_user.get("api_port") or (start_port + index - 1))
        if port in seen_ports:
            raise CliError(f"duplicate api_port: {port}")
        seen_ports.add(port)

        specs.append(
            UserSpec(
                username=username,
                linux_user=linux_user,
                home_dir=home_dir,
                hermes_home=hermes_home,
                workdir=workdir,
                port=port,
                api_key=str(raw_user.get("api_key") or secrets.token_urlsafe(24)),
                host=str(raw_user.get("api_server_host") or default_host),
                model_name=str(
                    raw_user.get("api_server_model_name") or default_model_name
                ),
                systemd_service=str(
                    raw_user.get("systemd_service") or f"hermes-{username}.service"
                ),
            )
        )

    return specs


def _build_specs_from_legacy_users(
    user_entries: Iterable[str],
    *,
    start_port: int,
    host: str,
    workspace_base: Path,
) -> list[UserSpec]:
    specs: list[UserSpec] = []
    seen_names: set[str] = set()
    next_port = start_port

    for raw in user_entries:
        name, provided_key = _parse_user_entry(raw)
        if name in seen_names:
            raise CliError(f"duplicate user/profile name: {name}")
        seen_names.add(name)
        linux_user = f"hmx_{name}"
        home_dir = workspace_base / linux_user
        specs.append(
            UserSpec(
                username=name,
                linux_user=linux_user,
                home_dir=home_dir,
                hermes_home=home_dir / ".hermes",
                workdir=home_dir / "work",
                port=next_port,
                api_key=provided_key or secrets.token_urlsafe(24),
                host=host,
                model_name=DEFAULT_MODEL_NAME,
                systemd_service=f"hermes-{name}.service",
            )
        )
        next_port += 1

    if not specs:
        raise CliError("at least one --user NAME or --user NAME:API_KEY is required")
    return specs


def _env_lines(spec: UserSpec) -> list[str]:
    return [
        "API_SERVER_ENABLED=true",
        f"API_SERVER_HOST={spec.host}",
        f"API_SERVER_PORT={spec.port}",
        f"API_SERVER_KEY={spec.api_key}",
        f"API_SERVER_MODEL_NAME={spec.model_name}",
    ]


def _config_yaml(spec: UserSpec) -> str:
    payload = {
        "terminal": {
            "backend": "local",
            "cwd": str(spec.workdir),
            "timeout": DEFAULT_TIMEOUT,
        }
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)


def _systemd_unit(spec: UserSpec, hermes_bin: str) -> str:
    return "\n".join(
        [
            "[Unit]",
            f"Description=Hermes Agent for {spec.username}",
            "After=network.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={spec.linux_user}",
            f"Group={spec.linux_user}",
            f"WorkingDirectory={spec.home_dir}",
            f"Environment=HOME={spec.home_dir}",
            f"Environment=HERMES_HOME={spec.hermes_home}",
            f"ExecStart={hermes_bin} gateway run --replace",
            "Restart=always",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def _write_deployment_bundle(
    specs: list[UserSpec], output_dir: Path, hermes_bin: str
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    users_dir = output_dir / "users"
    systemd_dir = output_dir / "systemd"
    users_dir.mkdir(parents=True, exist_ok=True)
    systemd_dir.mkdir(parents=True, exist_ok=True)

    for spec in specs:
        user_dir = users_dir / spec.username / ".hermes"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / ".env").write_text(
            "\n".join(_env_lines(spec)) + "\n", encoding="utf-8"
        )
        (user_dir / "config.yaml").write_text(_config_yaml(spec), encoding="utf-8")
        (systemd_dir / spec.systemd_service).write_text(
            _systemd_unit(spec, hermes_bin),
            encoding="utf-8",
        )

    apply_script = output_dir / "apply_host.sh"
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
        '[[ "$(id -u)" -eq 0 ]] || { echo "Run as root" >&2; exit 1; }',
        "",
    ]
    for spec in specs:
        rel_env = f"users/{spec.username}/.hermes/.env"
        rel_config = f"users/{spec.username}/.hermes/config.yaml"
        rel_service = f"systemd/{spec.systemd_service}"
        lines.extend(
            [
                f"id -u {_shell_quote(spec.linux_user)} >/dev/null 2>&1 || useradd -m -s /bin/bash {_shell_quote(spec.linux_user)}",
                f"install -d -m 700 -o {_shell_quote(spec.linux_user)} -g {_shell_quote(spec.linux_user)} {_shell_quote(spec.home_dir)}",
                f"install -d -m 700 -o {_shell_quote(spec.linux_user)} -g {_shell_quote(spec.linux_user)} {_shell_quote(spec.workdir)}",
                f"install -d -m 700 -o {_shell_quote(spec.linux_user)} -g {_shell_quote(spec.linux_user)} {_shell_quote(spec.hermes_home)}",
                f"install -d -m 700 -o {_shell_quote(spec.linux_user)} -g {_shell_quote(spec.linux_user)} {_shell_quote(spec.hermes_home / 'home')}",
                f'install -m 600 -o {_shell_quote(spec.linux_user)} -g {_shell_quote(spec.linux_user)} "$SCRIPT_DIR/{rel_env}" {_shell_quote(spec.env_path)}',
                f'install -m 600 -o {_shell_quote(spec.linux_user)} -g {_shell_quote(spec.linux_user)} "$SCRIPT_DIR/{rel_config}" {_shell_quote(spec.config_path)}',
                f'install -m 644 "$SCRIPT_DIR/{rel_service}" {_shell_quote(spec.service_path)}',
                "",
            ]
        )
    lines.append("systemctl daemon-reload")
    for spec in specs:
        lines.append(f"systemctl enable --now {_shell_quote(spec.systemd_service)}")
    lines.append("")
    apply_script.write_text("\n".join(lines), encoding="utf-8")
    apply_script.chmod(0o755)


def _print_plan(specs: list[UserSpec], *, hermes_bin: str, legacy_mode: bool) -> None:
    if legacy_mode:
        print("# Legacy quick mode")
        print(
            "# This mode is convenient for dry-runs but does NOT provide Linux-account isolation."
        )
        print("# For production, use --mapping and generated systemd units instead.")
        print()
    else:
        print("# Hermes multi-user deployment plan")
        print(
            "# This plan assumes one Linux account and one systemd unit per Open WebUI user."
        )
        print()

    for spec in specs:
        print(f"## {spec.username}")
        print(f"linux_user: {spec.linux_user}")
        print(f"home_dir: {spec.home_dir}")
        print(f"hermes_home: {spec.hermes_home}")
        print(f"workdir: {spec.workdir}")
        print(f"api_url: http://{spec.host}:{spec.port}/v1")
        print(f"api_key: {spec.api_key}")
        print(f"advertised_model_name: {spec.model_name}")
        print(f"systemd_service: {spec.systemd_service}")
        print()
        print(f"useradd -m -s /bin/bash {_shell_quote(spec.linux_user)}")
        print(
            f"install -d -m 700 -o {_shell_quote(spec.linux_user)} -g {_shell_quote(spec.linux_user)} {_shell_quote(spec.workdir)}"
        )
        print(
            f"install -d -m 700 -o {_shell_quote(spec.linux_user)} -g {_shell_quote(spec.linux_user)} {_shell_quote(spec.hermes_home)}"
        )
        print(
            f"install -d -m 700 -o {_shell_quote(spec.linux_user)} -g {_shell_quote(spec.linux_user)} {_shell_quote(spec.hermes_home / 'home')}"
        )
        print(f"# write {_shell_quote(spec.env_path)}")
        for line in _env_lines(spec):
            print(f"#   {line}")
        print(f"# write {_shell_quote(spec.config_path)}")
        print(f"# install systemd unit -> {_shell_quote(spec.service_path)}")
        print(f"# ExecStart={hermes_bin} gateway run --replace")
        print()


def _print_json_summary(specs: list[UserSpec]) -> None:
    payload = [
        {
            "username": spec.username,
            "linux_user": spec.linux_user,
            "home_dir": str(spec.home_dir),
            "hermes_home": str(spec.hermes_home),
            "workdir": str(spec.workdir),
            "api_server_host": spec.host,
            "api_server_port": spec.port,
            "api_server_key": spec.api_key,
            "api_server_model_name": spec.model_name,
            "open_webui_base_url": f"http://{spec.host}:{spec.port}/v1",
            "systemd_service": spec.systemd_service,
        }
        for spec in specs
    ]
    print(json.dumps(payload, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate Linux-user-based Hermes deployment helpers for Open WebUI."
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        help="Path to unified users_mapping.yaml. Recommended for production.",
    )
    parser.add_argument(
        "--user",
        action="append",
        default=[],
        help="Legacy quick mode: repeatable NAME or NAME:API_KEY entries.",
    )
    parser.add_argument(
        "--start-port",
        type=int,
        default=DEFAULT_PORT,
        help=f"First API server port to assign in legacy mode (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"API server bind host in legacy mode (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--workspace-base",
        default=str(DEFAULT_WORKSPACE_BASE),
        help=f"Legacy mode only. Base directory used to derive /home roots (default: {DEFAULT_WORKSPACE_BASE})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Write .env/config/systemd/apply_host.sh bundle to this directory.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON summary instead of the shell plan.",
    )
    parser.add_argument(
        "--hermes-bin",
        default=DEFAULT_HERMES_BIN,
        help=f"Hermes executable path written into generated systemd units (default: {DEFAULT_HERMES_BIN})",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.mapping:
            specs = _build_specs_from_mapping(_load_mapping(args.mapping))
            legacy_mode = False
        else:
            specs = _build_specs_from_legacy_users(
                args.user,
                start_port=args.start_port,
                host=args.host,
                workspace_base=Path(args.workspace_base),
            )
            legacy_mode = True
    except CliError as exc:
        parser.error(str(exc))

    if args.output_dir:
        _write_deployment_bundle(specs, args.output_dir, args.hermes_bin)
        print(f"Wrote deployment bundle to {args.output_dir}")

    if args.json:
        _print_json_summary(specs)
    else:
        _print_plan(specs, hermes_bin=args.hermes_bin, legacy_mode=legacy_mode)
        print()
        print(
            "# Note: strong isolation requires User=<linux_user> systemd services and per-user HOME/workdir."
        )
        print(
            "# Uniform end-user display name should be implemented in Open WebUI wrapper models, not by"
        )
        print(
            "# advertising the same base model id from every Hermes connection unless Open WebUI prefix_id is used."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
