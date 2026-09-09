import json
import subprocess
from pathlib import Path

import pytest
from scripts import bootstrap


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / '研念 Research Space'
    root.mkdir()
    (root / 'requirements.txt').write_text('example==1\n', encoding='utf-8')
    data = root / 'data'
    data.mkdir()
    (data / 'keep.txt').write_text('Saved research')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ['-m', 'venv']:
            python = bootstrap.environment_python(root)
            python.parent.mkdir(parents=True)
            python.write_text('executable fixture')
    monkeypatch.setattr(bootstrap.subprocess, 'run', run)
    return root, calls


def test_platform_paths():
    root = Path('/research space')
    assert bootstrap.environment_python(root, 'win32') == root / '.venv/Scripts/python.exe'
    assert bootstrap.environment_python(root, 'darwin') == root / '.venv/bin/python'
    assert bootstrap.environment_python(root, 'linux') == root / '.venv/bin/python'


def test_first_setup_quick_repeat_and_updated_dependencies(setup):
    root, calls = setup
    python = bootstrap.prepare(root)
    assert python == bootstrap.environment_python(root)
    assert sum('-r' in call for call in calls) == 1
    calls.clear()
    bootstrap.prepare(root)
    assert not any('-m' in call for call in calls), 'Quick launch must not recreate the venv or call pip.'
    (root / 'requirements.txt').write_text('example==2\n', encoding='utf-8')
    bootstrap.prepare(root)
    assert sum('-r' in call for call in calls) == 1
    assert (root / 'data/keep.txt').read_text() == 'Saved research'


def test_failed_dependency_install_is_retryable(setup, monkeypatch):
    root, calls = setup
    original = bootstrap.subprocess.run
    def fail(command, **kwargs):
        if '-r' in command:
            raise subprocess.CalledProcessError(1, command)
        original(command, **kwargs)
    monkeypatch.setattr(bootstrap.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        bootstrap.prepare(root)
    assert not (root / '.venv/yannian-setup.json').exists()
    monkeypatch.setattr(bootstrap.subprocess, 'run', original)
    bootstrap.prepare(root)
    assert json.loads((root / '.venv/yannian-setup.json').read_text())['requirements_sha256'] == bootstrap.fingerprint(root)


def test_incompatible_venv_is_never_deleted(setup):
    root, calls = setup
    (root / '.venv').mkdir()
    (root / '.venv/keep.txt').write_text('Other environment')
    with pytest.raises(RuntimeError, match='another platform'):
        bootstrap.prepare(root)
    assert not calls
    assert (root / '.venv/keep.txt').read_text() == 'Other environment'


def test_shell_entrypoints_keep_unix_line_endings():
    for name in ('start.sh', 'start.command'):
        data = (bootstrap.ROOT / name).read_bytes()
        assert data.startswith(b'#!/bin/bash\n') and b'\r' not in data
