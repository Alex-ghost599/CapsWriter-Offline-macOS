"""仅操作当前用户精确 SonoBus 应用的单机控制端。"""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

APP = Path.home() / 'Applications/SonoBus.app'
BINARY = APP / 'Contents/MacOS/SonoBus'
SETTINGS = Path.home() / 'Library/Application Support/SonoBus/SonoBus.settings'
BACKUPS = Path.home() / 'Library/Application Support/CapsWriter-Offline/script-runtime/sonobus-backups'
REMOTE_SETUP = BACKUPS.parent / 'remote-enabled.settings'
SERVER = '10.10.0.5:10999'
GROUP = 'Alex-CapsWriter-20261006-5a8d72'
URL = f'sonobus://{SERVER}/?g={GROUP}'


class EndpointStartError(RuntimeError):
    def __init__(self, cause, cleanup):
        super().__init__(f'SonoBus 启动未完成：{cause}')
        self.cleanup = cleanup


def processes():
    output = subprocess.check_output(
        ['/bin/ps', '-axww', '-o', 'pid=,uid=,lstart=,comm='], text=True,
        env=dict(os.environ, LC_ALL='C'), timeout=5)
    result = []
    for line in output.splitlines():
        fields = line.split(None, 7)
        if len(fields) == 8 and fields[1] == str(os.getuid()) and fields[7] == str(BINARY):
            result.append({'pid': int(fields[0]), 'started': ' '.join(fields[2:7])})
    return result


def validate_route(role):
    if not BINARY.is_file():
        raise RuntimeError(f'SonoBus 应用不存在：{APP}')
    xml = ET.parse(SETTINGS)
    device = xml.find('.//DEVICESETUP')
    if device is None:
        raise RuntimeError('SonoBus 未保存音频设备配置，请先在应用中配置')
    key, expected = ('audioInputDeviceName', 'UGREEN CM564 USB Audio') if role == 'remote' else ('audioOutputDeviceName', 'BlackHole 2ch')
    if device.get(key, '').strip() != expected:
        raise RuntimeError(f'SonoBus 保存的设备与此流程不符：需要 {expected}，请手动核对')


def mute_parameters(xml):
    result = []
    for name in ('mastsendmute', 'mastinmute'):
        values = xml.findall(f'.//PARAM[@id="{name}"]')
        if len(values) != 1:
            raise RuntimeError(f'SonoBus 静音配置 {name} 缺失或重复')
        result.append(values[0])
    return result


def write_settings(xml, destination):
    if processes():
        raise RuntimeError('SonoBus 仍运行，不能改写其保存配置')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        target = Path(temporary.name)
    try:
        xml.write(target, encoding='UTF-8', xml_declaration=True)
        if processes():
            raise RuntimeError('SonoBus 在保存配置期间重开，保留原配置')
        target.replace(destination)
    finally:
        target.unlink(missing_ok=True)


def save_muted_settings():
    # 本机接收保持双静音；远端普通配置也保存双静音，启用由独立预设负责。
    if processes():
        raise RuntimeError('SonoBus 仍运行，不能改写其保存配置')
    xml = ET.parse(SETTINGS)
    values = mute_parameters(xml)
    if all(value.get('value') == '1.0' for value in values):
        return
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = BACKUPS / f'{time.time_ns()}.settings'
    shutil.copy2(SETTINGS, backup)
    backup.chmod(0o600)
    for value in values:
        value.set('value', '1.0')
    write_settings(xml, SETTINGS)


def prepare_remote_setup():
    save_muted_settings()
    xml = ET.parse(SETTINGS)
    for value in mute_parameters(xml):
        value.set('value', '0.0')
    write_settings(xml, REMOTE_SETUP)
    # 启动预设未可靠覆盖已恢复的静音状态；原生启动配置同步启用，stop再恢复双静音。
    write_settings(xml, SETTINGS)
    return REMOTE_SETUP


