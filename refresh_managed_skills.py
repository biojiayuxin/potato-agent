#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import pwd
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from interface.hermes_service import (
    MANAGED_BIOINFORMATICS_SKILLS_DIR_NAME,
    is_service_active,
    restart_service,
)
from interface.mapping import DEFAULT_MAPPING_PATH, HermesTarget, MappingStore
from interface.user_private_files import (
    replace_user_private_tree,
    validate_user_runtime_paths,
)


DEPLOYED_ROOT = Path("/srv/potato_agent")
SOURCE_RELATIVE_PATH = Path("skills") / MANAGED_BIOINFORMATICS_SKILLS_DIR_NAME
DEFAULT_UNIT_DIR = Path("/etc/systemd/system")


class ManagedSkillRefreshError(RuntimeError):
    pass


@dataclass(frozen=True)
class RefreshCandidate:
    target: HermesTarget
    password_entry: object
    was_active: bool


@dataclass(frozen=True)
class RefreshResult:
    selected: tuple[str, ...]
    active: tuple[str, ...]
    completed: tuple[str, ...]
    applied: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run or refresh the Potato-managed bioinformatics skill category "
            "without changing other Hermes runtime files."
        )
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--user", help="Refresh one mapping username.")
    scope.add_argument("--all", action="store_true", help="Refresh every mapped user.")
    parser.add_argument(
        "--expect-count",
        type=int,
        help="Fail unless the selected target count exactly matches this value.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply the refresh.")
    return parser


def _real_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def _validate_source(source_path: Path) -> None:
    if not _real_directory(source_path):
        raise ManagedSkillRefreshError("Managed skill source is unavailable.")
    if not (source_path / "DESCRIPTION.md").is_file() or not (
        source_path / "general-web-search" / "SKILL.md"
    ).is_file():
        raise ManagedSkillRefreshError("Managed skill source is incomplete.")
    try:
        for candidate in source_path.rglob("*"):
            if candidate.is_symlink():
                raise ManagedSkillRefreshError(
                    "Managed skill source contains an unsupported symlink."
                )
    except OSError as exc:
        raise ManagedSkillRefreshError(
            "Managed skill source cannot be inspected."
        ) from exc


def _validate_production_source(source_path: Path) -> None:
    try:
        deployed_root = DEPLOYED_ROOT.resolve(strict=True)
        script_root = Path(__file__).resolve().parent
        source = source_path.resolve(strict=True)
    except OSError as exc:
        raise ManagedSkillRefreshError(
            "Apply must run from the deployed Potato release."
        ) from exc
    if script_root != deployed_root or source != deployed_root / SOURCE_RELATIVE_PATH:
        raise ManagedSkillRefreshError(
            "Apply must run from the deployed Potato release."
        )


def _select_targets(
    mapping_path: Path, *, username: str | None, all_users: bool
) -> list[HermesTarget]:
    try:
        targets = MappingStore(mapping_path).load_targets()
    except Exception as exc:
        raise ManagedSkillRefreshError(
            "Unable to load managed skill targets."
        ) from exc
    if all_users:
        selected = targets
    else:
        selected = [target for target in targets if target.username == username]
    if not selected:
        raise ManagedSkillRefreshError("No matching managed skill target was found.")
    return selected


