"""精确进程归属、连接命令与单字段静音保存。"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

if sys.platform == 'win32':
    pytest.skip('macOS管理脚本依赖POSIX进程、权限及fcntl', allow_module_level=True)

spec = importlib.util.spec_from_file_location(
    'sonobus_endpoint', Path(__file__).resolve().parents[1] / 'scripts/sonobus_endpoint.py')
endpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(endpoint)


@pytest.fixture(autouse=True)
def app_location(monkeypatch):
    # 固定进程清单fixture的位置，避免测试依赖运行者的home目录。
    monkeypatch.setattr(endpoint, 'APP', Path('/Users/alex/Applications/SonoBus.app'))
    monkeypatch.setattr(endpoint, 'BINARY', Path('/Users/alex/Applications/SonoBus.app/Contents/MacOS/SonoBus'))


def test_process_inventory_excludes_other_apps_and_users(monkeypatch):
    text = ('101 501 Tue Oct 6 12:00:00 2026 /Users/alex/Applications/SonoBus.app/Contents/MacOS/SonoBus\n'
            '102 502 Tue Oct 6 12:00:00 2026 /Users/alex/Applications/SonoBus.app/Contents/MacOS/SonoBus\n'
            '103 501 Tue Oct 6 12:00:00 2026 /tmp/SonoBus.app/Contents/MacOS/SonoBus\n'
            '104 501 Tue Oct 6 12:00:00 2026 /usr/bin/python3\n')
    monkeypatch.setattr(endpoint.os, 'getuid', lambda: 501)
    monkeypatch.setattr(endpoint.subprocess, 'check_output', lambda *a, **k: text)
    assert [p['pid'] for p in endpoint.processes()] == [101]


def test_reused_pid_is_not_signalled(monkeypatch):
    old = {'pid': 101, 'started': 'Tue Oct 6 12:00:00 2026'}
    monkeypatch.setattr(endpoint, 'processes', lambda: [{'pid': 101, 'started': 'Tue Oct 6 12:05:00 2026'}])
    monkeypatch.setattr(endpoint.os, 'kill', lambda *a: pytest.fail('不能终止重用PID'))
    endpoint.terminate(old)


def test_fresh_launch_uses_native_private_group_url_and_identity(monkeypatch):
    commands = []
    inventory = iter([[], [{'pid': 101, 'started': 'new'}]])
    monkeypatch.setattr(endpoint, 'processes', lambda: next(inventory))
    monkeypatch.setattr(endpoint, 'validate_route', lambda role: None)
    monkeypatch.setattr(endpoint, 'prepare_remote_setup', lambda: Path('/tmp/remote-enabled.settings'))
    monkeypatch.setattr(endpoint.subprocess, 'run', lambda argv, **kw: commands.append(argv) or subprocess.CompletedProcess(argv, 0))
    result = endpoint.start('remote')
    assert result['created']
    assert commands[0] == ['/usr/bin/open', '-a', '/Users/alex/Applications/SonoBus.app',
                           '--args', '--username', 'macmini2-Mic',
                           '--load-setup', '/tmp/remote-enabled.settings']
    assert commands[1] == ['/usr/bin/open', '-a', str(endpoint.APP), endpoint.URL]


def test_existing_local_app_restarts_and_receives_native_url(monkeypatch):
    commands, live = [], [{'pid': 101, 'started': 'old'}]
    monkeypatch.setattr(endpoint, 'processes', lambda: list(live))
    monkeypatch.setattr(endpoint, 'validate_route', lambda role: None)
    monkeypatch.setattr(endpoint, 'save_muted_settings', lambda: None)

    def stopped(role, only=None):
        assert role == 'local' and only == [{'pid': 101, 'started': 'old'}]
        live.clear()
        return {'remaining': []}

    def opened(argv, **kw):
        commands.append(argv)
        live[:] = [{'pid': 102, 'started': 'new'}]

    monkeypatch.setattr(endpoint, 'stop', stopped)
    monkeypatch.setattr(endpoint.subprocess, 'run', opened)
    result = endpoint.start('local')
    assert result['created'] and result['restarted_existing']
    assert commands[-1] == ['/usr/bin/open', '-a', '/Users/alex/Applications/SonoBus.app',
                            'sonobus://10.10.0.5:10999/?g=Alex-CapsWriter-20261006-5a8d72']


def test_stop_restores_both_mutes_and_preserves_other_settings(monkeypatch, tmp_path):
    settings = tmp_path / 'SonoBus.settings'
    settings.write_text('<PROPERTIES><SonoBusAoO><PARAM id="mastsendmute" value="0.0"/>'
                        '<PARAM id="mastinmute" value="0.0"/><PARAM id="dry" value="0.25"/>'
                        '<Secret value="preserve"/></SonoBusAoO></PROPERTIES>')
    monkeypatch.setattr(endpoint, 'SETTINGS', settings)
    monkeypatch.setattr(endpoint, 'BACKUPS', tmp_path / 'backups')
    monkeypatch.setattr(endpoint, 'processes', lambda: [])
    endpoint.save_muted_settings()
    xml = ET.parse(settings)
    assert xml.find('.//PARAM[@id="mastsendmute"]').get('value') == '1.0'
    assert xml.find('.//PARAM[@id="mastinmute"]').get('value') == '1.0'
    assert xml.find('.//PARAM[@id="dry"]').get('value') == '0.25'
    assert xml.find('.//Secret').get('value') == 'preserve'
    assert list((tmp_path / 'backups').glob('*.settings'))


def test_running_app_prevents_settings_write(monkeypatch, tmp_path):
    settings = tmp_path / 'SonoBus.settings'
    settings.write_text('<PROPERTIES/>')
    monkeypatch.setattr(endpoint, 'SETTINGS', settings)
    monkeypatch.setattr(endpoint, 'processes', lambda: [{'pid': 101, 'started': 'old'}])
    with pytest.raises(RuntimeError):
        endpoint.save_muted_settings()
    assert settings.read_text() == '<PROPERTIES/>'


def test_open_success_then_ps_failure_rolls_back_new_instance(monkeypatch):
    live = []
    reads = []

    def inventory():
        reads.append(True)
        if len(reads) == 2:
            raise TimeoutError('ps查询失败')
        return list(live)

    def opened(*a, **k):
        live.append({'pid': 101, 'started': 'new'})

    def signal_owned(pid, sig):
        assert pid == 101
        live.clear()

    monkeypatch.setattr(endpoint, 'validate_route', lambda role: None)
    monkeypatch.setattr(endpoint, 'save_muted_settings', lambda: None)
    monkeypatch.setattr(endpoint, 'processes', inventory)
    monkeypatch.setattr(endpoint.subprocess, 'run', opened)
    monkeypatch.setattr(endpoint.os, 'kill', signal_owned)
    with pytest.raises(RuntimeError) as error:
        endpoint.start('local')
    assert live == []
    assert error.value.cleanup['status'] == 'rolled_back'


def test_post_open_ps_failure_reports_unknown_cleanup(monkeypatch):
    reads = []

    def inventory():
        reads.append(True)
        if len(reads) == 1:
            return []
        raise TimeoutError('无法查询进程')

    monkeypatch.setattr(endpoint, 'validate_route', lambda role: None)
    monkeypatch.setattr(endpoint, 'save_muted_settings', lambda: None)
    monkeypatch.setattr(endpoint, 'processes', inventory)
    monkeypatch.setattr(endpoint.subprocess, 'run', lambda *a, **k: None)
    monkeypatch.setattr(endpoint.os, 'kill', lambda *a: pytest.fail('身份未知不能发信号'))
    with pytest.raises(RuntimeError) as error:
        endpoint.start('local')
    assert error.value.cleanup['status'] == 'unknown'


def test_local_restart_failure_does_not_open_or_write_settings(monkeypatch):
    monkeypatch.setattr(endpoint, 'processes', lambda: [{'pid': 101, 'started': 'old'}])
    monkeypatch.setattr(endpoint, 'validate_route', lambda role: None)

    def stopped(*a, **k):
        raise TimeoutError('停止未确认')

    monkeypatch.setattr(endpoint, 'stop', stopped)
    monkeypatch.setattr(endpoint, 'save_muted_settings', lambda: pytest.fail('仍运行不能写设置'))
    monkeypatch.setattr(endpoint.subprocess, 'run', lambda *a, **k: pytest.fail('停止失败不能启动'))
    with pytest.raises(RuntimeError) as error:
        endpoint.start('local')
    assert error.value.cleanup['status'] == 'unknown'


def test_remote_startup_settings_and_preset_both_enable_input_and_send(monkeypatch, tmp_path):
    settings = tmp_path / 'SonoBus.settings'
    settings.write_text('<PROPERTIES><VALUE name="filterStateXML"><SonoBusAoO>'
                        '<PARAM id="mastsendmute" value="1.0"/><PARAM id="mastinmute" value="0.0"/>'
                        '<PARAM id="dry" value="0.25"/></SonoBusAoO></VALUE>'
                        '<VALUE name="audioSetup"><DEVICESETUP audioInputDeviceName="UGREEN CM564 USB Audio " '
                        'audioDeviceRate="48000"/></VALUE><Secret value="preserve"/></PROPERTIES>')
    monkeypatch.setattr(endpoint, 'SETTINGS', settings)
    monkeypatch.setattr(endpoint, 'BACKUPS', tmp_path / 'backups')
    monkeypatch.setattr(endpoint, 'REMOTE_SETUP', tmp_path / 'runtime/remote-enabled.settings', raising=False)
    monkeypatch.setattr(endpoint, 'processes', lambda: [])
    preset = endpoint.prepare_remote_setup()
    normal, enabled = ET.parse(settings), ET.parse(preset)
    for name in ('mastsendmute', 'mastinmute'):
        assert normal.find(f'.//PARAM[@id="{name}"]').get('value') == '0.0'
        assert enabled.find(f'.//PARAM[@id="{name}"]').get('value') == '0.0'
    assert enabled.find('.//PARAM[@id="dry"]').get('value') == '0.25'
    assert enabled.find('.//Secret').get('value') == 'preserve'
    assert enabled.find('.//DEVICESETUP').get('audioDeviceRate') == '48000'
    assert preset.stat().st_mode & 0o777 == 0o600


def test_remote_existing_app_restarts_before_loading_enabled_preset(monkeypatch, tmp_path):
    old, new = {'pid': 101, 'started': 'old'}, {'pid': 102, 'started': 'new'}
    live, events = [old], []
    preset = tmp_path / 'remote-enabled.settings'
    monkeypatch.setattr(endpoint, 'processes', lambda: list(live))
    monkeypatch.setattr(endpoint, 'validate_route', lambda role: None)

    def stopped(role, only=None):
        assert role == 'remote' and only == [old]
        events.append('stop-old')
        live.clear()
        return {'ok': True, 'remaining': []}

    def setup():
        assert not live
        events.append('preset')
        return preset

    def opened(argv, **kw):
        if argv[-1] == endpoint.URL:
            assert live == [new]
            events.append('connect')
            return
        assert argv[-2:] == ['--load-setup', str(preset)]
        events.append('open')
        live.append(new)

    monkeypatch.setattr(endpoint, 'stop', stopped)
    monkeypatch.setattr(endpoint, 'prepare_remote_setup', setup, raising=False)
    monkeypatch.setattr(endpoint.subprocess, 'run', opened)
    result = endpoint.start('remote')
    assert events == ['stop-old', 'preset', 'open', 'connect']
    assert result['created'] and result['restarted_existing'] and result['processes'] == [new]


def test_remote_setup_cannot_be_written_while_app_runs(monkeypatch, tmp_path):
    settings = tmp_path / 'SonoBus.settings'
    settings.write_text('<PROPERTIES/>')
    monkeypatch.setattr(endpoint, 'SETTINGS', settings)
    monkeypatch.setattr(endpoint, 'REMOTE_SETUP', tmp_path / 'remote-enabled.settings', raising=False)
    monkeypatch.setattr(endpoint, 'processes', lambda: [{'pid': 101, 'started': 'old'}])
    with pytest.raises(RuntimeError, match='仍运行'):
        endpoint.prepare_remote_setup()
    assert not (tmp_path / 'remote-enabled.settings').exists()
    assert settings.read_text() == '<PROPERTIES/>'


def test_remote_restart_stop_failure_reports_unknown_cleanup(monkeypatch):
    monkeypatch.setattr(endpoint, 'validate_route', lambda role: None)
    monkeypatch.setattr(endpoint, 'processes', lambda: [{'pid': 101, 'started': 'old'}])

    def stopped(*a, **k):
        raise TimeoutError('终止已发出但ps查询失败')

    monkeypatch.setattr(endpoint, 'stop', stopped)
    monkeypatch.setattr(endpoint.subprocess, 'run', lambda *a, **k: pytest.fail('失败后不能继续启动'))
    with pytest.raises(RuntimeError) as error:
        endpoint.start('remote')
    assert error.value.cleanup['status'] == 'unknown'


def test_fresh_connect_url_is_sent_after_process_exists(monkeypatch):
    live, events = [], []
    monkeypatch.setattr(endpoint, 'validate_route', lambda role: None)
    monkeypatch.setattr(endpoint, 'save_muted_settings', lambda: None)
    monkeypatch.setattr(endpoint, 'processes', lambda: list(live))

    def run(argv, **kw):
        if not live:
            assert endpoint.URL not in argv
            live.append({'pid': 101, 'started': 'new'})
            events.append('launch')
        else:
            assert argv == ['/usr/bin/open', '-a', str(endpoint.APP), endpoint.URL]
            events.append('connect')

    monkeypatch.setattr(endpoint.subprocess, 'run', run)
    endpoint.start('local')
    assert events == ['launch', 'connect']


@pytest.mark.parametrize('send_mute,input_mute', [('0.0', '0.0'), ('1.0', '0.0'), ('0.0', '1.0')])
def test_local_start_restores_mutes_before_launch(monkeypatch, tmp_path, send_mute, input_mute):
    settings = tmp_path / 'SonoBus.settings'
    settings.write_text(f'<PROPERTIES><SonoBusAoO><PARAM id="mastsendmute" value="{send_mute}"/>'
                        f'<PARAM id="mastinmute" value="{input_mute}"/><PARAM id="dry" value="0.25"/>'
                        '<Secret value="preserve"/></SonoBusAoO><DEVICESETUP '
                        'audioOutputDeviceName="BlackHole 2ch"/></PROPERTIES>')
    monkeypatch.setattr(endpoint, 'SETTINGS', settings)
    monkeypatch.setattr(endpoint, 'BACKUPS', tmp_path / 'backups')
    monkeypatch.setattr(endpoint, 'BINARY', tmp_path / 'SonoBus')
    endpoint.BINARY.touch()
    live = []
    monkeypatch.setattr(endpoint, 'processes', lambda: list(live))

    def opened(argv, **kw):
        xml = ET.parse(settings)
        for name in ('mastsendmute', 'mastinmute'):
            assert xml.find(f'.//PARAM[@id="{name}"]').get('value') == '1.0'
        assert xml.find('.//Secret').get('value') == 'preserve'
        assert xml.find('.//PARAM[@id="dry"]').get('value') == '0.25'
        assert xml.find('.//DEVICESETUP').get('audioOutputDeviceName') == 'BlackHole 2ch'
        if not live:
            live.append({'pid': 102, 'started': 'new'})

    monkeypatch.setattr(endpoint.subprocess, 'run', opened)
    assert endpoint.start('local')['created']
    assert list((tmp_path / 'backups').glob('*.settings'))


def test_local_existing_app_restarts_to_apply_mutes(monkeypatch, tmp_path):
    old, new = {'pid': 101, 'started': 'old'}, {'pid': 102, 'started': 'new'}
    settings = tmp_path / 'SonoBus.settings'
    settings.write_text('<PROPERTIES><PARAM id="mastsendmute" value="0.0"/>'
                        '<PARAM id="mastinmute" value="0.0"/>'
                        '<DEVICESETUP audioOutputDeviceName="BlackHole 2ch"/></PROPERTIES>')
    monkeypatch.setattr(endpoint, 'SETTINGS', settings)
    monkeypatch.setattr(endpoint, 'BACKUPS', tmp_path / 'backups')
    monkeypatch.setattr(endpoint, 'BINARY', tmp_path / 'SonoBus')
    endpoint.BINARY.touch()
    live, events = [old], []
    monkeypatch.setattr(endpoint, 'processes', lambda: list(live))

    def stopped(role, only=None):
        assert role == 'local' and only == [old]
        live.clear()
        events.append('stop')
        return {'remaining': []}

    def opened(argv, **kw):
        assert events
        xml = ET.parse(settings)
        assert all(xml.find(f'.//PARAM[@id="{name}"]').get('value') == '1.0'
                   for name in ('mastsendmute', 'mastinmute'))
        if not live:
            live.append(new)
        events.append('open')

    monkeypatch.setattr(endpoint, 'stop', stopped)
    monkeypatch.setattr(endpoint.subprocess, 'run', opened)
    result = endpoint.start('local')
    assert result['created'] and result['restarted_existing']
    assert result['processes'] == [new] and events[0] == 'stop'
