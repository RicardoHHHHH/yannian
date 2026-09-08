"""Open Yannian, starting one hidden server only when it isn't already running."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def service_state(url):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/health", timeout=1.5) as response:
            result = json.load(response)
        return "ready" if result.get("app") in {"yannian-workbench", "yanji-workbench"} else "occupied"
    except urllib.error.HTTPError:
        return "occupied"
    except (OSError, ValueError):
        return "absent"


@contextlib.contextmanager
def startup_lock(path):
    with path.open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"1"); handle.flush()
        acquired = False
        deadline = time.monotonic() + 35
        while not acquired and time.monotonic() < deadline:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                time.sleep(.3)
        if not acquired: raise RuntimeError("工作台仍在启动，请稍后再试。")
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt": msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def ensure_server(port):
    url = f"http://127.0.0.1:{port}"
    state = service_state(url)
    if state == "ready": return url
    if state == "occupied": raise RuntimeError(f"端口 {port} 已被其他服务占用，请设置 YANNIAN_PORT 后重试。")
    runtime = ROOT / "data" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    with startup_lock(runtime / "startup.lock"):
        state = service_state(url)
        if state == "ready": return url
        if state == "occupied": raise RuntimeError(f"端口 {port} 已被其他服务占用。")
        binary = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not binary.is_file(): raise RuntimeError("缺少运行环境，请先运行 start.cmd 安装依赖。")
        with (runtime / "server.log").open("ab") as log:
            process = subprocess.Popen([str(binary), str(ROOT / "run.py"), "--no-browser", "--port", str(port)],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0,
                start_new_session=os.name != "nt")
        for _ in range(60):
            if service_state(url) == "ready": return url
            if process.poll() is not None: break
            time.sleep(.5)
        raise RuntimeError("工作台未能正常启动，请查看 data/runtime/server.log。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        address = ensure_server(int(os.environ.get("YANNIAN_PORT", os.environ.get("YANJI_PORT", "8765"))))
        if args.no_browser:
            print("Yannian ready: " + address)
        else:
            webbrowser.open(address)
    except Exception as exc:
        if os.name == "nt" and not args.no_browser:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(exc), "研念启动失败", 0x10)
        else:
            print(str(exc), file=sys.stderr)
        sys.exit(1)