def _preflight(
    targets: Sequence[HermesTarget],
    *,
    source_path: Path,
    unit_dir: Path,
    password_lookup: Callable[[str], object],
    service_active: Callable[[str], bool],
) -> list[RefreshCandidate]:
    _validate_source(source_path)
    candidates: list[RefreshCandidate] = []
    for target in targets:
        try:
            password_entry = password_lookup(target.linux_user)
            validate_user_runtime_paths(target, password_entry)
            hermes_info = target.hermes_home.lstat()
        except Exception as exc:
            raise ManagedSkillRefreshError(
                f"Preflight failed for mapping user {target.username}."
            ) from exc
        if (
            not stat.S_ISDIR(hermes_info.st_mode)
            or stat.S_ISLNK(hermes_info.st_mode)
            or hermes_info.st_uid != int(password_entry.pw_uid)
        ):
            raise ManagedSkillRefreshError(
                f"Preflight failed for mapping user {target.username}."
            )
        unit_path = unit_dir / target.systemd_service
        try:
            unit_info = unit_path.lstat()
        except OSError as exc:
            raise ManagedSkillRefreshError(
                f"Preflight failed for mapping user {target.username}."
            ) from exc
        if not stat.S_ISREG(unit_info.st_mode) or stat.S_ISLNK(unit_info.st_mode):
            raise ManagedSkillRefreshError(
                f"Preflight failed for mapping user {target.username}."
            )
        try:
            active = service_active(target.systemd_service)
        except Exception as exc:
            raise ManagedSkillRefreshError(
                f"Unable to inspect gateway state for mapping user {target.username}."
            ) from exc
        candidates.append(
            RefreshCandidate(
                target=target,
                password_entry=password_entry,
                was_active=active,
            )
        )
    return candidates


def refresh_managed_skills(
    *,
    mapping_path: Path = DEFAULT_MAPPING_PATH,
    source_path: Path,
    username: str | None,
    all_users: bool,
    expect_count: int | None,
    apply: bool,
    unit_dir: Path = DEFAULT_UNIT_DIR,
    password_lookup: Callable[[str], object] = pwd.getpwnam,
    service_active: Callable[[str], bool] = is_service_active,
    service_restart: Callable[[str], None] = restart_service,
    require_deployed_source: bool = True,
) -> RefreshResult:
    if bool(username) == bool(all_users):
        raise ManagedSkillRefreshError("Select exactly one refresh scope.")
    if expect_count is not None and expect_count < 0:
        raise ManagedSkillRefreshError("Expected target count must not be negative.")
    if apply and all_users and expect_count is None:
        raise ManagedSkillRefreshError(
            "--all --apply requires --expect-count before any write."
        )
    if apply and os.geteuid() != 0 and require_deployed_source:
        raise ManagedSkillRefreshError("Apply must run as root.")
    if apply and require_deployed_source:
        _validate_production_source(source_path)

    targets = _select_targets(
        mapping_path, username=username, all_users=all_users
    )
    if expect_count is not None and len(targets) != expect_count:
        raise ManagedSkillRefreshError(
            "Selected target count does not match --expect-count."
        )
    candidates = _preflight(
        targets,
        source_path=source_path,
        unit_dir=unit_dir,
        password_lookup=password_lookup,
        service_active=service_active,
    )
    selected_names = tuple(candidate.target.username for candidate in candidates)
    active_names = tuple(
        candidate.target.username for candidate in candidates if candidate.was_active
    )
    if not apply:
        return RefreshResult(selected_names, active_names, (), False)

    completed: list[str] = []
    for candidate in candidates:
        target = candidate.target
        try:
            replace_user_private_tree(
                target,
                candidate.password_entry,
                source=source_path,
                destination=(
                    target.hermes_home
                    / "skills"
                    / MANAGED_BIOINFORMATICS_SKILLS_DIR_NAME
                ),
            )
            if candidate.was_active:
                service_restart(target.systemd_service)
        except Exception as exc:
            remaining = [
                item.target.username
                for item in candidates
                if item.target.username not in completed
            ]
            completed_text = ", ".join(completed) if completed else "none"
            remaining_text = ", ".join(remaining) if remaining else "none"
            raise ManagedSkillRefreshError(
                "Managed skill apply stopped after a partial failure; "
                f"completed: {completed_text}; incomplete: {remaining_text}."
            ) from exc
        completed.append(target.username)
    return RefreshResult(selected_names, active_names, tuple(completed), True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_path = Path(__file__).resolve().parent / SOURCE_RELATIVE_PATH
    try:
        result = refresh_managed_skills(
            source_path=source_path,
            username=args.user,
            all_users=args.all,
            expect_count=args.expect_count,
            apply=args.apply,
        )
    except ManagedSkillRefreshError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    mode = "applied" if result.applied else "dry-run"
    print(
        f"{mode}: selected={len(result.selected)} active={len(result.active)} "
        f"completed={len(result.completed)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
