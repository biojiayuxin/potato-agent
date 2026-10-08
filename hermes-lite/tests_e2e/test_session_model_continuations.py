"""Real Gateway regression checks for per-session approvals and compression."""
from __future__ import annotations

import sqlite3
import time

from test_mock_provider_e2e import GatewayProcess, MockProvider, _payload


def test_real_compression_keeps_model_and_can_resume(tmp_path):
    deep = {'id':'deep','model':'Deep','provider':'custom','api_mode':'chat_completions','context_length':128000,'reasoning_effort':'high'}
    fast = {'id':'fast','model':'Fast','provider':'custom','api_mode':'chat_completions','context_length':256000,'reasoning_effort':'low'}
    root = tmp_path / 'gateway'
    with MockProvider([]) as provider:
        with GatewayProcess(root, provider) as gateway:
            a = gateway.rpc('session.create', {'model_config':deep})
            b = gateway.rpc('session.create', {'model_config':fast})
            sid = a['session_id']
            gateway.rpc('prompt.submit', {'session_id':b['session_id'], 'text':'Independent Fast conversation'})
            assert _payload(gateway.wait_event('message.complete', session_id=b['session_id']))['status'] == 'complete'
            for n in range(6):
                gateway.rpc('prompt.submit', {'session_id':sid, 'text':f'Question {n}: ' + 'This is retained conversation context. ' * 200})
                assert _payload(gateway.wait_event('message.complete', session_id=sid))['status'] == 'complete'
                deadline = time.monotonic()+5
                while gateway.rpc('session.resume', {'session_id':a['stored_session_id']}).get('running') and time.monotonic()<deadline:
                    time.sleep(.02)
            compressed = gateway.rpc('session.compress', {'session_id':sid})
            assert compressed['status'] == 'compressed'
            assert compressed['before_messages'] >= 12
            assert compressed['info']['model'] == 'Deep'
            assert compressed['info']['reasoning_effort'] == 'high'
            assert compressed['info']['usage']['context_max'] == 128000
            status_b = gateway.rpc('session.resume', {'session_id':b['stored_session_id']})
            assert status_b['info']['model'] == 'Fast'
            assert status_b['info']['usage']['context_max'] == 256000
            gateway.rpc('prompt.submit', {'session_id':sid, 'text':'Continue after compression'})
            after = _payload(gateway.wait_event('message.complete', session_id=sid))
            assert after['status'] == 'complete'
            physical = after['_fork_raw_boundary']['physical_session_id']
            assert physical != a['stored_session_id'], compressed
            db = sqlite3.connect(gateway.hermes_home / 'state.db')
            try:
                assert db.execute('SELECT parent_session_id FROM sessions WHERE id=?',(physical,)).fetchone()[0] == a['stored_session_id']
            finally:
                db.close()
        with GatewayProcess(root, provider) as restored:
            resumed = restored.rpc('session.resume', {'session_id':physical, 'model_config':deep})
            assert resumed['info']['model'] == 'Deep'
            restored.rpc('prompt.submit', {'session_id':resumed['session_id'], 'text':'Continue after cold resume'})
            assert _payload(restored.wait_event('message.complete', session_id=resumed['session_id']))['status'] == 'complete'
            assert [body for body in provider.state.requests if body.get('stream')][-1]['model'] == 'Deep'


def test_approval_once_executes_only_after_decision_while_other_chat_runs(tmp_path):
    deep = {'id':'deep','model':'Deep','provider':'custom','api_mode':'chat_completions','context_length':128000,'reasoning_effort':'high'}
    fast = {'id':'fast','model':'Fast','provider':'custom','api_mode':'chat_completions','context_length':256000,'reasoning_effort':'low'}
    with MockProvider([
        {'kind':'tool','command':'rm -rf ./audited-approval-fixture'},
        {'kind':'text','text':'Independent Deep response'},
        {'kind':'text','text':'Approved command complete'},
    ]) as provider:
        with GatewayProcess(tmp_path / 'approval', provider) as gateway:
            target = gateway.work / 'audited-approval-fixture'
            target.mkdir()
            (target/'marker.txt').write_text('test fixture only')
            a = gateway.rpc('session.create', {'model_config':fast})
            b = gateway.rpc('session.create', {'model_config':fast})
            gateway.rpc('prompt.submit', {'session_id':a['session_id'],'text':'Try approval-controlled fixture action'})
            approval = _payload(gateway.wait_event('approval.request', session_id=a['session_id']))
            assert target.exists()
            denied = gateway.response(gateway.send('session.model.set', {'session_id':a['session_id'],'model_config':deep}))
            assert denied['error']['code'] == 4009
            changed = gateway.rpc('session.model.set', {'session_id':b['session_id'],'model_config':deep})
            assert changed['found']
            gateway.rpc('prompt.submit', {'session_id':b['session_id'],'text':'Respond while A waits for approval'})
            assert _payload(gateway.wait_event('message.complete',session_id=b['session_id']))['text']=='Independent Deep response'
            assert target.exists()
            result = gateway.rpc('approval.respond', {'session_id':a['session_id'],'choice':'once','approval_id':approval['approval_id']})
            assert result['resolved'] == 1
            done = _payload(gateway.wait_event('message.complete', session_id=a['session_id']))
            assert done['status']=='complete'
            assert done['text']=='Approved command complete'
            assert not target.exists()
            models = [body['model'] for body in provider.state.requests if body.get('stream')]
            assert models == ['Fast','Deep','Fast']


def test_automatic_compression_uses_session_snapshot(tmp_path, monkeypatch):
    deep = {'id':'deep','model':'Deep','provider':'custom','api_mode':'chat_completions','context_length':128000,'reasoning_effort':'high'}
    plans = [{'kind':'text','text':f'History answer {n}'} for n in range(5)]
    plans.extend([{'kind':'tool','command':'printf audit-ok'}, {'kind':'text','text':'Continued after automatic compression'}])
    with MockProvider(plans) as provider:
        handler = provider.server.RequestHandlerClass
        real_write = handler._write_sse
        def high_usage(self, value):
            if isinstance(value, dict) and isinstance(value.get('usage'),dict):
                value={**value, 'usage':{'prompt_tokens':70000,'completion_tokens':2,'total_tokens':70002}}
            return real_write(self, value)
        monkeypatch.setattr(handler, "_write_sse", high_usage)
        with GatewayProcess(tmp_path / 'automatic', provider) as gateway:
            a=gateway.rpc('session.create',{'model_config':deep})
            physical=a['stored_session_id']
            for n in range(6):
                gateway.rpc('prompt.submit',{'session_id':a['session_id'],'text':f'History prompt {n}: keep context'})
                complete=_payload(gateway.wait_event('message.complete',session_id=a['session_id']))
                assert complete['status']=='complete'
                deadline=time.monotonic()+5
                with sqlite3.connect(gateway.hermes_home / 'state.db') as db:
                    physical=db.execute("SELECT id FROM sessions WHERE source='tui' ORDER BY started_at DESC LIMIT 1").fetchone()[0]
                while gateway.rpc('session.resume',{'session_id':physical}).get('running') and time.monotonic()<deadline:
                    time.sleep(.02)
            assert complete['text']=='Continued after automatic compression'
            assert physical != a['stored_session_id']
            resumed=gateway.rpc('session.resume',{'session_id':physical})
            assert resumed['info']['model']=='Deep'
            assert resumed['info']['reasoning_effort']=='high'
            assert resumed['info']['usage']['context_max']==128000
            assert resumed['info']['usage']['compressions']>=1
            assert all(body['model']=='Deep' for body in provider.state.requests if body.get('stream'))
