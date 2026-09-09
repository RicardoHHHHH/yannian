"""Local, portable SQLite persistence. No API credentials are stored here."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("YANNIAN_DATA_DIR", os.environ.get("YANJI_DATA_DIR", ROOT / "data"))).expanduser().resolve()


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA / "library.sqlite3", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    (DATA / "papers").mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
            color TEXT DEFAULT '#54786a', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT DEFAULT '',
            year TEXT DEFAULT '', abstract TEXT DEFAULT '', doi TEXT DEFAULT '',
            url TEXT DEFAULT '', source TEXT DEFAULT 'upload', pdf_path TEXT,
            sha256 TEXT UNIQUE, page_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL, last_opened TEXT,
            zotero_key TEXT UNIQUE, note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS project_papers (
            project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
            paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
            PRIMARY KEY(project_id, paper_id));
        CREATE TABLE IF NOT EXISTS paragraphs (
            id TEXT PRIMARY KEY, paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
            page INTEGER NOT NULL, ordinal INTEGER NOT NULL, text TEXT NOT NULL,
            bbox TEXT NOT NULL, kind TEXT DEFAULT 'text');
        CREATE INDEX IF NOT EXISTS para_paper ON paragraphs(paper_id,page,ordinal);
        CREATE TABLE IF NOT EXISTS ideas (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
            status TEXT DEFAULT 'spark', project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
            paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
            paragraph_id TEXT REFERENCES paragraphs(id) ON DELETE SET NULL,
            quote TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
            paragraph_id TEXT REFERENCES paragraphs(id) ON DELETE SET NULL,
            title TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL, content TEXT NOT NULL, citations TEXT DEFAULT '[]', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS analyses (
            id TEXT PRIMARY KEY, idea_id TEXT REFERENCES ideas(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, content TEXT NOT NULL, sources TEXT DEFAULT '[]',
            queries TEXT DEFAULT '[]', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS selections (
            id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            paragraph_id TEXT REFERENCES paragraphs(id) ON DELETE SET NULL,
            page INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
            rects TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        columns = {r[1] for r in c.execute("PRAGMA table_info(papers)")}
        for name, default in (("arxiv_id", "''"), ("source_links", "'[]'"), ("pdf_origin", "''"), ("pdf_fetched_at", "''")):
            if name not in columns:
                c.execute(f"ALTER TABLE papers ADD COLUMN {name} TEXT DEFAULT {default}")
        for table in ('ideas', 'conversations'):
            if 'selection_id' not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
                c.execute(f"ALTER TABLE {table} ADD COLUMN selection_id TEXT REFERENCES selections(id) ON DELETE SET NULL")
        if not c.execute("SELECT 1 FROM projects LIMIT 1").fetchone():
            c.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                      (uid(), "我的研究", "把相关论文和想法放在一起，逐步形成研究方向。", "#54786a", now()))


def rows(sql, args=()):
    with connect() as c:
        return [dict(x) for x in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    found = rows(sql, args)
    return found[0] if found else None


def execute(sql, args=()):
    with connect() as c:
        c.execute(sql, args)


def setting(key, default=None):
    row = one("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(row["value"]) if row else default


def save_setting(key, value):
    execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
