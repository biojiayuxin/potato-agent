from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LITE_APP_PATH = REPO_ROOT / "interface/static/lite/app.js"


def test_model_picker_names_are_configured_by_the_server() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    assert "MODEL_DISPLAY_NAME_OVERRIDES" not in source
    assert "model.display_name" in source


def test_model_picker_distinguishes_modes_with_the_same_upstream_model() -> None:
    _run_model_behavior("""
apiResponse = {json:async () => ({data:[
  {id:'fast', display_name:'Quick renamed', model:'same-upstream'},
  {id:'backup', display_name:'Backup renamed', model:'same-upstream'},
  {id:'deep', display_name:'Think renamed', model:'same-upstream'},
], default_id:'backup'})};
await fetchModels();
assert.deepEqual(state.models.map(getModelDisplayName), ['Quick renamed', 'Backup renamed', 'Think renamed']);
activate(createDraftSession());
assert.equal(getActiveSessionModelId(), 'backup');
""")


def _run_model_behavior(body: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the model picker")
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    def section(start: str, end: str) -> str:
        return source[source.index(start):source.index(end, source.index(start))]

    script = """
import assert from 'node:assert/strict';
const catalog = [
  {id:'deep', name:'deep', display_name:'Deep', model:'gpt-6.1-sol'},
  {id:'fast', name:'fast', display_name:'Fast', model:'gpt-6.1-sol'},
];
const state = {models:catalog, defaultModelId:'fast', sessions:[], activeSession:null,
  activeSessionId:null, draftSession:null};
const sessionPinStatesById = new Map();
const sessionModelStatesById = new Map();
const sessionModelRequestsById = new Map();
const sessionModelErrorsById = new Map();
const busySessionIds = new Set();
const pendingApprovalsBySessionId = new Map();
const isSessionBusy = id => busySessionIds.has(id);
const sessionNeedsApproval = id => pendingApprovalsBySessionId.has(id);
const getActivePersistentSessionId = () => state.activeSession?.id || '';
const shouldPollLiveSession = live => ['queued','starting','running','awaiting_approval'].includes(live?.status)
  || Boolean(live?.background_pending);
const isDefaultChatTitle = title => !title || title === 'New chat';
const renderWorkspaceHeader = () => {};
const nowSeconds = () => 123;
let nextId = 0;
const uuid = () => String(++nextId);
let authSessionGeneration = 0;
const calls = [];
let apiResponse;
const api = async (path, options) => { calls.push({path, ...options}); return apiResponse; };
const activate = session => { state.activeSession = session; state.activeSessionId = session.id; };
"""
    script += section("const isActiveSessionBlockingModelSwitch =", "const getModelDisplayName =")
    script += section("const getModelDisplayName =", "const setLocalSessionTitle =")
    script += section("const createDraftSession =", "const activatePersistedSession =")
    script += section("const fetchModels =", "const refreshSessions =")
    script += body
    subprocess.run([node, "--input-type=module", "--eval", script], check=True, capture_output=True, text=True)


def test_drafts_default_to_fast_in_both_catalog_loading_orders() -> None:
    _run_model_behavior("""
apiResponse = {json:async () => ({data:catalog, default_id:'fast'})};
state.models = []; state.defaultModelId = '';
state.draftSession = createDraftSession(); activate(state.draftSession);
assert.equal(state.activeSession.model_id, '');
await fetchModels();
assert.equal(getActiveSessionModelId(), 'fast');
await switchActiveModel('deep');
assert.equal(getActiveSessionModelId(), 'deep');
await fetchModels();
assert.equal(getActiveSessionModelId(), 'deep');
const transferred = JSON.parse(JSON.stringify(state.activeSession));
sessionModelStatesById.clear(); activate(transferred);
assert.equal(getActiveSessionModelId(), 'deep');
activate(createDraftSession());
assert.equal(getActiveSessionModelId(), 'fast');
activate({id:'legacy', model:'gpt-5.6-sol'});
assert.equal(getActiveSessionModelId(), 'fast');
assert(calls.every(call => call.path === '/api/models'));
""")


def test_draft_default_requires_the_server_default_instead_of_guessing_from_names() -> None:
    _run_model_behavior("""
apiResponse = {json:async () => ({data:[
  {id:'deep', display_name:'Deep', model:'shared-model'},
  {id:'gpt-6-sol', display_name:'Fast', model:'shared-model'},
  {id:'fast', display_name:'Fast renamed', model:'shared-model'},
]})};
await fetchModels();
activate(createDraftSession());
assert.equal(getActiveSessionModelId(), '');
assert.equal(getActiveSessionModel(), null);
apiResponse = {json:async () => ({data:state.models, default_id:'deep'})};
await fetchModels();
assert.equal(state.activeSession.model_id, 'deep');
assert.equal(getActiveSessionModelId(), 'deep');
""")


def test_model_save_targets_one_conversation_and_ignores_older_snapshots() -> None:
    _run_model_behavior("""
const a = {id:'a', model_id:'deep', model_revision:1};
const b = {id:'b', model_id:'fast', model_revision:2};
state.sessions = [a, b]; activate(b); busySessionIds.add('a');
let finish;
apiResponse = new Promise(resolve => { finish = resolve; });
const saving = switchActiveModel('deep');
assert.equal(sessionModelRequestsById.size, 1);
assert.equal(isActiveSessionBlockingModelSwitch(), true);
await switchActiveModel('fast');
assert.equal(calls.length, 1);
assert.equal(calls[0].path, '/api/sessions/b/model');
assert.deepEqual(JSON.parse(calls[0].body), {id:'deep'});
activate(a);
finish({json:async () => ({ok:true, session_id:'b', model_id:'deep', model_revision:3})});
await saving;
assert.equal(state.activeSession.id, 'a');
assert.equal(state.activeSession.model_revision, 1);
assert.equal(busySessionIds.has('a'), true);
assert.equal(sessionModelRequestsById.size, 0);
state.sessions = []; // The saved conversation may fall out of a paginated list.
const stale = normalizeSessionSnapshot(b);
assert.equal(stale.model_id, 'deep');
assert.equal(stale.model_revision, 3);
const fresh = normalizeSessionSnapshot({...b, model_revision:4});
assert.equal(fresh.model_id, 'fast');
assert.equal(fresh.model_revision, 4);
""")


def test_model_save_failure_and_signout_do_not_overwrite_another_session() -> None:
    _run_model_behavior("""
const a = {id:'a', model_id:'deep', model_revision:1};
const b = {id:'b', model_id:'fast', model_revision:2};
state.sessions = [a, b]; activate(b);
let finish;
apiResponse = new Promise(resolve => { finish = resolve; });
const saving = switchActiveModel('deep');
activate(a);
finish({json:async () => { throw new Error('Model temporarily unavailable'); }});
await saving;
assert.equal(getActiveSessionModelId(), 'deep');
assert.equal(sessionModelErrorsById.get('b'), 'Model temporarily unavailable');
assert.equal(sessionModelErrorsById.has('a'), false);
activate(b);
assert.equal(getActiveSessionModelId(), 'fast');
apiResponse = new Promise(resolve => { finish = resolve; });
const late = switchActiveModel('deep');
authSessionGeneration += 1;
sessionModelRequestsById.clear(); sessionModelStatesById.clear(); sessionModelErrorsById.clear();
activate({id:'new-account', model_id:'fast'});
finish({json:async () => ({ok:true, session_id:'b', model_id:'deep', model_revision:3})});
await late;
assert.equal(state.activeSession.id, 'new-account');
assert.equal(sessionModelStatesById.size, 0);
assert.equal(sessionModelErrorsById.size, 0);
""")


def test_only_the_current_session_blocks_its_model_picker() -> None:
    _run_model_behavior("""
const a = {id:'a', model_id:'deep'};
const b = {id:'b', model_id:'fast'};
activate(b); busySessionIds.add('a'); pendingApprovalsBySessionId.set('a', {});
assert.equal(isActiveSessionBlockingModelSwitch(), false);
activate(a);
assert.equal(isActiveSessionBlockingModelSwitch(), true);
busySessionIds.clear(); pendingApprovalsBySessionId.clear();
for (const status of ['queued','starting','running','awaiting_approval']) {
  a.live = {status}; assert.equal(isActiveSessionBlockingModelSwitch(), true);
}
a.live = {status:'idle', background_pending:true};
assert.equal(isActiveSessionBlockingModelSwitch(), true);
a.live.background_pending = false;
assert.equal(isActiveSessionBlockingModelSwitch(), false);
""")
