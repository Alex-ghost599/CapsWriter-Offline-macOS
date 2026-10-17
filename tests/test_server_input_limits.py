from base64 import b64encode
import socket
import sys

import pytest

from config_server import ServerConfig as Config
from core.protocol import AudioMessage
from core.server.connection.server_manager import SocketManager
from core.server.connection.ws_recv import AudioCache, validate_audio_message


def make_message(**overrides):
    values = {
        'task_id': 'task-1',
        'source': 'mic',
        'data': b64encode(b'\0\0\0\0').decode('ascii'),
        'is_final': False,
        'time_start': 1.0,
        'seg_duration': 60,
        'seg_overlap': 4,
    }
    values.update(overrides)
    return AudioMessage(**values)


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS server default')
def test_macos_server_defaults_to_loopback():
    assert Config.addr == '127.0.0.1'


def test_server_port_check_still_rejects_an_active_listener(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(Config, 'addr', '127.0.0.1')
        monkeypatch.setattr(Config, 'port', str(port))

        assert SocketManager(None)._check_port() is False


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX immediate-restart behavior')
def test_server_port_check_allows_immediate_restart(monkeypatch):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]

    client = socket.create_connection(('127.0.0.1', port))
    connection, _ = listener.accept()
    connection.close()
    client.close()
    listener.close()

    monkeypatch.setattr(Config, 'addr', '127.0.0.1')
    monkeypatch.setattr(Config, 'port', str(port))

    assert SocketManager(None)._check_port() is True


@pytest.mark.parametrize(
    'overrides',
    [
        {'source': 'network'},
        {'seg_duration': 0},
        {'seg_duration': Config.max_segment_duration + 1},
        {'seg_overlap': Config.max_segment_overlap + 1},
        {'context': 'x' * (Config.max_context_chars + 1)},
    ],
)
def test_rejects_invalid_audio_message_values(overrides):
    with pytest.raises(ValueError):
        validate_audio_message(make_message(**overrides))


def test_audio_cache_enforces_total_duration(monkeypatch):
    monkeypatch.setattr(Config, 'max_audio_seconds', 1)
    cache = AudioCache()

    with pytest.raises(ValueError):
        cache.append(b'\0' * (64000 + 4))


def test_audio_cache_rejects_mixed_task_sequence():
    cache = AudioCache()
    cache.validate_sequence(make_message(task_id='task-1'))

    with pytest.raises(ValueError, match='混合'):
        cache.validate_sequence(make_message(task_id='task-2'))
