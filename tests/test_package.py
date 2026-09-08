from pathlib import Path
import zipfile

from scripts.package_source import ROOT_FILES, package


def test_source_package_excludes_personal_data_and_keeps_hidden_templates(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    for name in ROOT_FILES:
        (root / name).write_text('source fixture', encoding='utf-8')
    fixtures = {
        'app/main.py': 'source', 'static/index.html': 'source',
        'static/yannian-app.ico': 'icon', 'static/vendor/LICENSE': 'license',
        'static/vendor/pdfjs/legacy/build/pdf.mjs': 'vendored runtime',
        '.env': 'private', '.env.production': 'private',
        'data/library.sqlite3': 'private', 'data/papers/personal.pdf': 'private',
        'app/__pycache__/main.pyc': 'cache', 'tests/.env.local': 'private',
        '.venv/Lib/private.py': 'runtime', 'static/yanji-app.ico': 'legacy',
        'dist/old.zip': 'generated', 'other-personal-note.md': 'private',
    }
    for name, content in fixtures.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    output = root / 'dist/yannian-source.zip'
    count = package(output, root)
    with zipfile.ZipFile(output) as archive:
        names = {str(Path(name).relative_to('yannian')).replace('\\', '/') for name in archive.namelist()}
        assert names == set(ROOT_FILES) | {'app/main.py', 'static/index.html', 'static/yannian-app.ico', 'static/vendor/LICENSE', 'static/vendor/pdfjs/legacy/build/pdf.mjs'}
        assert count == len(names)
        assert all(archive.read(name) != b'private' for name in archive.namelist())
