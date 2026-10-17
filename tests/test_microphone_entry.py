"""选择、切换拒绝和双端失败清理；RPC/硬件作为外部依赖替身。"""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

if sys.platform == 'win32':
    pytest.skip('macOS管理脚本依赖POSIX进程、权限及fcntl', allow_module_level=True)

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('capswriter_entry', SCRIPTS / 'capswriter_entry.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def test_menu_reprompts_then_selects_local():
    assert entry.choose_microphone(io.StringIO('x\n2\n'), io.StringIO()) == 'local'


def test_closed_menu_cancels_without_default_capture():
    with pytest.raises(RuntimeError, match='取消'):
        entry.choose_microphone(io.StringIO(''), io.StringIO())


def test_local_mode_overrides_inherited_remote_device(monkeypatch):
    monkeypatch.setenv('CAPSWRITER_AUDIO_DEVICE', 'BlackHole 2ch')
    monkeypatch.setattr(entry, 'default_input_device', lambda: 'Omi Recorder Audio Driver')
    env = entry.microphone_environment('local')
    assert env['CAPSWRITER_AUDIO_DEVICE'] == 'Omi Recorder Audio Driver'
    assert env['CAPSWRITER_MICROPHONE_SOURCE'] == 'local'
    assert os.environ['CAPSWRITER_AUDIO_DEVICE'] == 'BlackHole 2ch'


def test_remote_device_is_explicit_blackhole(monkeypatch):
    monkeypatch.setenv('CAPSWRITER_AUDIO_DEVICE', 'Omi Recorder Audio Driver')
    env = entry.microphone_environment('remote')
    assert env['CAPSWRITER_AUDIO_DEVICE'] == 'BlackHole 2ch'


def test_device_switch_refuses_before_touching_sonobus(monkeypatch):
    monkeypatch.setattr(entry, 'background_state', lambda: {'server_only': False, 'audio_device': 'BlackHole 2ch',
                                                         'microphone_source': 'remote', 'phase': 'ready'})
    monkeypatch.setattr(entry, 'default_input_device', lambda: 'Omi Recorder Audio Driver')
    monkeypatch.setattr(entry, 'endpoint', lambda *a, **k: pytest.fail('切换拒绝前不应操作SonoBus'))
    with pytest.raises(RuntimeError, match='stop'):
        entry.start_microphone('local')


def test_second_app_failure_rolls_back_only_created_remote(monkeypatch, tmp_path):
    calls = []
    remote = {'ok': True, 'created': True, 'processes': [{'pid': 42, 'started': 'Tue Oct 6 12:00:00 2026'}]}

    def rpc(host, operation, only=None):
        calls.append((host, operation, only))
        if host == 'remote' and operation == 'start':
            return remote
        if host == 'local' and operation == 'start':
            raise RuntimeError('本机应用打开失败')
        return {'ok': True, 'remaining': []}

    monkeypatch.setattr(entry, 'endpoint', rpc)
    monkeypatch.setattr(entry, 'WORKFLOW', tmp_path / 'workflow.json')
    with pytest.raises(RuntimeError, match='本机应用打开失败'):
        entry.start_bridge()
    assert calls == [('remote', 'start', None), ('local', 'start', None),
                     ('remote', 'stop', remote['processes'])]
    state = json.loads((tmp_path / 'workflow.json').read_text())
    assert state['failed_endpoint']['host'] == 'local'
    assert any('未知' in reason for reason in state['cleanup_errors'])


def test_failure_does_not_stop_preexisting_remote_app(monkeypatch, tmp_path):
    calls = []

    def rpc(host, operation, only=None):
        calls.append((host, operation))
        if host == 'local':
            raise RuntimeError('失败')
        return {'ok': True, 'created': False, 'processes': [{'pid': 42, 'started': 'old'}]}

    monkeypatch.setattr(entry, 'endpoint', rpc)
    monkeypatch.setattr(entry, 'WORKFLOW', tmp_path / 'workflow.json')
    with pytest.raises(RuntimeError):
        entry.start_bridge()
    assert calls == [('remote', 'start'), ('local', 'start')]


def test_stop_checks_both_apps_even_when_capswriter_fails(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(entry, 'run_background', lambda *a, **k: 1)

    def rpc(host, operation, only=None):
        calls.append((host, operation))
        return {'ok': True, 'remaining': []}

    monkeypatch.setattr(entry, 'endpoint', rpc)
    monkeypatch.setattr(entry, 'WORKFLOW', tmp_path / 'workflow.json')
    assert entry.stop_all() == 1
    assert calls == [('remote', 'stop'), ('local', 'stop')]


def test_unreachable_remote_does_not_skip_local_cleanup(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(entry, 'run_background', lambda *a, **k: 0)

    def rpc(host, operation, only=None):
        calls.append((host, operation))
        if host == 'remote':
            raise RuntimeError('SSH不可达')
        return {'ok': True, 'remaining': []}

    monkeypatch.setattr(entry, 'endpoint', rpc)
    monkeypatch.setattr(entry, 'WORKFLOW', tmp_path / 'workflow.json')
    assert entry.stop_all() == 1
    assert calls == [('remote', 'stop'), ('local', 'stop')]


def test_explicit_legacy_device_in_terminal_does_not_open_menu(monkeypatch):
    monkeypatch.setenv('CAPSWRITER_AUDIO_DEVICE', 'BlackHole 2ch')
    monkeypatch.setattr(sys, 'argv', ['capswriter_entry.py', 'start'])
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(entry, 'choose_microphone', lambda *a: pytest.fail('已明确设备的旧命令应直接运行'))
    commands = []
    monkeypatch.setattr(entry, 'run_background', lambda args, **kw: commands.append(args) or 0)
    assert entry.main() == 0
    assert commands == [['start']]


def test_connect_timeout_retries_once_without_claiming_cleanup_unknown(monkeypatch):
    calls = []
    error = 'ssh: connect to host 10.10.0.5 port 22: Operation timed out\n'

    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=255, stdout='', stderr=error)

    monkeypatch.setattr(entry.subprocess, 'run', run)
    with pytest.raises(entry.EndpointError) as exc:
        entry.endpoint('remote', 'start')
    assert len(calls) == 2
    assert exc.value.cleanup['status'] == 'not_started'
    assert 'VPN' in str(exc.value) and '未执行' in str(exc.value)


def test_connect_timeout_can_recover_on_second_attempt(monkeypatch):
    responses = iter([
        SimpleNamespace(returncode=255, stdout='', stderr='ssh: connect to host 10.10.0.5 port 22: Operation timed out\n'),
        SimpleNamespace(returncode=0, stdout='{"ok": true, "processes": []}', stderr='')])
    calls = []

    def run(argv, **kw):
        calls.append(kw['input'])
        return next(responses)

    monkeypatch.setattr(entry.subprocess, 'run', run)
    assert entry.endpoint('remote', 'status')['ok']
    assert len(calls) == 2 and calls[0] == calls[1]


def test_lost_reply_after_remote_execution_does_not_retry(monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=255, stdout='', stderr='client_loop: send disconnect: Broken pipe\n')

    monkeypatch.setattr(entry.subprocess, 'run', run)
    with pytest.raises(entry.EndpointError) as exc:
        entry.endpoint('remote', 'start')
    assert len(calls) == 1
    assert exc.value.cleanup['status'] == 'unknown'


def test_remote_output_prevents_preconnect_classification(monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=255, stdout='已启动，回执损坏',
                               stderr='ssh: connect to host 10.10.0.5 port 22: Operation timed out\n')

    monkeypatch.setattr(entry.subprocess, 'run', run)
    with pytest.raises(entry.EndpointError) as exc:
        entry.endpoint('remote', 'start')
    assert len(calls) == 1
    assert exc.value.cleanup['status'] == 'unknown'
