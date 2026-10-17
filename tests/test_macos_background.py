"""启动入口的进程归属和端口检查；不使用真实麦克风。"""

import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import tempfile

import pytest

if sys.platform == 'win32':
    pytest.skip('macOS管理脚本依赖POSIX进程、权限及fcntl', allow_module_level=True)


spec = importlib.util.spec_from_file_location(
    'macos_background', Path(__file__).resolve().parents[1] / 'scripts/macos_background.py',
)
background = importlib.util.module_from_spec(spec)
spec.loader.exec_module(background)


def test_occupied_port_is_detected_without_stopping_owner(monkeypatch):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        monkeypatch.setattr(background, 'PORT', listener.getsockname()[1])
        assert background.occupied()
        assert listener.fileno() >= 0


def test_closed_port_is_available(monkeypatch):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    monkeypatch.setattr(background, 'PORT', port)
    assert not background.occupied()


def test_stop_only_terminates_owned_process(tmp_path):
    program = tmp_path / 'idle.py'
    program.write_text('import time\ntime.sleep(60)\n')
    owned = subprocess.Popen([sys.executable, str(program)], start_new_session=True)
    unrelated = subprocess.Popen([sys.executable, str(program)], start_new_session=True)
    try:
        background.stop_process(owned)
        assert owned.poll() is not None
        assert unrelated.poll() is None
    finally:
        if owned.poll() is None:
            owned.kill()
            owned.wait()
        unrelated.terminate()
        unrelated.wait()


def test_stop_waits_through_cleanup_timeout(monkeypatch, tmp_path, capsys):
    state_file = tmp_path / 'state.json'
    state_file.write_text(json.dumps({'phase': 'stopped', 'message': '服务已停止'}))
    responses = iter([{'phase': 'stopping', 'message': '正在停止'},
                      TimeoutError(), ConnectionRefusedError()])

    def request(_):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(background, 'request', request)
    monkeypatch.setattr(background, 'STATE', state_file)
    monkeypatch.setattr(background.time, 'sleep', lambda _: None)
    monkeypatch.setattr(sys, 'argv', ['macos_background.py', 'stop'])
    assert background.main() == 0
    assert '服务已停止' in capsys.readouterr().out


def test_control_close_is_connection_error(monkeypatch):
    directory = tempfile.TemporaryDirectory(prefix='cw-', dir='/tmp')
    path = Path(directory.name) / 'control.sock'
    monkeypatch.setattr(background, 'SOCKET', path)
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        listener.listen()

        def close_connection():
            with listener.accept()[0] as connection:
                connection.recv(32)

        thread = threading.Thread(target=close_connection)
        thread.start()
        try:
            with pytest.raises(ConnectionError):
                background.request('status')
        finally:
            thread.join(timeout=2)
    directory.cleanup()


def test_default_data_matches_app_without_changing_parent_environment(monkeypatch):
    monkeypatch.delenv('CAPSWRITER_HOME', raising=False)
    monkeypatch.setenv('CAPSWRITER_MANAGED_TOKEN', 'old-launcher-token')
    monkeypatch.setenv('CAPSWRITER_SERVER_BIND', '0.0.0.0')
    env = background.launch_environment()
    assert env['CAPSWRITER_HOME'] == str(Path.home() / 'Library/Application Support/CapsWriter-Offline')
    assert env['CAPSWRITER_SERVER_BIND'] == '127.0.0.1'
    assert 'CAPSWRITER_MANAGED_TOKEN' not in env
    import os
    assert os.environ['CAPSWRITER_MANAGED_TOKEN'] == 'old-launcher-token'


def test_explicit_data_directory_is_preserved(monkeypatch, tmp_path):
    monkeypatch.setenv('CAPSWRITER_HOME', str(tmp_path))
    assert background.launch_environment()['CAPSWRITER_HOME'] == str(tmp_path)


@pytest.mark.parametrize('error', [TimeoutError(), BrokenPipeError(), UnicodeDecodeError('utf8', b'\xff', 0, 1, 'invalid')])
def test_failed_control_query_does_not_stop_running_service(error):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, _):
            pass

        def recv(self, _):
            if isinstance(error, BrokenPipeError):
                return b'status'
            raise error

        def sendall(self, _):
            raise error

    class Control:
        def accept(self):
            return Connection(), None

    state = {'phase': 'ready', 'message': '服务已就绪'}
    assert not background.handle_control(Control(), state)
    assert state['phase'] == 'ready'


def test_stop_timeout_is_not_reported_as_success(monkeypatch, capsys):
    def request(_):
        raise TimeoutError('控制连接超时')

    monkeypatch.setattr(background, 'request', request)
    monkeypatch.setattr(sys, 'argv', ['macos_background.py', 'stop'])
    assert background.main() == 1
    assert '超时' in capsys.readouterr().out


def test_stop_signal_during_status_query_is_preserved(monkeypatch, tmp_path):
    import signal
    from types import SimpleNamespace
    handlers = {}
    stopped = []
    polls = []
    directory = tempfile.TemporaryDirectory(prefix='cw-sig-', dir='/tmp')
    monkeypatch.setattr(background, 'RUNTIME', tmp_path)
    monkeypatch.setattr(background, 'STATE', tmp_path / 'state.json')
    monkeypatch.setattr(background, 'SOCKET', Path(directory.name) / 'control.sock')
    monkeypatch.setattr(background.signal, 'signal', lambda sig, fn: handlers.update({sig: fn}))
    monkeypatch.setattr(background, 'occupied', lambda: False)
    monkeypatch.setattr(background.subprocess, 'Popen', lambda *a, **k: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(background, 'stop_process', stopped.append)

    def select_control(readers, *_):
        polls.append(True)
        assert len(polls) == 1, '停止信号被丢弃后仍在循环'
        return readers, [], []

    def status_with_stop_signal(*_):
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return False

    monkeypatch.setattr(background.select, 'select', select_control)
    monkeypatch.setattr(background, 'handle_control', status_with_stop_signal)
    background.serve(server_only=True)
    assert json.loads((tmp_path / 'state.json').read_text())['phase'] == 'stopped'
    assert len(stopped) == 1
    directory.cleanup()


def test_fast_start_failure_reports_this_daemons_cause(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace
    state_file = tmp_path / 'state.json'
    state_file.write_text(json.dumps({'phase': 'failed', 'message': '6016 端口已被其他服务占用',
                                     'server_only': True, 'manager_pid': 321}))

    def refused(_):
        raise ConnectionRefusedError()

    monkeypatch.setattr(background, 'request', refused)
    monkeypatch.setattr(background, 'RUNTIME', tmp_path)
    monkeypatch.setattr(background, 'STATE', state_file)
    monkeypatch.setattr(background.subprocess, 'Popen', lambda *a, **k: SimpleNamespace(pid=321, poll=lambda: 1))
    monkeypatch.setattr(background.time, 'sleep', lambda _: None)
    monkeypatch.setattr(sys, 'argv', ['macos_background.py', 'start', '--server-only'])
    assert background.main() == 1
    assert '6016 端口已被其他服务占用' in capsys.readouterr().out
