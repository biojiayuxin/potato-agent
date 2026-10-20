"""Reset regressions use private temporary DBs/homes, never system users."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import pwd
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from interface import auth_db
from interface import workspace_reset as reset
from interface import workspace_reset_store as store
from interface import workspace_reset_api as api


@pytest.fixture
def hmx_enrollment(tmp_path, monkeypatch):
    targets = [
        SimpleNamespace(username='alice', linux_user='hmx_alice'),
        SimpleNamespace(username='bob', linux_user='hmxlegacy'),
        SimpleNamespace(username='hmx_website_login', linux_user='external_user'),
    ]
    monkeypatch.setattr(reset.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(reset, 'IDENTITY_ROOT', tmp_path)
    monkeypatch.setattr(reset, 'MappingStore', lambda _: SimpleNamespace(load_targets=lambda: targets))
    held = []

    @contextlib.contextmanager
    def mapping_lock(*, exclusive):
        assert exclusive is False
        held.append('mapping')
        try:
            yield
        finally:
            held.pop()

    @contextlib.contextmanager
    def user_lock(username, *, exclusive, blocking):
        assert held == ['mapping']
        assert exclusive is True and blocking is False
        held.append(username)
        try:
            yield
        finally:
            held.pop()

    monkeypatch.setattr(reset, 'acquire_mapping_lock', mapping_lock)
    monkeypatch.setattr(reset, 'acquire_user_lock', user_lock)

    def eligible(target):
        path = reset._identity_path(target.username)
        return path.exists() and path.read_text() == target.linux_user

    calls = []

    def enroll(target, *, source):
        assert held == ['mapping', target.username]
        assert source == 'legacy_hmx_registration'
        reset._identity_path(target.username).write_text(target.linux_user)
        calls.append(target.username)

    monkeypatch.setattr(reset, 'eligible', eligible)
    monkeypatch.setattr(reset, 'enroll', enroll)
    return targets, calls


def test_hmx_enrollment_uses_linux_name_and_is_idempotent(hmx_enrollment):
    targets, calls = hmx_enrollment
    assert reset.enroll_legacy_hmx_accounts() == {
        'matched': 2, 'enrolled': 2, 'already_enabled': 0, 'failed': 0, 'errors': {},
    }
    assert reset.enroll_legacy_hmx_accounts() == {
        'matched': 2, 'enrolled': 0, 'already_enabled': 2, 'failed': 0, 'errors': {},
    }
    assert calls == ['alice', 'bob']
    assert not reset._identity_path(targets[2].username).exists()


def test_hmx_enrollment_preserves_conflicting_identity(hmx_enrollment):
    _, calls = hmx_enrollment
    path = reset._identity_path('alice')
    path.write_text('previous_identity')
    result = reset.enroll_legacy_hmx_accounts()
    assert result == {
        'matched': 2, 'enrolled': 1, 'already_enabled': 0,
        'failed': 1, 'errors': {'identity_changed': 1},
    }
    assert path.read_text() == 'previous_identity'
    assert calls == ['bob']


@pytest.mark.parametrize('error,code', [
    (reset.ResetError('unsafe_home'), 'unsafe_home'),
    (reset.ResetError('shared_identity'), 'shared_identity'),
    (BlockingIOError(), 'workspace_busy'),
    (OSError('private path must not appear in output'), 'enrollment_failed'),
])
def test_hmx_enrollment_keeps_validation_and_continues(hmx_enrollment, monkeypatch, error, code):
    _, calls = hmx_enrollment
    enroll = reset.enroll

    def guarded_enroll(target, *, source):
        if target.username == 'alice':
            raise error
        enroll(target, source=source)

    monkeypatch.setattr(reset, 'enroll', guarded_enroll)
    result = reset.enroll_legacy_hmx_accounts()
    assert result['failed'] == 1
    assert result['errors'] == {code: 1}
    assert result['enrolled'] == 1
    assert calls == ['bob']


def test_hmx_enrollment_requires_root(hmx_enrollment, monkeypatch):
    _, calls = hmx_enrollment
    monkeypatch.setattr(reset.os, 'geteuid', lambda: 1000)
    with pytest.raises(reset.ResetError, match='root_required'):
        reset.enroll_legacy_hmx_accounts()
    assert calls == []


@pytest.mark.parametrize('action', ['enroll', 'retry'])
def test_existing_reset_admin_commands_still_require_username(monkeypatch, action):
    monkeypatch.setattr(reset.sys, 'argv', ['workspace_reset', action])
    with pytest.raises(SystemExit) as exc:
        reset.main()
    assert exc.value.code == 2


def test_hmx_cli_reports_partial_enrollment_failure(hmx_enrollment, monkeypatch, capsys):
    reset._identity_path('alice').write_text('previous_identity')
    monkeypatch.setattr(reset.sys, 'argv', ['workspace_reset', 'enroll-hmx'])
    with pytest.raises(SystemExit) as exc:
        reset.main()
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)['errors'] == {'identity_changed': 1}


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / 'interface.db'
    monkeypatch.setenv('INTERFACE_AUTH_DB', str(path))
    connect = auth_db.connect_auth_db
    monkeypatch.setattr(store, 'connect_auth_db', lambda _path=path: connect(path))
    store.ensure_reset_store(path)
    user = auth_db.upsert_user(username='alice', email='alice@example.com', password='test-password',
                              mapping_username='alice', name='Alice', db_path=path)
    return path, user


def new_job(user, request_id='request-0000000001'):
    token = store.prepare_reset(user.id)
    return store.enqueue_reset(user.id, user.mapping_username, token, request_id)


def test_confirmation_is_scoped_expiring_single_use_and_idempotent(database):
    path, user = database
    token = store.prepare_reset(user.id)
    with pytest.raises(store.ResetConflict):
        store.enqueue_reset('someone-else', 'someone-else', token, 'request-other-user')
    job = store.enqueue_reset(user.id, 'alice', token, 'request-0000000001')
    assert store.enqueue_reset(user.id, 'alice', token, 'request-0000000001') == job
    assert store.maintenance_active('alice', path)
    assert not store.maintenance_active('bob', path)
    store.update_job(job['job_id'], status='succeeded', phase='complete')
    with pytest.raises(store.ResetConflict):
        store.enqueue_reset(user.id, 'alice', token, 'request-0000000002')
    token = store.prepare_reset(user.id)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE workspace_reset_confirmations SET expires_at=0')
    with pytest.raises(store.ResetConflict):
        store.enqueue_reset(user.id, 'alice', token, 'request-0000000003')
    assert auth_db.get_user_by_id(user.id, path) == user


def test_blocked_reset_keeps_gate_across_connections(database):
    path, user = database
    job = new_job(user)
    store.update_job(job['job_id'], status='blocked', phase='cleaning', error_code='reset_failed')
    assert store.maintenance_active('alice', path)
    assert store.workspace_state(user.id)['job']['status'] == 'blocked'
    with pytest.raises(store.ResetConflict):
        new_job(user, 'request-0000000002')
    from restore_archived_chats import _verify_no_workspace_resets, RestoreError
    with pytest.raises(RestoreError, match='reset recovery'):
        _verify_no_workspace_resets(path, {'alice'})
    _verify_no_workspace_resets(path, {'bob'})


def test_reset_and_temporary_expiry_are_mutually_exclusive(database):
    from interface.runtime_state import claim_temporary_user_cleanup
    path, _ = database
    user = auth_db.create_temporary_user(username='temporary', email='temporary@example.com', password='p',
                                         mapping_username='temporary', name='Temporary', db_path=path)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE temporary_users SET created_at=1')
    new_job(user)
    assert claim_temporary_user_cleanup(user.id, idle_timeout_seconds=1, cleanup_retry_seconds=1, now=1000, db_path=path) is None


@pytest.fixture
def reset_home(database, tmp_path, monkeypatch):
    path, user = database
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'uploaded.txt').write_text('irreplaceable')
    public = tmp_path / 'public'
    public.mkdir()
    (public / 'dataset.txt').write_text('shared')
    (home / 'public_data').symlink_to(public, target_is_directory=True)
    target = SimpleNamespace(home_dir=home, username='alice')
    identity = {'uid': 12345, 'nonce': 'original'}
    quarantine = tmp_path / 'quarantine'
    quarantine.mkdir()
    monkeypatch.setattr(reset, '_quarantine_root', lambda _target: quarantine)
    monkeypatch.setattr(reset, '_validate_private_directory', lambda _path: None)
    monkeypatch.setattr(reset, '_read_identity', lambda _target: identity)
    monkeypatch.setattr(reset, '_uid_processes', lambda _uid: {})
    monkeypatch.setattr(reset, '_reject_external_schedulers', lambda *_: None)
    monkeypatch.setattr(reset, '_validate_home', lambda target, **_: target.home_dir.stat() if target.home_dir.exists() else None)
    def initialize(target):
        target.home_dir.mkdir()
        (target.home_dir / 'default.txt').write_text('current default')
    monkeypatch.setattr(reset, '_initialize_home', initialize)
    cleared = []
    monkeypatch.setattr(reset, '_clear_application_data', lambda job: cleared.append(job['user_id']))
    monkeypatch.setattr(reset, '_clear_file_archives', lambda _: None)
    return target, new_job(user), quarantine, public, cleared


def test_reset_rebuilds_home_without_following_public_symlink(reset_home):
    target, job, quarantine, public, cleared = reset_home
    reset._execute_locked(target, job)
    assert list(target.home_dir.iterdir()) == [target.home_dir / 'default.txt']
    assert (public / 'dataset.txt').read_text() == 'shared'
    assert cleared == [job['user_id']]
    assert not (quarantine / job['job_id'] / 'old-home').exists()
    assert store.get_job(job['job_id'])['status'] == 'succeeded'


def test_initialization_failure_restores_original_home(reset_home, monkeypatch):
    target, job, _, public, cleared = reset_home
    def fail(target):
        target.home_dir.mkdir()
        (target.home_dir / 'partial').write_text('half-created')
        raise OSError('disk full')
    monkeypatch.setattr(reset, '_initialize_home', fail)
    with pytest.raises(OSError):
        reset._execute_locked(target, job)
    assert (target.home_dir / 'uploaded.txt').read_text() == 'irreplaceable'
    assert not (target.home_dir / 'partial').exists()
    assert (public / 'dataset.txt').read_text() == 'shared'
    assert cleared == []
    assert store.get_job(job['job_id'])['status'] == 'failed'


def test_crash_during_rollback_does_not_delete_restored_original(reset_home, monkeypatch):
    target, job, _, _, _ = reset_home
    monkeypatch.setattr(reset, '_initialize_home', lambda _: (_ for _ in ()).throw(OSError('disk full')))
    original_rename = reset.os.rename
    def crash_after_restore(source, destination):
        original_rename(source, destination)
        if Path(source).name == 'old-home':
            raise KeyboardInterrupt('power loss after rollback rename')
    monkeypatch.setattr(reset.os, 'rename', crash_after_restore)
    with pytest.raises(KeyboardInterrupt):
        reset._execute_locked(target, job)
    monkeypatch.setattr(reset.os, 'rename', original_rename)
    reset._execute_locked(target, job)
    assert (target.home_dir / 'uploaded.txt').read_text() == 'irreplaceable'
    assert store.get_job(job['job_id'])['status'] == 'failed'


def test_private_quarantine_rejects_symlink(tmp_path):
    actual = tmp_path / 'actual'
    actual.mkdir(mode=0o700)
    link = tmp_path / 'link'
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(reset.ResetError, match='unsafe_quarantine'):
        reset._validate_private_directory(link)


def test_mountpoint_including_same_device_bind_mount_is_rejected(tmp_path, monkeypatch):
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo':
            return f'10 1 8:1 / {tmp_path}/home/subdir rw - ext4 /dev/a rw\n'
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(reset.ResetError, match='workspace_contains_mount'):
        reset._reject_mounts(tmp_path / 'home')


@pytest.mark.parametrize('crash_phase', ['prepared', 'initializing', 'committing', 'complete'])
def test_resume_after_each_durable_boundary(reset_home, monkeypatch, crash_phase):
    target, job, quarantine, _, _ = reset_home
    original_write = reset._write_json
    crashed = False
    def write(path, payload, **kwargs):
        nonlocal crashed
        original_write(path, payload, **kwargs)
        if payload.get('phase') == crash_phase and not crashed:
            crashed = True
            raise KeyboardInterrupt('simulated process death')
    monkeypatch.setattr(reset, '_write_json', write)
    with pytest.raises(KeyboardInterrupt):
        reset._execute_locked(target, job)
    assert store.workspace_state(job['user_id'])['job']
    reset._execute_locked(target, store.get_job(job['job_id']))
    assert store.get_job(job['job_id'])['status'] == 'succeeded'
    assert (target.home_dir / 'default.txt').exists()
    assert not (quarantine / job['job_id'] / 'old-home').exists()


def test_failure_after_commit_never_restores_old_home(reset_home, monkeypatch):
    target, job, quarantine, _, _ = reset_home
    monkeypatch.setattr(reset, '_clear_application_data', lambda _: (_ for _ in ()).throw(OSError('database unavailable')))
    with pytest.raises(OSError):
        reset._execute_locked(target, job)
    assert (target.home_dir / 'default.txt').exists()
    assert not (target.home_dir / 'uploaded.txt').exists()
    assert (quarantine / job['job_id'] / 'old-home' / 'uploaded.txt').exists()
    assert store.workspace_state(job['user_id'])['job']
    monkeypatch.setattr(reset, '_clear_application_data', lambda _: None)
    reset._execute_locked(target, job)
    assert store.get_job(job['job_id'])['status'] == 'succeeded'


def test_unexpected_process_prevents_any_file_changes(reset_home, monkeypatch):
    target, job, _, _, _ = reset_home
    monkeypatch.setattr(reset, '_uid_processes', lambda _: {123: (1, 'unmanaged')})
    with pytest.raises(reset.ResetError, match='unmanaged_processes'):
        reset._execute_locked(target, job)
    assert (target.home_dir / 'uploaded.txt').read_text() == 'irreplaceable'


def test_http_confirmation_and_job_ownership(database, monkeypatch):
    _, user = database
    current = SimpleNamespace(id=user.id, mapping_username='alice', target=object())
    app = FastAPI()
    async def authenticate():
        return current
    api.install(app, authenticate)
    monkeypatch.setattr(api, 'enabled', lambda: True)
    monkeypatch.setattr(api, 'eligible', lambda _: True)
    client = TestClient(app)
    headers = {'X-Workspace-Reset': '1', 'Origin': 'http://testserver'}
    assert client.post('/api/workspace/reset/prepare').status_code == 403
    assert client.post('/api/workspace/reset/prepare', headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    token = client.post('/api/workspace/reset/prepare', headers=headers).json()['token']
    body = {'token': token, 'confirmation': '', 'request_id': 'request-0000000001'}
    assert client.post('/api/workspace/reset', json=body, headers=headers).status_code == 400
    response = client.post('/api/workspace/reset', json={**body, 'confirmation': 'RESET'}, headers=headers)
    assert response.status_code == 202
    job = response.json()
    assert client.get('/api/workspace/reset/' + job['job_id']).status_code == 200
    current.id = 'another-user'
    assert client.get('/api/workspace/reset/' + job['job_id']).status_code == 404
    monkeypatch.setattr(api, 'eligible', lambda _: False)
    assert client.post('/api/workspace/reset/prepare', headers=headers).status_code == 403


def test_manager_cancellation_waits_for_inflight_database_write(database):
    from interface.session_run_manager import SessionRunManager
    path, _ = database
    async def scenario():
        manager = SessionRunManager(db_path=path)
        started = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        def write():
            started.set()
            release.wait(5)
            completed.set()
        task = asyncio.create_task(manager._reset_safe_io(write))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert completed.is_set()
    asyncio.run(scenario())


def test_share_reset_does_not_tombstone_account(database, tmp_path, monkeypatch):
    from interface import chat_share_store as shares
    path = tmp_path / 'shares.db'
    monkeypatch.setattr(shares, 'DEFAULT_CHAT_SHARE_DB_PATH', path)
    shares.ensure_chat_share_store()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO chat_share_owner_tombstones VALUES ('alice',1)")
        conn.execute("INSERT INTO chat_share_owner_tombstones VALUES ('bob',1)")
        conn.execute("INSERT INTO chat_share_recipient_tombstones VALUES ('formal:alice',1)")
    shares.reset_user_chat_share_data('alice')
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT * FROM chat_share_owner_tombstones').fetchall() == [('bob',1)]
        assert not conn.execute('SELECT * FROM chat_share_recipient_tombstones').fetchall()


def test_reset_preserves_cookie_revocation_and_other_accounts(database):
    from interface.runtime_state import ensure_runtime_state_store, reset_workspace_runtime_state
    path, user = database
    ensure_runtime_state_store(path)
    with sqlite3.connect(path) as conn:
        conn.execute('INSERT INTO runtime_state(user_id,session_revoked_after,last_sleep_reason,updated_at) VALUES (?,?,?,?)',
                     (user.id, 1234, 'idle_timeout', 1234))
        conn.execute("INSERT INTO runtime_state(user_id,session_revoked_after,updated_at) VALUES ('other',4321,4321)")
    reset_workspace_runtime_state(user.id, path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT session_revoked_after,last_sleep_reason FROM runtime_state WHERE user_id=?', (user.id,)).fetchone() == (1234, 'idle_timeout')
        assert conn.execute("SELECT session_revoked_after FROM runtime_state WHERE user_id='other'").fetchone() == (4321,)


def test_http_gate_drains_existing_requests_and_rejects_old_generation(database, monkeypatch):
    import importlib
    app_mod = importlib.import_module('interface.app')
    _, user = database
    monkeypatch.setattr(app_mod, '_resolve_current_user', lambda _: (user, None))
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        messages = []
        async def upstream(scope, receive, send):
            entered.set()
            await release.wait()
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})
        middleware = api.WorkspaceResetMiddleware(upstream)
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            messages.append(message)
        scope = {'type': 'http', 'path': '/api/files/upload', 'method': 'POST',
                 'headers': [], 'query_string': b'', 'scheme': 'http', 'server': ('test',80)}
        old_request = asyncio.create_task(middleware(scope, receive, send))
        await entered.wait()
        assert len(api.ACTIVE_REQUESTS[user.id]) == 1
        job = new_job(user)
        await middleware(scope, receive, send)
        assert messages[0]['status'] == 409
        drain = asyncio.create_task(api._drain_requests(user.id))
        await asyncio.sleep(0)
        assert not drain.done()
        old_request.cancel()
        await asyncio.sleep(0)
        assert api.ACTIVE_REQUESTS[user.id]
        assert not drain.done()
        release.set()
        await asyncio.gather(old_request, return_exceptions=True)
        await drain
        assert user.id not in api.ACTIVE_REQUESTS
        store.update_job(job['job_id'], status='succeeded', phase='complete')
        messages.clear()
        await middleware(scope, receive, send)
        assert messages[0]['status'] == 409
        assert b'workspace_generation_changed' in messages[1]['body']
        messages.clear()
        await middleware({**scope, 'headers': [(b'x-workspace-generation',b'1')]}, receive, send)
        assert messages[0]['status'] == 200
        messages.clear()
        await middleware({**scope, 'type': 'websocket'}, receive, send)
        assert messages == [{'type': 'websocket.close', 'code': 4410}]
    asyncio.run(scenario())


def test_home_validator_rejects_system_uid_and_symlink(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    target = SimpleNamespace(home_dir=home, workdir=home, hermes_home=home/'.hermes', linux_user='dedicated')
    account = SimpleNamespace(pw_uid=0, pw_dir=str(home))
    monkeypatch.setattr(reset.pwd, 'getpwnam', lambda _: account)
    with pytest.raises(reset.ResetError, match='unsafe_home'):
        reset._validate_home(target)
    account.pw_uid = 1001
    link = tmp_path / 'linked'
    link.symlink_to(home, target_is_directory=True)
    target.home_dir = link
    account.pw_dir = str(link)
    with pytest.raises(reset.ResetError, match='unsafe_home'):
        reset._validate_home(target)


def test_unknown_process_refuses_before_service_stop(tmp_path, monkeypatch):
    from interface import hermes_service
    target = SimpleNamespace(linux_user='dedicated', hermes_home=tmp_path, systemd_service='hermes-test.service')
    monkeypatch.setattr(reset, '_read_identity', lambda _: {'uid': 12345})
    monkeypatch.setattr(reset, 'eligible', lambda _: True)
    monkeypatch.setattr(reset.pwd, 'getpwall', lambda: [])
    monkeypatch.setattr(reset, '_reject_external_schedulers', lambda *_: None)
    monkeypatch.setattr(reset, '_uid_processes', lambda _: {42: (1, 'sshd: dedicated')})
    monkeypatch.setattr(reset.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    monkeypatch.setattr(hermes_service, 'stop_service', lambda _: pytest.fail('must not stop on unknown processes'))
    with pytest.raises(reset.ResetError, match='unmanaged_processes'):
        reset.stop_workspace_processes(target)


def test_external_schedule_is_refused_without_deleting_it(monkeypatch):
    observed = []
    def exists(path):
        observed.append(str(path))
        return str(path) == '/var/spool/cron/crontabs/dedicated'
    monkeypatch.setattr(Path, 'exists', exists)
    with pytest.raises(reset.ResetError, match='external_schedules'):
        reset._reject_external_schedulers(SimpleNamespace(linux_user='dedicated'), 12345)
    assert observed == ['/var/spool/cron/crontabs/dedicated']


@pytest.mark.parametrize('nonce', ['new-identity', ''])
def test_file_archives_are_purged_for_current_and_legacy_identity(tmp_path, monkeypatch, nonce):
    from interface import user_data_retention as retention
    history = tmp_path / 'history'
    history.mkdir(mode=0o700)
    (history / 'files').mkdir()
    archived = history / 'files' / 'old.txt'
    archived.write_text('private old data')
    unrelated = history / 'files' / 'other.txt'
    unrelated.write_text('other account data')
    config_path = tmp_path / 'retention.yaml'
    config_path.touch()
    monkeypatch.setattr(retention, 'DEFAULT_CONFIG_PATH', config_path)
    monkeypatch.setattr(retention, 'load_retention_config', lambda: SimpleNamespace(filesystems=[SimpleNamespace(history_root=history)]))
    target = SimpleNamespace(username='alice', linux_user=pwd.getpwuid(os.getuid()).pw_name,
                             home_dir=tmp_path / 'home', retention_identity_nonce=nonce)
    expected_nonce = nonce or 'legacy-' + hashlib.sha256(f'alice\0{target.linux_user}\0{target.home_dir}'.encode()).hexdigest()
    journal = retention.Journal(history)
    with journal.connect() as conn:
        for origin, identity_nonce, filename in [('mine', expected_nonce, b'files/old.txt'), ('other', 'another-identity', b'files/other.txt')]:
            conn.execute('INSERT INTO origins(origin_id,mapping_username,linux_user,uid,gid,home_path,home_dev,home_ino,identity_nonce,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (origin,'alice',target.linux_user,os.getuid(),os.getgid(),b'/home/test',1,2,identity_nonce,1))
            conn.execute('INSERT INTO runs(run_id,origin_id,mode,status,policy_sha256,started_at) VALUES (?,?,?,?,?,?)',
                         (origin,origin,'enforce','ok','test-policy',1))
            conn.execute('INSERT INTO entries(entry_id,origin_id,run_id,relpath,archive_relpath,dev,ino,mode,uid,gid,size,blocks,atime_ns,mtime_ns,ctime_ns,state,prepared_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (origin,origin,origin,b'old.txt',filename,1,2,0o100600,os.getuid(),os.getgid(),3,1,1,1,1,'STAGED',1))
        conn.commit()
    reset._clear_file_archives(target)
    assert not archived.exists()
    assert unrelated.read_text() == 'other account data'
    with journal.connect() as conn:
        assert conn.execute("SELECT state FROM entries WHERE entry_id='mine'").fetchone()['state'] == 'PURGED'
        assert conn.execute("SELECT active FROM origins WHERE origin_id='mine'").fetchone()['active'] == 0


def test_gateway_descendants_are_recognized_before_stop(tmp_path, monkeypatch):
    from interface import hermes_service
    target = SimpleNamespace(linux_user='dedicated', hermes_home=tmp_path, systemd_service='hermes-test.service')
    monkeypatch.setattr(reset, '_read_identity', lambda _: {'uid': 12345})
    monkeypatch.setattr(reset, 'eligible', lambda _: True)
    monkeypatch.setattr(reset.pwd, 'getpwall', lambda: [])
    monkeypatch.setattr(reset, '_reject_external_schedulers', lambda *_: None)
    processes = {42: (1, 'python -m tui_gateway.entry'), 43: (42, 'bash analysis.sh'), 44: (43, 'python analysis.py')}
    monkeypatch.setattr(reset, '_uid_processes', lambda _: dict(processes))
    monkeypatch.setattr(reset.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    monkeypatch.setattr(hermes_service, 'service_operation_lock', lambda _: contextlib.nullcontext())
    stopped = []
    def stop(_):
        stopped.append(True)
        processes.clear()
    monkeypatch.setattr(hermes_service, 'stop_service', stop)
    monkeypatch.setattr(reset.os, 'pidfd_open', lambda _: (_ for _ in ()).throw(ProcessLookupError()))
    reset.stop_workspace_processes(target)
    assert stopped == [True]
