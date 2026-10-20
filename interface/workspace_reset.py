"""Restricted root operations for dedicated Potato workspace identities.

Paths come exclusively from a root-owned enrollment record and the mapping.
The journal lives beside the old home on the same filesystem. Once committing
starts, recovery always finishes the reset; it never resurrects deleted chats.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from interface.auth_db import connect_auth_db
from interface.mapping import DEFAULT_MAPPING_PATH, HermesTarget, MappingStore, load_mapping
from interface.secure_paths import DEFAULT_STATE_DIR
from interface.user_lifecycle_lock import acquire_mapping_lock, acquire_user_lock, acquire_retention_lock
from interface import workspace_reset_store as store

IDENTITY_ROOT = DEFAULT_STATE_DIR / 'workspace-identities'


class ResetError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, payload: dict, *, mode: int = 0o600) -> None:
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def _identity_path(username: str) -> Path:
    return IDENTITY_ROOT / (hashlib.sha256(username.encode()).hexdigest() + '.json')


def _read_identity(target: HermesTarget) -> dict:
    path = _identity_path(target.username)
    for item in (IDENTITY_ROOT, path):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ResetError('unsafe_identity')
    identity = json.loads(path.read_text())
    expected = {'username': target.username, 'linux_user': target.linux_user,
                'home': str(target.home_dir), 'nonce': target.retention_identity_nonce}
    if any(identity.get(key) != value for key, value in expected.items()):
        raise ResetError('identity_changed')
    return identity


def eligible(target: HermesTarget) -> bool:
    try:
        identity = _read_identity(target)
        account = pwd.getpwnam(target.linux_user)
        return (account.pw_uid == identity['uid'] and account.pw_gid == identity['gid']
                and account.pw_dir == identity['home'])
    except (OSError, ValueError, KeyError, ResetError):
        return False


def _validate_home(target: HermesTarget, *, allow_missing: bool = False,
                   initializing: bool = False) -> os.stat_result | None:
    home = target.home_dir
    account = pwd.getpwnam(target.linux_user)
    if (account.pw_uid < 1000 or account.pw_dir != str(home) or not home.is_absolute()
            or home != home.resolve() or len(home.parts) < 3
            or target.hermes_home != home / '.hermes' or target.workdir != home):
        raise ResetError('unsafe_home')
    # A root-controlled parent is required for rename and recovery.
    parent = home.parent.stat()
    if parent.st_uid != 0 or parent.st_mode & 0o022:
        raise ResetError('unsafe_home_parent')
    for other in MappingStore(DEFAULT_MAPPING_PATH).load_targets():
        if other.username != target.username and (
            other.linux_user == target.linux_user or other.home_dir == home
            or home in other.home_dir.parents or other.home_dir in home.parents
        ):
            raise ResetError('shared_identity')
    if any(other.pw_uid == account.pw_uid and other.pw_name != target.linux_user for other in pwd.getpwall()):
        raise ResetError('shared_identity')
    _reject_mounts(home)
    try:
        info = home.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ResetError('home_missing') from None
    allowed_owners = (0, account.pw_uid) if initializing else (account.pw_uid,)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in allowed_owners:
        raise ResetError('unsafe_home')
    return info


def _reject_mounts(root: Path) -> None:
    # Includes bind mounts (same st_dev); do not traverse them during cleanup.
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        raw = line.split()[4]
        mount = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), raw))
        if mount == root or root in mount.parents:
            raise ResetError('workspace_contains_mount')


def enroll(target: HermesTarget, *, source: str) -> None:
    if os.geteuid() != 0:
        raise ResetError('root_required')
    _validate_home(target)
    import grp
    IDENTITY_ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
    if IDENTITY_ROOT.is_symlink() or IDENTITY_ROOT.stat().st_uid != 0:
        raise ResetError('unsafe_identity')
    os.chmod(IDENTITY_ROOT, 0o750)
    os.chown(IDENTITY_ROOT, 0, grp.getgrnam('potato-interface').gr_gid)
    account = pwd.getpwnam(target.linux_user)
    path = _identity_path(target.username)
    _write_json(path, {'username': target.username, 'linux_user': target.linux_user,
                      'home': str(target.home_dir), 'uid': account.pw_uid,
                      'gid': account.pw_gid, 'nonce': target.retention_identity_nonce,
                      'source': source, 'created_at': int(time.time())}, mode=0o640)
    os.chmod(path, 0o640)
    os.chown(path, 0, grp.getgrnam('potato-interface').gr_gid)


def enroll_legacy_hmx_accounts() -> dict:
    """Backfill the owner-confirmed registration namespace without resetting data."""
    if os.geteuid() != 0:
        raise ResetError('root_required')
    result = {'matched': 0, 'enrolled': 0, 'already_enabled': 0, 'failed': 0, 'errors': {}}
    with acquire_mapping_lock(exclusive=False):
        for target in MappingStore(DEFAULT_MAPPING_PATH).load_targets():
            if not target.linux_user.startswith('hmx'):
                continue
            result['matched'] += 1
            if eligible(target):
                result['already_enabled'] += 1
                continue
            try:
                with acquire_user_lock(target.username, exclusive=True, blocking=False):
                    # Never replace an old identity that no longer matches the mapping.
                    # Concurrent enrollment may have completed before we got the lock.
                    try:
                        _identity_path(target.username).lstat()
                    except FileNotFoundError:
                        enroll(target, source='legacy_hmx_registration')
                        result['enrolled'] += 1
                    else:
                        if not eligible(target):
                            raise ResetError('identity_changed')
                        result['already_enabled'] += 1
            except (OSError, KeyError, ValueError, ResetError) as exc:
                if isinstance(exc, ResetError):
                    code = exc.code
                elif isinstance(exc, BlockingIOError):
                    code = 'workspace_busy'
                else:
                    code = 'enrollment_failed'
                result['failed'] += 1
                result['errors'][code] = result['errors'].get(code, 0) + 1
    return result


def _uid_processes(uid: int) -> dict[int, tuple[int, str]]:
    result = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            lines = dict(line.split(':', 1) for line in (entry / 'status').read_text().splitlines() if ':' in line)
            if uid not in map(int, lines['Uid'].split()) or lines['State'].strip().startswith('Z'):
                continue
            result[int(entry.name)] = (int(lines['PPid']), (entry / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace'))
        except FileNotFoundError:
            continue
    return result


def _reject_external_schedulers(target: HermesTarget, uid: int) -> None:
    # These launchers live outside the home and could create fresh writers after
    # the process scan. Reset does not remove host-level scheduler configuration.
    for root in ('/var/spool/cron/crontabs', '/var/spool/cron', '/var/lib/systemd/linger'):
        if (Path(root) / target.linux_user).exists():
            raise ResetError('external_schedules')
    queued = Path('/var/spool/cron/atjobs')
    if queued.exists() and any(path.lstat().st_uid == uid for path in queued.iterdir()):
        raise ResetError('external_schedules')


def stop_workspace_processes(target: HermesTarget) -> None:
    from interface.hermes_service import service_operation_lock, stop_service
    identity = _read_identity(target)
    if not eligible(target):
        raise ResetError('identity_changed')
    # UID aliases are never eligible for process termination, including during
    # recovery when the live home is temporarily absent.
    if any(account.pw_uid == identity['uid'] and account.pw_name != target.linux_user for account in pwd.getpwall()):
        raise ResetError('shared_identity')
    _reject_external_schedulers(target, identity['uid'])
    processes = _uid_processes(identity['uid'])
    known = {pid for pid, (_, command) in processes.items()
             if 'tui_gateway.entry' in command or 'hermes gateway' in command}
    checkpoint = target.hermes_home / 'processes.json'
    # Read user-authored checkpoints as the user; never follow their links as root.
    result = subprocess.run(['runuser', '-u', target.linux_user, '--', 'head', '-c', '1048576', str(checkpoint)],
                            capture_output=True, timeout=10, check=False)
    if result.returncode == 0:
        try:
            known.update(int(row.get('pid', 0)) for row in json.loads(result.stdout) if isinstance(row, dict))
        except (ValueError, TypeError):
            raise ResetError('invalid_process_checkpoint') from None
    while True:
        children = {pid for pid, (parent, _) in processes.items() if parent in known}
        if children <= known:
            break
        known |= children
    if set(processes) - known:
        raise ResetError('unmanaged_processes')
    with service_operation_lock(target.systemd_service):
        stop_service(target.systemd_service)
    # pidfds prevent PID reuse from redirecting a signal to another process.
    handles = []
    try:
        for pid in known & processes.keys():
            try:
                fd = os.pidfd_open(pid)
                if pid in _uid_processes(identity['uid']):
                    handles.append(fd)
                    signal.pidfd_send_signal(fd, signal.SIGTERM)
                else:
                    os.close(fd)
            except ProcessLookupError:
                continue
        for _ in range(30):
            if not _uid_processes(identity['uid']):
                return
            time.sleep(0.1)
        for fd in handles:
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        for _ in range(30):
            if not _uid_processes(identity['uid']):
                return
            time.sleep(0.1)
        raise ResetError('unmanaged_processes')
    finally:
        for fd in handles:
            os.close(fd)


def _remove_tree(path: Path) -> None:
    if path.is_symlink():
        raise ResetError('unsafe_cleanup_path')
    if path.exists():
        _reject_mounts(path)
        shutil.rmtree(path)  # Python's fd-based rmtree does not follow symlinks.


def _initialize_home(target: HermesTarget) -> None:
    from interface.hermes_service import install_user_runtime_files
    account = pwd.getpwnam(target.linux_user)
    target.home_dir.mkdir(mode=0o700)
    shutil.copytree('/etc/skel', target.home_dir, dirs_exist_ok=True, symlinks=True)
    for directory, dirs, files in os.walk(target.home_dir, followlinks=False):
        for name in [*dirs, *files]:
            os.chown(Path(directory) / name, account.pw_uid, account.pw_gid, follow_symlinks=False)
    os.chown(target.home_dir, account.pw_uid, account.pw_gid)
    os.chmod(target.home_dir, 0o700)
    install_user_runtime_files(load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True), target)
    for name in ('config.yaml', '.env', 'SOUL.md'):
        info = (target.hermes_home / name).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid:
            raise ResetError('initialization_failed')
    for directory, _, files in os.walk(target.home_dir, followlinks=False):
        for name in files:
            path = Path(directory) / name
            if stat.S_ISREG(path.lstat().st_mode):
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        _fsync_dir(Path(directory))


def _clear_application_data(job: dict) -> None:
    if os.geteuid() == 0:
        # Shared SQLite WAL files must be created by their service owner, not
        # root, so other accounts can keep using the Interface during a reset.
        command = (
            'import sys; from interface.workspace_reset import _clear_application_data; '
            'from interface.workspace_reset_store import get_job; '
            '_clear_application_data(get_job(sys.argv[1]))'
        )
        result = subprocess.run(
            ['runuser', '-u', 'potato-interface', '--', sys.executable, '-B', '-c', command, job['job_id']],
            cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=120, check=False,
        )
        if result.returncode:
            raise ResetError('application_cleanup_failed')
        return
    from interface.display_store import delete_display_user_data
    from interface.runtime_state import reset_workspace_runtime_state
    from interface.chat_share_store import reset_user_chat_share_data
    from interface.archive_store import _connect_archive_db, ensure_archive_db
    user_id = job['user_id']
    delete_display_user_data(user_id)
    reset_workspace_runtime_state(user_id)
    reset_user_chat_share_data(user_id)
    with connect_auth_db() as conn:
        conn.execute('DELETE FROM user_storage_snapshots WHERE user_id=?', (user_id,))
        conn.commit()
    ensure_archive_db()
    with _connect_archive_db() as conn:
        conn.execute('DELETE FROM archived_sessions WHERE mapping_username=?', (job['mapping_username'],))


def _clear_file_archives(target: HermesTarget) -> None:
    from interface.user_data_retention import DEFAULT_CONFIG_PATH, Journal, load_retention_config, _open_root_fd, openat2_beneath
    if not DEFAULT_CONFIG_PATH.exists():
        return
    identity_nonce = target.retention_identity_nonce or 'legacy-' + hashlib.sha256(
        f'{target.username}\0{target.linux_user}\0{target.home_dir}'.encode('utf-8')
    ).hexdigest()
    for filesystem in load_retention_config().filesystems:
        if not (filesystem.history_root / 'journal.sqlite3').exists():
            continue
        journal = Journal(filesystem.history_root)
        with journal.connect() as conn:
            origins = [row['origin_id'] for row in conn.execute(
                'SELECT origin_id FROM origins WHERE mapping_username=? AND identity_nonce=? AND uid=?',
                (target.username, identity_nonce, pwd.getpwnam(target.linux_user).pw_uid))]
        history_fd = _open_root_fd(filesystem.history_root)
        try:
            for origin in origins:
                with journal.connect() as conn:
                    rows = conn.execute("SELECT entry_id,archive_relpath FROM entries WHERE origin_id=? AND state NOT IN ('PURGED','RESTORED')", (origin,)).fetchall()
                    for row in rows:
                        relative = bytes(row['archive_relpath'])
                        parent, _, name = relative.rpartition(b'/')
                        if not parent or name in (b'', b'.', b'..'):
                            raise ResetError('unsafe_archive_path')
                        try:
                            fd = openat2_beneath(history_fd, parent, os.O_RDONLY | os.O_DIRECTORY)
                        except FileNotFoundError:
                            fd = None
                        if fd is not None:
                            try:
                                with contextlib.suppress(FileNotFoundError):
                                    os.unlink(name, dir_fd=fd)
                                os.fsync(fd)
                            finally:
                                os.close(fd)
                        conn.execute("UPDATE entries SET state='PURGED',purged_at=?,hold_reason='workspace_reset' WHERE entry_id=?", (time.time(), row['entry_id']))
                    conn.execute('UPDATE origins SET active=0 WHERE origin_id=?', (origin,))
                    conn.commit()
        finally:
            os.close(history_fd)


def execute_reset(username: str, job_id: str, *, stop_only: bool = False) -> None:
    if os.geteuid() != 0 or not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ResetError('invalid_reset_request')
    job = store.get_job(job_id)
    if not job or job['mapping_username'] != username or job['status'] not in store.ACTIVE:
        raise ResetError('invalid_reset_request')
    retention_lock = contextlib.nullcontext() if stop_only else acquire_retention_lock(blocking=False)
    with retention_lock, acquire_mapping_lock(exclusive=not stop_only):
        job = store.get_job(job_id)
        if job and job['status'] in ('succeeded', 'failed'):
            return
        target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(username)
        if target is None or not eligible(target):
            raise ResetError('identity_changed')
        if stop_only:
            stop_workspace_processes(target)
            return
        # Retention and user deletion share this lock. The caller has already
        # stopped long-lived shared-lock holders before requesting exclusivity.
        with acquire_user_lock(username, exclusive=True, blocking=False, publish_marker=True, run_id=job_id):
            _execute_locked(target, job)


def _quarantine_root(target: HermesTarget) -> Path:
    root = target.home_dir.parent / '.potato-workspace-resets'
    root.mkdir(mode=0o700, exist_ok=True)
    _validate_private_directory(root)
    return root


def _validate_private_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise ResetError('unsafe_quarantine')


def _rollback_home(target: HermesTarget, old: Path, journal_path: Path, journal: dict, job: dict) -> None:
    # Write intent BEFORE renaming back. A crash after rename must not make the
    # next attempt mistake the restored original for an incomplete new home.
    journal['phase'] = 'rolling_back'
    _write_json(journal_path, journal)
    if old.exists():
        info = old.lstat()
        if (info.st_dev, info.st_ino) != (journal['old_dev'], journal['old_ino']):
            raise ResetError('identity_changed')
        _remove_tree(target.home_dir)
        os.rename(old, target.home_dir)
        _fsync_dir(target.home_dir.parent)
        _fsync_dir(old.parent)
    original = _validate_home(target)
    if (original.st_dev, original.st_ino) != (journal['old_dev'], journal['old_ino']):
        raise ResetError('identity_changed')
    journal['phase'] = 'rolled_back'
    _write_json(journal_path, journal)
    store.update_job(job['job_id'], status='failed', phase='rolled_back', error_code='initialization_failed')


def _execute_locked(target: HermesTarget, job: dict) -> None:
    identity = _read_identity(target)
    _reject_external_schedulers(target, identity['uid'])
    if _uid_processes(identity['uid']):
        raise ResetError('unmanaged_processes')
    # Persist the maintenance phase before the first possible directory move.
    store.update_job(job['job_id'], status='running', phase='rebuilding')
    root = _quarantine_root(target)
    directory = root / job['job_id']
    directory.mkdir(mode=0o700, exist_ok=True)
    _validate_private_directory(directory)
    journal_path = directory / 'journal.json'
    old = directory / 'old-home'
    if journal_path.exists():
        journal = json.loads(journal_path.read_text())
        if journal['identity'] != identity:
            raise ResetError('identity_changed')
    else:
        original = _validate_home(target)
        journal = {'identity': identity, 'phase': 'prepared', 'old_dev': original.st_dev,
                   'old_ino': original.st_ino}
        _write_json(journal_path, journal)
    try:
        if journal['phase'] == 'rolling_back':
            _rollback_home(target, old, journal_path, journal, job)
            return
        if journal['phase'] == 'prepared':
            if not old.exists():
                original = _validate_home(target)
                if (original.st_dev, original.st_ino) != (journal['old_dev'], journal['old_ino']):
                    raise ResetError('identity_changed')
                os.rename(target.home_dir, old)
                _fsync_dir(target.home_dir.parent)
                _fsync_dir(directory)
            info = old.lstat()
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (journal['old_dev'], journal['old_ino']):
                raise ResetError('identity_changed')
            journal['phase'] = 'initializing'
            _write_json(journal_path, journal)
        if journal['phase'] == 'initializing':
            store.update_job(job['job_id'], status='running', phase='rebuilding')
            # An interrupted initialization contains no user work: entry is gated.
            _validate_home(target, allow_missing=True, initializing=True)
            _remove_tree(target.home_dir)
            _initialize_home(target)
            journal['phase'] = 'committing'
            _write_json(journal_path, journal)
        if journal['phase'] == 'committing':
            store.update_job(job['job_id'], status='running', phase='cleaning')
            _clear_application_data(job)
            _clear_file_archives(target)
            _remove_tree(old)
            _fsync_dir(directory)
            journal['phase'] = 'complete'
            _write_json(journal_path, journal)
        if journal['phase'] == 'complete':
            store.update_job(job['job_id'], status='succeeded', phase='complete')
        if journal['phase'] == 'rolled_back':
            store.update_job(job['job_id'], status='failed', phase='rolled_back', error_code='initialization_failed')
    except Exception:
        if journal['phase'] in ('prepared', 'initializing') and old.exists():
            _rollback_home(target, old, journal_path, journal, job)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description='Enroll a verified dedicated account or retry a blocked reset.')
    parser.add_argument('action', choices=('enroll', 'enroll-hmx', 'retry'))
    parser.add_argument('username', nargs='?')
    parser.add_argument('--confirm-dedicated-account', action='store_true')
    args = parser.parse_args()
    if args.action == 'enroll-hmx' and args.username is not None:
        parser.error('enroll-hmx operates on all mapped hmx Linux accounts; omit username')
    if args.action != 'enroll-hmx' and args.username is None:
        parser.error('username is required for enroll and retry')
    if os.geteuid() != 0:
        raise ResetError('root_required')
    if args.action == 'enroll-hmx':
        result = enroll_legacy_hmx_accounts()
        print(json.dumps(result, sort_keys=True))
        if result['failed']:
            raise SystemExit(1)
    elif args.action == 'enroll':
        if not args.confirm_dedicated_account:
            parser.error('--confirm-dedicated-account is required after verifying account provenance')
        with acquire_mapping_lock(exclusive=False), acquire_user_lock(args.username, exclusive=True, blocking=False):
            target = MappingStore(DEFAULT_MAPPING_PATH).get_target_by_username(args.username)
            if target is None:
                raise ResetError('unknown_user')
            enroll(target, source='administrator_verified')
        print('Dedicated workspace reset enabled.')
    else:
        with connect_auth_db() as conn:
            conn.execute("UPDATE workspace_reset_jobs SET status='queued',error_code='' WHERE mapping_username=? AND status='blocked'", (args.username,))
            conn.commit()
        print('Blocked reset queued for recovery.')


if __name__ == '__main__':
    main()
