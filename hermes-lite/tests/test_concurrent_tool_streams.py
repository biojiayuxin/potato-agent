from __future__ import annotations

import json
import sys
import threading


def test_overlapping_execute_code_calls_preserve_process_streams(monkeypatch):
    import model_tools
    from tools.code_execution_tool import _rpc_server_loop

    first_entered = threading.Event()
    second_entered = threading.Event()
    first_finished = threading.Event()
    failures = []

    def handle(_name, _args, task_id):
        if task_id == 'first':
            first_entered.set()
            assert second_entered.wait(5)
        else:
            second_entered.set()
            assert first_finished.wait(5)
        return '{}'

    class Connection:
        def __init__(self):
            self.request = json.dumps({'token': 'test', 'tool': 'probe', 'args': {}}).encode() + b'\n'

        def settimeout(self, _value): pass
        def sendall(self, _data): pass
        def close(self): pass
        def recv(self, _size):
            data, self.request = self.request, b''
            return data

    class Server:
        def settimeout(self, _value): pass
        def accept(self): return Connection(), None

    monkeypatch.setattr(model_tools, 'handle_function_call', handle)
    def worker(task):
        try:
            _rpc_server_loop(Server(), task, [], [0], 1, frozenset({'probe'}), 'test')
        except BaseException as exc:
            failures.append(exc)
        finally:
            if task == 'first': first_finished.set()

    saved = sys.stdout, sys.stderr
    first = threading.Thread(target=worker, args=('first',))
    second = threading.Thread(target=worker, args=('second',))
    try:
        first.start()
        assert first_entered.wait(5)
        second.start()
        first.join(5)
        second.join(5)
        actual = sys.stdout, sys.stderr
    finally:
        sys.stdout, sys.stderr = saved
    assert not first.is_alive() and not second.is_alive()
    assert not failures
    assert actual == saved
    assert not actual[0].closed and not actual[1].closed
