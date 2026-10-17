"""CapsWriter 麦克风选择及两端 SonoBus 联动入口。"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import macos_background as background

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = ROOT / 'scripts/sonobus_endpoint.py'
WORKFLOW = background.RUNTIME / 'microphone-workflow.json'


class EndpointError(RuntimeError):
    def __init__(self, message, cleanup):
        super().__init__(message)
        self.cleanup = cleanup


def choose_microphone(stream, output):
    output.write('请选择麦克风：\n  1 远端 macmini2（SonoBus → BlackHole）\n  2 本机当前默认输入\n')
    while True:
        output.write('输入 1 或 2：')
        output.flush()
        answer = stream.readline()
        if not answer:
            raise RuntimeError('已取消麦克风选择；非交互使用请指定 --microphone remote 或 local')
        if answer.strip() in ('1', '2'):
            return 'remote' if answer.strip() == '1' else 'local'
        output.write('选择无效，请重新输入。\n')


def default_input_device():
    import sounddevice as sd
    return sd.query_devices(kind='input')['name']


def microphone_environment(mode):
    env = os.environ.copy()
    env['CAPSWRITER_MICROPHONE_SOURCE'] = mode
    env['CAPSWRITER_AUDIO_DEVICE'] = 'BlackHole 2ch' if mode == 'remote' else default_input_device()
    return env


def background_state():
    try:
        return background.request('status')
    except (FileNotFoundError, ConnectionRefusedError):
        return None


def run_background(arguments, env=None):
    return subprocess.call([sys.executable, str(ROOT / 'scripts/macos_background.py'), *arguments],
                           env=env, stdin=subprocess.DEVNULL)


def endpoint(host, operation, only=None):
    arguments = [operation, host]
    if only is not None:
        arguments += ['--only', json.dumps(only)]
    if host == 'remote':
        # SSH 命令经过逐参数 shell quoting；源码走 stdin，不在远端安装运行环境。
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 'macmini2',
                   shlex.join(['python3', '-', *arguments])]
        code = ENDPOINT.read_text()
    else:
        command = [sys.executable, str(ENDPOINT), *arguments]
        code = None
    for attempt in range(2):
        completed = subprocess.run(command, input=code, text=True, capture_output=True, timeout=25)
        # 仅OpenSSH明确的TCP连接前失败可以重试；断连或丢回执可能已执行命令。
        not_connected = host == 'remote' and completed.returncode == 255 and not completed.stdout and re.fullmatch(
            r'ssh: connect to host \S+ port \d+: (?:Operation timed out|Connection refused|No route to host|Network is unreachable)',
            completed.stderr.strip())
        if not not_connected:
            break
        if attempt == 0:
            print('尚未建立到 macmini2 的 SSH 连接，自动重试一次。', file=sys.stderr)
            continue
        raise EndpointError('macmini2 SSH 连接未建立，本次未执行远端命令；'
                            '请确认 VPN 和远端主机在线后重试。远端既有运行状态未检查。'
                            f'原始错误：{completed.stderr.strip()}', {'status': 'not_started'})
    try:
        result = json.loads(completed.stdout)
    except ValueError as exc:
        raise EndpointError(f'{host} SonoBus 控制失败：{completed.stderr.strip() or completed.stdout.strip()}',
                            {'status': 'unknown'}) from exc
    if completed.returncode or not result.get('ok'):
        raise EndpointError(f'{host} SonoBus：{result.get("error", completed.stderr.strip())}',
                            result.get('cleanup', {'status': 'unknown'}))
    return result


def save_workflow(state):
    WORKFLOW.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = WORKFLOW.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(WORKFLOW)


def rollback_bridge(apps):
    errors = []
    for app in reversed(apps):
        if not app['created']:
            continue
        try:
            endpoint(app['host'], 'stop', only=app['processes'])
        except Exception as exc:
            errors.append(str(exc))
    return errors


def start_bridge():
    apps = []
    pending_host = None
    try:
        for host in ('remote', 'local'):
            pending_host = host
            app = dict(endpoint(host, 'start'), host=host)
            apps.append(app)
            pending_host = None
            save_workflow({'phase': 'starting', 'microphone': 'remote', 'apps': apps})
    except BaseException as exc:
        errors = rollback_bridge(apps)
        state = {'phase': 'failed', 'microphone': 'remote', 'apps': apps, 'cleanup_errors': errors}
        if pending_host:
            cleanup = getattr(exc, 'cleanup', {'status': 'unknown'})
            state['failed_endpoint'] = {'host': pending_host, 'error': str(exc), 'cleanup': cleanup}
            if cleanup.get('status') not in ('rolled_back', 'preexisting_preserved', 'not_started'):
                errors.append(f'{pending_host} SonoBus 清理状态未知，请在连接恢复后再次运行 stop 检查')
        save_workflow(state)
        if errors:
            raise RuntimeError(f'{exc}；回退未完成：{"; ".join(errors)}') from exc
        raise
    print('两端 SonoBus 已打开并请求加入私有组。请查看实际连接状态；已有其他组时手动连接。')
    print('远端已加载自动启用输入和发送的预设；本机输入和发送自动保持静音。两端已有专用实例会重启加载设置。')
    return apps


def start_microphone(mode):
    env = microphone_environment(mode)
    current = background_state()
    if current and (current.get('server_only') or current.get('audio_device') != env['CAPSWRITER_AUDIO_DEVICE']
                    or current.get('microphone_source') != mode):
        raise RuntimeError('后台正在使用不同或未知输入，请先运行 stop，再选择麦克风；没有切换设备')
    apps = start_bridge() if mode == 'remote' else []
    print(f'CapsWriter 输入：{env["CAPSWRITER_AUDIO_DEVICE"]}', flush=True)
    try:
        result = run_background(['start'], env=env)
        if result:
            raise RuntimeError('CapsWriter 启动失败，请查看后台日志中的权限或设备原因')
    except BaseException as exc:
        errors = []
        if current is None:
            try:
                if run_background(['stop']):
                    errors.append('CapsWriter 停止失败')
            except Exception as cleanup_exc:
                errors.append(str(cleanup_exc))
        errors.extend(rollback_bridge(apps))
        save_workflow({'phase': 'failed', 'microphone': mode, 'apps': apps, 'cleanup_errors': errors})
        if errors:
            raise RuntimeError(f'{exc}；清理未完成：{"; ".join(errors)}') from exc
        raise
    save_workflow({'phase': 'ready', 'microphone': mode, 'audio_device': env['CAPSWRITER_AUDIO_DEVICE'],
                   'apps': apps, 'bridge_verified': False})
    return 0


def stop_all():
    errors, results = [], {}
    try:
        if run_background(['stop']):
            errors.append('CapsWriter 未能确认停止')
    except Exception as exc:
        errors.append(str(exc))
    # Alex授权本stop检查已打开的两端专用应用，覆盖手动打开的本流程。
    for host in ('remote', 'local'):
        try:
            results[host] = endpoint(host, 'stop')
            print(f'{host} SonoBus 已检查并停止')
        except Exception as exc:
            errors.append(str(exc))
    save_workflow({'phase': 'failed' if errors else 'stopped', 'apps': results, 'errors': errors})
    if errors:
        print('部分停止未完成：' + '; '.join(errors), file=sys.stderr)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'stop', 'status'], nargs='?', default='start')
    parser.add_argument('--server-only', action='store_true')
    parser.add_argument('--microphone', choices=['remote', 'local'])
    args = parser.parse_args()
    try:
        if args.command == 'status':
            return run_background(['status'])
        if args.server_only:
            if args.command != 'start' or args.microphone:
                parser.error('--server-only 仅用于不选择麦克风的 start')
            return run_background(['start', '--server-only'])
        mode = args.microphone
        if args.command == 'start' and mode is None:
            if os.environ.get('CAPSWRITER_AUDIO_DEVICE'):
                # 兼容旧的显式设备命令，不自动启动网络音频应用。
                return run_background(['start'])
            mode = choose_microphone(sys.stdin, sys.stdout)
        background.RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (background.RUNTIME / 'entry.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('另一个启动/停止操作正在执行，请待其结束后重试') from None
            return stop_all() if args.command == 'stop' else start_microphone(mode)
    except (Exception, KeyboardInterrupt) as exc:
        print(f'操作未完成：{exc or "已取消"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
