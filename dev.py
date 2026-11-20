"""Run the current gate application locally with its existing cloud workspace."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web'
HOST = '127.0.0.1'


def available_port(requested: int | None) -> int:
    with socket.socket() as listener:
        try:
            listener.bind((HOST, requested or 0))
        except OSError as error:
            raise RuntimeError(f'端口 {requested} 已占用或不可用，请换一个 --port；不会停止原程序。') from error
        return listener.getsockname()[1]


def find_npm() -> str:
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise RuntimeError('未找到 npm，请先安装 Node.js 并重新打开终端。')
    return npm


def ensure_dependencies(npm: str, env: dict[str, str]) -> None:
    required = ('vite/bin/vite.js', 'typescript/bin/tsc', 'pyodide/pyodide.asm.wasm',
                'cp-sat-wasm/package.json', '@supabase/supabase-js/package.json',
                'coi-serviceworker/coi-serviceworker.js')
    if all((WEB / 'node_modules' / name).is_file() for name in required):
        return
    print('首次启动，正在按现有依赖清单准备运行环境…', flush=True)
    result = subprocess.run([npm, 'ci'], cwd=WEB, env=env, check=False)
    if result.returncode:
        raise RuntimeError('运行环境准备失败，请检查上方提示及网络后重试。')


def prepare_runtime(env: dict[str, str]) -> None:
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/prepare-browser.py')],
                            cwd=ROOT, env=env, check=False)
    if result.returncode:
        raise RuntimeError('浏览器运行文件准备失败，请检查上方提示后重试。')


def ensure_cloud(env: dict[str, str]) -> None:
    node = shutil.which('node', path=env['PATH'])
    if not node:
        raise RuntimeError('未找到 Node.js，请检查安装后重试。')
    check = [node, str(ROOT / 'scripts/check-cloud.mjs')]
    result = subprocess.run(check, cwd=ROOT, env=env, check=False)
    if result.returncode == 2:
        if os.name != 'nt':
            raise RuntimeError('请先安装登机口数据库登录审批更新后重试。')
        print('将运行登机口专用数据库升级工具，先备份再更新。首次需设置一次连接，以后复用。', flush=True)
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(ROOT / 'scripts/upgrade-database.ps1')],
                                cwd=ROOT, env=env, check=False)
        if result.returncode:
            raise RuntimeError('云端升级尚未完成，已停止；原资料保留，可重新运行 dev.py。')
        result = subprocess.run(check, cwd=ROOT, env=env, check=False)
    if result.returncode:
        raise RuntimeError('云端连接检查尚未通过，请根据上方提示处理后重试。')


def wait_until_ready(process: subprocess.Popen, url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'云端页面启动失败（退出码 {process.returncode}），请检查上方云端配置或启动提示。')
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError):
            time.sleep(.2)
    raise RuntimeError('等待云端页面启动超时，请检查上方提示。')


def open_browser(url: str) -> None:
    if sys.platform == 'win32':
        for folder in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            if not os.environ.get(folder):
                continue
            for name, option in (('Microsoft/Edge/Application/msedge.exe', '--inprivate'),
                                 ('Google/Chrome/Application/chrome.exe', '--incognito')):
                browser = Path(os.environ[folder]) / name
                if browser.is_file():
                    subprocess.Popen([str(browser), option, '--new-window', url],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
    if not webbrowser.open(url, new=1):
        print('未能自动打开浏览器，请手动打开上方地址。', flush=True)


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == 'nt':
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='启动登机口排班云端工作区，登录后可显式保存到云端。')
    parser.add_argument('--preview', action='store_true', help='构建当前云端版本后预览；默认使用开发服务。')
    parser.add_argument('--port', type=int, help='指定端口，默认自动选择空闲端口。')
    parser.add_argument('--no-browser', action='store_true', help='只启动服务，不自动打开浏览器。')
    args = parser.parse_args(argv)
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error('--port 必须在 1 到 65535 之间。')
    process = None
    try:
        npm = find_npm()
        port = available_port(args.port)
        temporary = ROOT / '.tmp'
        temporary.mkdir(exist_ok=True)
        env = dict(os.environ, TEMP=str(temporary), TMP=str(temporary))
        # build:cloud invokes "python" itself; use this launcher interpreter.
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
        ensure_dependencies(npm, env)
        ensure_cloud(env)
        if args.preview:
            print('正在构建当前云端版本…', flush=True)
            result = subprocess.run([npm, 'run', 'build:cloud'], cwd=WEB, env=env, check=False)
            if result.returncode:
                return result.returncode
        else:
            prepare_runtime(env)
        script = 'preview:cloud' if args.preview else 'dev:cloud'
        url = f'http://{HOST}:{port}/'
        command = [npm, 'run', script, '--', '--host', HOST, '--port', str(port), '--strictPort']
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        process = subprocess.Popen(command, cwd=WEB, env=env, **options)
        wait_until_ready(process, url)
        print(f'已启动登机口云端入口：{url}', flush=True)
        print('登录后读取已有云端资料；修改后点击“保存到云端”。本地旧资料不会自动上传。', flush=True)
        print('关闭此窗口或按 Ctrl+C 停止本次服务。', flush=True)
        if not args.no_browser:
            open_browser(url)
        return process.wait()
    except KeyboardInterrupt:
        print('\n正在停止本次服务…', flush=True)
        return 0
    except (RuntimeError, OSError) as error:
        print(f'启动失败：{error}', file=sys.stderr, flush=True)
        return 1
    finally:
        if process is not None:
            stop_process(process)


if __name__ == '__main__':
    raise SystemExit(main())
