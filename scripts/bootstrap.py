"""Portable first-run setup. Subsequent launches skip unchanged dependencies."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 11)


def environment_python(root=ROOT, platform=sys.platform):
    return root / '.venv' / ('Scripts/python.exe' if platform == 'win32' else 'bin/python')


def fingerprint(root=ROOT):
    return hashlib.sha256((root / 'requirements.txt').read_bytes()).hexdigest()


@contextlib.contextmanager
def setup_lock(root=ROOT):
    with (root / '.yannian-setup.lock').open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'1'); handle.flush()
        acquired = False
        deadline = time.monotonic() + 600
        while not acquired:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Another setup is still running. Please wait and retry.')
                time.sleep(.3)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def prepare(root=ROOT):
    if sys.version_info[:2] < MIN_PYTHON:
        raise RuntimeError('Python 3.11 or newer is required: https://www.python.org/downloads/')
    python = environment_python(root)
    with setup_lock(root):
        if not python.is_file():
            if (root / '.venv').exists():
                raise RuntimeError('This .venv belongs to another platform or is incomplete. Rename .venv and retry; keep data/.')
            print('Creating the local Python environment...', flush=True)
            subprocess.run([sys.executable, '-m', 'venv', str(root / '.venv')], check=True, cwd=root)
        subprocess.run([str(python), '-c', 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'], check=True, cwd=root)
        marker = root / '.venv' / 'yannian-setup.json'
        expected = {'requirements_sha256': fingerprint(root), 'platform': sys.platform, 'python': list(sys.version_info[:2])}
        try:
            ready = json.loads(marker.read_text(encoding='utf-8')) == expected
        except (OSError, ValueError):
            ready = False
        if not ready:
            print('Installing / updating dependencies (first launch may take a few minutes)...', flush=True)
            subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(root / 'requirements.txt')], check=True, cwd=root)
            marker.write_text(json.dumps(expected), encoding='utf-8')
    return python


def main():
    parser = argparse.ArgumentParser(description='Set up and quickly launch Yannian on Windows, macOS or Linux.')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--foreground', action='store_true', help='Keep the server in this terminal; Ctrl+C stops it.')
    args = parser.parse_args()
    try:
        python = prepare()
        command = [str(python), str(ROOT / ('run.py' if args.foreground else 'launch.pyw'))]
        if args.no_browser:
            command.append('--no-browser')
        subprocess.run(command, check=True, cwd=ROOT)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print('Yannian startup failed: ' + str(exc), file=sys.stderr)
        print('Check Python 3.11+, network access and write permission for the project folder. Your data/ is kept.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
