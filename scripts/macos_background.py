"""固定环境的 macOS 后台入口；仅管理自身启动的进程。"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path.home() / 'Library/Application Support/CapsWriter-Offline/script-runtime'
SOCKET = RUNTIME / 'control.sock'
STATE = RUNTIME / 'state.json'
PORT = 6016


def request(command):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(2)
        connection.connect(str(SOCKET))
        connection.sendall(command.encode())
        response = connection.recv(8192)
        if not response:
            raise ConnectionError('后台控制连接已关闭')
        return json.loads(response)


def handle_control(control, state):
    # 查询端关闭或超时只影响本次连接，不能触发后台服务清理。
    with control.accept()[0] as connection:
        connection.settimeout(2)
        try:
            command = connection.recv(32).decode()
        except (OSError, UnicodeError):
            return False
        if command not in ('stop', 'status'):
            return False
        stopping = command == 'stop'
        if stopping:
            state.update(phase='stopping', message='正在停止服务')
        try:
            connection.sendall(json.dumps(dict(state), ensure_ascii=False).encode())
        except OSError:
            pass
        return stopping


def stop_process(process):
    # 现有入口通过两次 SIGINT 正常退出；随后清理同组模型子进程。
    for sig, delay in ((signal.SIGINT, .15), (signal.SIGINT, 3),
                       (signal.SIGTERM, 1), (signal.SIGKILL, .1)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            # macOS 对仅剩已退出进程的组也可能返回 EPERM。
            # 活进程的权限错误仍然向上传播，不能把停止失败当成功。
            if process.poll() is None:
                raise
            break
        time.sleep(delay)
    process.wait(timeout=5)


def occupied():
    with socket.socket() as probe:
        probe.settimeout(.2)
        return probe.connect_ex(('127.0.0.1', PORT)) == 0


def owns_listener(process):
    return subprocess.run(
        ['/usr/sbin/lsof', '-a', '-p', str(process.pid),
         f'-iTCP:{PORT}', '-sTCP:LISTEN', '-t'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=3, check=False,
    ).returncode == 0


def launch_environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('CAPSWRITER_MANAGED_'):
            del env[key]
    # 源码模式原本写入项目根；迁移入口沿用 App 数据位置，保留热词与日记。
    env.setdefault('CAPSWRITER_HOME', str(Path.home() / 'Library/Application Support/CapsWriter-Offline'))
    env.update(PYTHONUNBUFFERED='1', CAPSWRITER_SERVER_BIND='127.0.0.1',
               CAPSWRITER_SERVER_ADDR='127.0.0.1',
               CAPSWRITER_SERVER_PORT=str(PORT), CAPSWRITER_MODEL_TYPE='paraformer')
    return env


def serve(server_only):
    os.umask(0o077)
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUNTIME / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        children, logs = [], []
        state = {'phase': 'starting', 'message': '正在加载本地模型',
                 'server_only': server_only, 'manager_pid': os.getpid(),
                 'audio_device': os.environ.get('CAPSWRITER_AUDIO_DEVICE') or None,
                 'microphone_source': os.environ.get('CAPSWRITER_MICROPHONE_SOURCE') or None}

        def save():
            temporary = STATE.with_suffix('.tmp')
            temporary.write_text(json.dumps(state, ensure_ascii=False))
            temporary.replace(STATE)

        env = launch_environment()

        def launch(name):
            log = (RUNTIME / f'{name}.log').open('w')
            logs.append(log)
            child = subprocess.Popen(
                [sys.executable, str(ROOT / f'start_{name}.py')],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
            children.append(child)
            return child

        stopping = False

        def interrupted(*_):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        SOCKET.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX) as control:
            control.bind(str(SOCKET))
            control.listen(4)
            try:
                save()
                if occupied():
                    raise RuntimeError('6016 端口已被其他服务占用；请先退出旧 App 或手动服务。')
                server = launch('server')
                client = None
                deadline = time.monotonic() + 120
                while not stopping:
                    if select.select([control], [], [], .25)[0]:
                        if handle_control(control, state):
                            stopping = True
                            save()
                    if stopping:
                        break
                    if any(child.poll() is not None for child in children):
                        raise RuntimeError('服务或客户端已退出。请查看日志中的权限或设备错误。')
                    if state['phase'] == 'starting' and owns_listener(server):
                        if server_only:
                            state.update(phase='ready', message='本地转文字服务已就绪（未开启麦克风）')
                        elif client is None:
                            client = launch('client')
                            state['message'] = '正在启动输入客户端；首次请允许系统权限提示'
                        elif '已连接服务端:' in (RUNTIME / 'client.log').read_text(errors='replace'):
                            state.update(phase='ready', message='后台输入已就绪，按住右 Shift 说话，松开输入')
                        save()
                    if state['phase'] != 'ready' and time.monotonic() > deadline:
                        raise RuntimeError('启动超时；请检查权限与日志，处理后重新双击启动。')
            except Exception as exc:
                state.update(phase='failed', message=str(exc))
            finally:
                errors = []
                for child in reversed(children):
                    try:
                        stop_process(child)
                    except Exception as exc:
                        errors.append(str(exc))
                for log in logs:
                    log.close()
                if errors:
                    state.update(phase='failed', message='停止过程出错：' + '; '.join(errors))
                elif state['phase'] != 'failed':
                    state.update(phase='stopped', message='本脚本启动的服务已停止')
                save()
                SOCKET.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'stop', 'status', '_serve'], default='start', nargs='?')
    parser.add_argument('--server-only', action='store_true')
    args = parser.parse_args()
    if args.command == '_serve':
        serve(args.server_only)
        return 0
    try:
        state = request('stop' if args.command == 'stop' else 'status')
    except (FileNotFoundError, ConnectionRefusedError):
        state = None
    except (OSError, ValueError) as exc:
        print('后台控制请求失败：', exc)
        return 1
    if args.command == 'start' and state is None:
        os.umask(0o077)
        RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = [sys.executable, __file__, '_serve']
        if args.server_only:
            command.append('--server-only')
        with (RUNTIME / 'launcher.log').open('w') as log:
            supervisor = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                          stdout=log, stderr=log, start_new_session=True)
        for _ in range(20):
            try:
                state = request('status')
                break
            except (OSError, ValueError):
                time.sleep(.1)
        if state is None:
            # 快速失败可能先移除 socket；只接受本次子进程留下的最终状态。
            try:
                final_state = json.loads(STATE.read_text())
                if supervisor.poll() is not None and final_state.get('manager_pid') == supervisor.pid:
                    state = final_state
            except (OSError, ValueError):
                pass
            if state is None:
                print('后台启动失败，请查看：', RUNTIME / 'launcher.log')
                return 1
    if args.command == 'start':
        if state['server_only'] != args.server_only:
            print('后台已经运行另一种模式；请先 stop，再启动需要的模式。')
            return 1
        print(state['message'], flush=True)
        for _ in range(130):
            if state['phase'] != 'starting':
                break
            time.sleep(1)
            try:
                state = request('status')
            except (OSError, ValueError):
                state = json.loads(STATE.read_text())
        print(state['message'])
        print('日志目录：', RUNTIME)
        return 0 if state['phase'] == 'ready' else 1
    if args.command == 'stop' and state is not None:
        for _ in range(25):
            time.sleep(.5)
            try:
                request('status')
            except TimeoutError:
                # 清理模型期间控制端不再处理请求，等待最终状态落盘。
                continue
            except OSError:
                state = json.loads(STATE.read_text())
                break
        print(state['message'])
        return 0 if state['phase'] == 'stopped' else 1
    print(state['message'] if state else '当前没有本脚本管理的后台服务')
    return 0


if __name__ == '__main__':
    sys.exit(main())
