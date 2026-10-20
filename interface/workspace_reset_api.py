"""HTTP confirmation flow, whole-request drain barrier and reset coordinator."""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
from collections import defaultdict

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection

from interface import workspace_reset_store as store
from interface.workspace_reset import eligible

WORKSPACE_PREFIXES = ('/api/runtime', '/api/sessions', '/api/models', '/api/files', '/api/chat-shares', '/api/tui')


def enabled() -> bool:
    return os.getenv('INTERFACE_WORKSPACE_RESET_ENABLED', '0') == '1'


def feature_state(user) -> dict:
    state = store.workspace_state(user.id)
    return {'enabled': enabled() and eligible(user.target), **state}


class ResetSubmission(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    confirmation: str = Field(max_length=20)
    request_id: str = Field(min_length=16, max_length=64, pattern=r'^[a-zA-Z0-9-]+$')


class WorkspaceResetMiddleware:
    """Track through the final ASGI response, including streaming downloads."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket') or not scope.get('path', '').startswith(WORKSPACE_PREFIXES):
            return await self.app(scope, receive, send)
        from interface.app import _resolve_current_user
        connection = HTTPConnection(scope)
        user, _ = await asyncio.to_thread(_resolve_current_user, connection)
        if user is None:
            return await self.app(scope, receive, send)
        # No await between the final gate check and registration: a queued reset
        # cannot miss an old request. The official server uses one ASGI worker.
        state = store.workspace_state(user.id)
        supplied = connection.headers.get('x-workspace-generation')
        if scope['type'] == 'websocket':
            supplied = connection.query_params.get('workspace_generation')
        stale = str(state['generation']) != str(supplied or '0')
        # Direct download links cannot send headers; they are read-only and drain
        # under the same barrier. Mutations and sockets must supply the epoch.
        require_epoch = scope['type'] == 'websocket' or scope.get('method') not in ('GET', 'HEAD', 'OPTIONS')
        if state['job'] or (stale and require_epoch):
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 4410})
            else:
                await JSONResponse(status_code=409, content={'detail': {
                    'code': 'workspace_reset_in_progress' if state['job'] else 'workspace_generation_changed',
                    'message': 'Workspace reset is in progress.' if state['job'] else 'Workspace changed. Refresh to continue.',
                    **state,
                }})(scope, receive, send)
            return
        task = asyncio.create_task(self.app(scope, receive, send))
        ACTIVE_REQUESTS[user.id][task] = send if scope['type'] == 'websocket' else None
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Disconnecting the browser must not hide a still-running database
            # operation from the drain barrier.
            with contextlib.suppress(Exception):
                await task
            raise
        finally:
            ACTIVE_REQUESTS[user.id].pop(task, None)
            if not ACTIVE_REQUESTS[user.id]:
                ACTIVE_REQUESTS.pop(user.id, None)


ACTIVE_REQUESTS: dict[str, dict] = defaultdict(dict)


def _require_same_origin(request: Request) -> None:
    origin = request.headers.get('origin', '')
    # JSON + a custom header require CORS preflight. Never allow a cross-site
    # origin to mint a confirmation even if cookies are forwarded by a proxy.
    expected = str(request.base_url).rstrip('/')
    if (request.headers.get('x-workspace-reset') != '1'
            or request.headers.get('sec-fetch-site') == 'cross-site'
            or (origin and origin != expected)):
        raise HTTPException(403, 'Workspace reset must be requested from this site.')


def _require_eligible(user) -> None:
    if not enabled() or not eligible(user.target):
        raise HTTPException(403, 'Reset is available only for verified dedicated workspaces.')


async def _drain_requests(user_id: str) -> None:
    for send in list(ACTIVE_REQUESTS.get(user_id, {}).values()):
        if send:
            with contextlib.suppress(Exception):
                await send({'type': 'websocket.close', 'code': 4410})
    deadline = asyncio.get_running_loop().time() + 65
    while ACTIVE_REQUESTS.get(user_id):
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError('requests_busy')
        await asyncio.sleep(0.1)


async def _helper(job: dict, *, stop_only: bool) -> None:
    from interface.app import privileged_client
    from interface.workspace_reset import execute_reset
    if privileged_client._can_call_directly():
        await asyncio.to_thread(execute_reset, job['mapping_username'], job['job_id'], stop_only=stop_only)
    else:
        await asyncio.to_thread(privileged_client._call_helper, [
            'reset-workspace-stop' if stop_only else 'reset-workspace',
            '--username', job['mapping_username'], '--job-id', job['job_id'],
        ])


async def _process_job(app, job: dict) -> None:
    user_id = job['user_id']
    manager = app.state.session_run_manager
    try:
        store.update_job(job['job_id'], status='running', phase=job['phase'])
        # Stop while parent/child process attribution is still available.
        await _helper(job, stop_only=True)
        if store.get_job(job['job_id'])['status'] not in store.ACTIVE:
            return
        await app.state.tui_gateway_bridges.close_for_reset(user_id)
        await _drain_requests(user_id)
        await manager.quiesce_user(user_id)
        await _helper(job, stop_only=False)
    except asyncio.CancelledError:
        # The root helper may still be finishing; its journal and locks remain
        # authoritative. Startup recovery must never assume cancellation undid it.
        raise
    except Exception as exc:
        current = store.get_job(job['job_id'])
        if current and current['status'] in store.ACTIVE:
            safe_failure = current['phase'] == 'stopping'
            known = ('unmanaged_processes', 'external_schedules', 'requests_busy', 'identity_changed', 'workspace_contains_mount', 'unsafe_home', 'unsafe_home_parent')
            code = next((code for code in known if code in str(exc)), 'reset_failed')
            store.update_job(job['job_id'], status='failed' if safe_failure else 'blocked',
                             phase=current['phase'], error_code=code)
            logging.getLogger(__name__).warning('Workspace reset needs attention: job=%s phase=%s code=%s',
                                                job['job_id'], current['phase'], code)
    finally:
        current = store.get_job(job['job_id'])
        if current and current['status'] not in store.ACTIVE:
            manager.release_user_after_reset(user_id)


async def worker(app) -> None:
    # A single coordinator across app instances; subprocess helpers additionally
    # take cross-process mapping/user locks during recovery.
    path = store._db_path().parent / '.workspace-reset-worker.lock'
    handle = path.open('a')
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                await asyncio.sleep(2)
                continue
            try:
                for job in await asyncio.to_thread(store.pending_jobs):
                    await _process_job(app, job)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).warning('Workspace reset coordinator will retry after a state-store failure')
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
            await asyncio.sleep(1)
    finally:
        handle.close()


def install(app, get_current_user) -> None:
    app.add_middleware(WorkspaceResetMiddleware)

    @app.post('/api/workspace/reset/prepare')
    async def prepare(request: Request, user=Depends(get_current_user)):
        _require_same_origin(request)
        _require_eligible(user)
        try:
            token = await asyncio.to_thread(store.prepare_reset, user.id)
        except store.ResetConflict as exc:
            raise HTTPException(409, str(exc)) from None
        return {'token': token, 'expires_in': 300}

    @app.post('/api/workspace/reset', status_code=202)
    async def submit(body: ResetSubmission, request: Request, user=Depends(get_current_user)):
        _require_same_origin(request)
        _require_eligible(user)
        if body.confirmation != 'RESET':
            raise HTTPException(400, 'Type RESET to confirm permanent deletion.')
        try:
            job = await asyncio.to_thread(store.enqueue_reset, user.id, user.mapping_username, body.token, body.request_id)
        except store.ResetConflict as exc:
            raise HTTPException(409, str(exc)) from None
        return store.public_job(job)

    @app.get('/api/workspace/reset/{job_id}')
    async def status(job_id: str, user=Depends(get_current_user)):
        job = await asyncio.to_thread(store.get_job, job_id)
        if not job or job['user_id'] != user.id:
            raise HTTPException(404, 'Reset task not found.')
        return store.public_job(job)
