#!/bin/bash
set -eu
cd "$(dirname "$0")"

# Finder and desktop launchers may have a shorter PATH than an interactive shell.
export PATH="${PATH}:/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin"
task_python=""
if [ -n "${YANNIAN_PYTHON:-}" ]; then
    task_python="$YANNIAN_PYTHON"
else
    for candidate in .venv/bin/python python3.14 python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
            task_python="$candidate"
            break
        fi
    done
fi
if [ -z "$task_python" ]; then
    echo 'Please install Python 3.11+ from https://www.python.org/downloads/ and run again.' >&2
    echo 'On macOS, the Python.org installer supports Apple Silicon and Intel Macs.' >&2
    exit 1
fi
exec "$task_python" scripts/bootstrap.py "$@"
