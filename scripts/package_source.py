"""Build a source-only archive using explicit source roots, never the data directory."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = (
    '.env.example', '.gitignore', 'README.md', 'LICENSE', 'THIRD_PARTY.md',
    'VERIFICATION.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'requirements.txt',
    'requirements-dev.txt', 'run.py', 'launch.pyw', 'start.cmd', 'start.ps1',
    'create-desktop-shortcut.ps1',
)
SOURCE_DIRS = ('app', 'static', 'tests', 'docs', 'scripts')
EXCLUDED_PARTS = {'.git', '.venv', 'venv', 'data', '__pycache__', '.pytest_cache',
                  '.cache', 'node_modules', 'dist'}
LEGACY_ASSETS = {'yanji-logo.png', 'yanji-app.ico', 'yanji-icon-64.png'}


def excluded(path):
    return (bool(EXCLUDED_PARTS.intersection(path.parts))
            or (path.name.startswith('.env') and path.name != '.env.example')
            or path.name in LEGACY_ASSETS
            or path.name in {'.DS_Store', 'Thumbs.db', 'desktop.ini'}
            or path.suffix.lower() in {'.pyc', '.pyo', '.log', '.db', '.pdf', '.zip'}
            or '.sqlite' in path.name.lower())


def source_files(root=ROOT):
    root = root.resolve()
    paths = [root / name for name in ROOT_FILES]
    for folder in SOURCE_DIRS:
        paths.extend(p for p in (root / folder).rglob('*') if p.is_file())
    result = []
    for path in sorted(set(paths)):
        relative = path.relative_to(root)
        if excluded(relative):
            continue
        if not path.is_file():
            raise RuntimeError(f'Missing source file: {relative}')
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise RuntimeError(f'Source path must stay inside the project: {relative}')
        result.append(path)
    if not (root / 'app/main.py') in result or not (root / 'static/index.html') in result:
        raise RuntimeError('The source tree is incomplete.')
    return result


def package(output, root=ROOT):
    root = root.resolve()
    files = source_files(root)
    output = output.resolve()
    if output.is_relative_to(root) and not output.is_relative_to(root / 'dist'):
        raise ValueError('Put in-project archives inside dist/ to keep source files separate.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, (Path('yannian') / path.relative_to(root)).as_posix())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Archive integrity check failed.')
        if any(excluded(Path(name).relative_to('yannian')) for name in archive.namelist()):
            raise RuntimeError('Unexpected private or generated file in archive.')
    return len(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Package Yannian source without local research data.')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/yannian-source.zip')
    args = parser.parse_args()
    count = package(args.output)
    print(f'Packaged {count} source files: {args.output.resolve()} ({args.output.stat().st_size:,} bytes)')