def start(role):
    validate_route(role)
    previous = processes()
    restarted = False
    if previous:
        # 已有实例只处理 URL，不重载保存配置；重启专用应用确保双端静音状态生效。
        try:
            stopped = stop(role, only=previous)
            if stopped['remaining']:
                raise RuntimeError('远端 SonoBus 未完全退出，不能加载麦克风预设')
        except Exception as exc:
            raise EndpointStartError(exc, {'status': 'unknown', 'phase': 'restart'}) from exc
        previous = []
        restarted = True
    if role == 'local':
        save_muted_settings()
    name = 'macmini2-Mic' if role == 'remote' else 'Current-Mac-Receive'
    # 先初始化窗口和预设，再投递原生 URL，避免启动时连接请求丢失。
    command = ['/usr/bin/open', '-a', str(APP), '--args', '--username', name]
    if role == 'remote':
        command += ['--load-setup', str(prepare_remote_setup())]
    try:
        subprocess.run(command, check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        current = processes()
        while not current and time.monotonic() < deadline:
            time.sleep(.2)
            current = processes()
        if not current:
            raise RuntimeError('SonoBus 启动请求已发出，但未检测到应用进程')
        if not previous:
            subprocess.run(['/usr/bin/open', '-a', str(APP), URL], check=True, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception as exc:
        if previous:
            cleanup = {'status': 'preexisting_preserved'}
        else:
            try:
                identities = processes()
                if not identities:
                    # open 可能仍在异步启动；空列表不足以证明请求已取消。
                    raise RuntimeError('无法确认启动请求是否仍会创建进程')
                result = stop(role, only=identities)
                if result['remaining']:
                    raise RuntimeError('仍有 SonoBus 实例，未确认回退完成')
                cleanup = {'status': 'rolled_back', 'processes': identities}
            except Exception as cleanup_exc:
                cleanup = {'status': 'unknown', 'error': str(cleanup_exc)}
        raise EndpointStartError(exc, cleanup) from exc
    return {'ok': True, 'created': not previous, 'processes': current,
            'connection_requested': True, 'existing_instance': bool(previous),
            'restarted_existing': restarted, 'microphone_enable_requested': role == 'remote'}


def terminate(identity):
    # 每次发信号前重查精确路径、用户和启动时间，过期 PID 不构成终止依据。
    for sig, delay in ((signal.SIGTERM, 3), (signal.SIGKILL, 1)):
        if identity not in processes():
            return
        try:
            os.kill(identity['pid'], sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if identity not in processes():
                return
            time.sleep(.1)
    if identity in processes():
        raise RuntimeError(f'SonoBus 进程 {identity["pid"]} 未退出')


def stop(role, only=None):
    current = processes()
    selected = current if only is None else [p for p in current if p in only]
    for identity in selected:
        terminate(identity)
    remaining = processes()
    if role == 'remote' and not remaining:
        save_muted_settings()
    if only is None and remaining:
        raise RuntimeError('SonoBus 仍有运行进程，请再次停止或人工退出')
    return {'ok': True, 'stopped': selected, 'remaining': remaining, 'method': 'exact-identity SIGTERM; bounded SIGKILL fallback'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['start', 'stop', 'status'])
    parser.add_argument('role', choices=['local', 'remote'])
    parser.add_argument('--only', type=json.loads)
    args = parser.parse_args()
    try:
        if args.role == 'remote':
            name = subprocess.check_output(['/usr/sbin/scutil', '--get', 'ComputerName'], text=True, timeout=5).strip()
            if name != 'Alex_Mac_mini_2' or Path.home() != Path('/Users/alex'):
                raise RuntimeError('SSH 目标身份与已授权 macmini2/alex 不匹配，停止操作')
        if args.operation == 'start':
            result = start(args.role)
        elif args.operation == 'stop':
            result = stop(args.role, args.only)
        else:
            result = {'ok': True, 'processes': processes()}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}
        if args.operation == 'start':
            result['cleanup'] = getattr(exc, 'cleanup', {'status': 'not_started'})
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
