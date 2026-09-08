import asyncio
import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import zipfile
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db, pdf, research, pdf_sources, selections
load_dotenv(db.ROOT / ".env")
from . import ai, discovery, chat as reading_chat


@asynccontextmanager
async def lifespan(app):
    db.init()
    discovery.init()
    reading_chat.init()
    try:
        yield
    finally:
        await reading_chat.shutdown()
        await discovery.shutdown()


app = FastAPI(title="研念 · Yannian Workbench", version="0.7.2", lifespan=lifespan)
PDF_FETCHING = set()
URL_FETCHING = set()
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])


@app.middleware("http")
async def local_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if (request.headers.get("X-Yannian") != "1" and request.headers.get("X-Yanji") != "1") or (origin and urlparse(origin).netloc != request.headers.get("host")):
            return JSONResponse({"detail": "只接受本地工作台发起的操作。"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else "no-cache"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data: blob:; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; object-src 'none'"
    return response


def require(table, item_id):
    assert table in {"projects", "papers", "ideas", "paragraphs", "conversations"}
    row = db.one(f"SELECT * FROM {table} WHERE id=?", (item_id,))
    if not row:
        raise HTTPException(404, "记录不存在。")
    return row


def paper_public(row):
    result = {k: v for k, v in row.items() if k not in ("pdf_path", "sha256")}
    if isinstance(result.get("source_links"), str): result["source_links"] = json.loads(result["source_links"] or '[]')
    return result


def add_membership(paper_id, project_id):
    require("papers", paper_id)
    require("projects", project_id)
    db.execute("INSERT OR IGNORE INTO project_papers VALUES (?,?)", (project_id, paper_id))


def save_pdf(data, filename, project_id=None, attach_id=None, metadata=None):
    if project_id:
        require("projects", project_id)
    try:
        parsed = pdf.parse_pdf(data, filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    duplicate = db.one("SELECT * FROM papers WHERE sha256=?", (parsed["sha256"],))
    if duplicate:
        if attach_id and attach_id != duplicate["id"]:
            raise HTTPException(409, "这份 PDF 已存在于另一条文献中，请使用已有文献。")
        if project_id:
            add_membership(duplicate["id"], project_id)
        return {"paper": paper_public(duplicate), "duplicate": True, "warning": parsed["warning"]}
    if attach_id:
        old = require("papers", attach_id)
        if old["pdf_path"]:
            raise HTTPException(409, "该文献已有 PDF；为保留段落和 idea 关联，请作为新文献导入。")
    paper_id = attach_id or db.uid()
    relative = "papers/" + paper_id + ".pdf"
    target = db.DATA / relative
    target.write_bytes(data)
    meta = metadata or {}
    try:
        with db.connect() as c:
            if attach_id:
                c.execute("UPDATE papers SET pdf_path=?,sha256=?,page_count=? WHERE id=?",
                          (relative, parsed["sha256"], parsed["page_count"], paper_id))
            else:
                c.execute("""INSERT INTO papers(id,title,authors,abstract,pdf_path,sha256,page_count,created_at,source,url,year,doi)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (paper_id, meta.get("title") or parsed["title"], meta.get("authors") or parsed["authors"],
                           meta.get("abstract") or parsed["abstract"], relative, parsed["sha256"], parsed["page_count"], db.now(),
                           meta.get("source", "upload"), meta.get("url", ""), meta.get("year", ""), meta.get("doi", "")))
            c.executemany("INSERT INTO paragraphs VALUES (?,?,?,?,?,?,?)",
                          [(db.uid(), paper_id, p["page"], p["ordinal"], p["text"], json.dumps(p["bbox"]), p["kind"]) for p in parsed["paragraphs"]])
            if project_id:
                c.execute("INSERT OR IGNORE INTO project_papers VALUES (?,?)", (project_id, paper_id))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"paper": paper_public(require("papers", paper_id)), "duplicate": False, "warning": parsed["warning"]}


class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    color: str = Field(default="#54786a", pattern=r"^#[a-fA-F0-9]{6}$")


class SourceLink(BaseModel):
    source: str = Field(default="来源", max_length=200)
    url: str = Field(max_length=3000)


class MetadataBody(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    authors: str = Field(default="", max_length=3000)
    year: str = Field(default="", max_length=20)
    abstract: str = Field(default="", max_length=30000)
    doi: str = Field(default="", max_length=300)
    url: str = Field(default="", max_length=3000)
    source: str = Field(default="manual", max_length=100)
    arxiv_id: str = Field(default="", max_length=100)
    source_links: list[SourceLink] = Field(default_factory=list, max_length=30)
    project_id: str | None = None


class IdeaBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=30000)
    status: str = Field(default="spark", pattern="^(spark|exploring|testing|parked)$")
    project_id: str | None = None
    paper_id: str | None = None
    paragraph_id: str | None = None
    quote: str = Field(default="", max_length=15000)
    selection_id: str | None = None


class SettingsBody(BaseModel):
    provider: Literal["codex", "api"] | None = None
    codex_model: str | None = Field(default=None, max_length=120)
    codex_effort: Literal["low", "medium", "high"] | None = None
    model: str = Field(default="gpt-6-astra", max_length=120)
    base_url: str = Field(default="https://api.openai.com/v1", max_length=2000)
    api_key: str | None = Field(default=None, max_length=1000)
    clear_key: bool = False
    web_search: bool = True


class ChatBody(BaseModel):
    paper_id: str
    paragraph_id: str | None = None
    conversation_id: str | None = None
    question: str = Field(min_length=1, max_length=12000)
    selected_text: str = Field(default="", max_length=15000)
    include_page: bool = False
    page: int = Field(default=1, ge=1, le=500)
    selection_id: str | None = None


class ChatRunBody(ChatBody):
    model: str | None = Field(default=None, max_length=120)
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"] | None = None


class SelectionBody(BaseModel):
    page: int = Field(ge=1, le=500)
    kind: Literal['text', 'region']
    text: str = Field(default='', max_length=15000)
    rects: list[tuple[float, float, float, float]] = Field(min_length=1, max_length=256)


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


class DiscoveryBody(SearchBody):
    depth: Literal["quick", "deep"] = "deep"
    web: bool = True
    paper_id: str | None = None


class ArxivBody(BaseModel):
    arxiv: str = Field(max_length=300)
    project_id: str | None = None


class URLImportBody(BaseModel):
    url: str = Field(min_length=5, max_length=3000)
    project_id: str | None = None


class AnalyzeBody(BaseModel):
    kind: str = Field(default="novelty", pattern="^(novelty|resources|experiment)$")
    query: str = Field(default="", max_length=500)
    web: bool = True


@app.get("/api/health")
def health():
    return {"ok": True, "version": "0.7.2", "app": "yannian-workbench"}


@app.get("/api/settings")
async def settings():
    if ai.settings()["provider"] == "codex":
        await asyncio.to_thread(ai.codex_bridge.status)
    return ai.settings()


@app.put("/api/settings")
async def configure(body: SettingsBody):
    ai.configure(**body.model_dump())
    return await settings()


@app.get("/api/codex/status")
async def codex_status():
    return await asyncio.to_thread(ai.codex_bridge.status, True)


@app.post("/api/settings/test")
async def test_connection():
    result = await ai.respond("请只回复：连接成功。", [{"role": "user", "content": "测试连接"}], max_tokens=500)
    return {"ok": True, "message": result["content"], "model": result["model"], "provider": result.get("provider", ai.settings()["provider"])}


@app.get("/api/library")
def library():
    papers = db.rows("SELECT * FROM papers ORDER BY COALESCE(last_opened,created_at) DESC")
    for p in papers:
        p["has_pdf"] = bool(p["pdf_path"])
        p["project_ids"] = [r["project_id"] for r in db.rows("SELECT project_id FROM project_papers WHERE paper_id=?", (p["id"],))]
    return {"papers": [paper_public(p) for p in papers],
            "projects": db.rows("SELECT p.*,COUNT(pp.paper_id) paper_count FROM projects p LEFT JOIN project_papers pp ON p.id=pp.project_id GROUP BY p.id ORDER BY p.created_at"),
            "idea_count": db.one("SELECT COUNT(*) n FROM ideas")["n"]}


@app.post("/api/projects")
def create_project(body: ProjectBody):
    project_id = db.uid()
    if not body.name.strip():
        raise HTTPException(400, "项目名不能为空。")
    db.execute("INSERT INTO projects VALUES (?,?,?,?,?)", (project_id, body.name.strip(), body.description, body.color, db.now()))
    return require("projects", project_id)


@app.post("/api/projects/{project_id}/papers/{paper_id}")
def add_to_project(project_id: str, paper_id: str):
    add_membership(paper_id, project_id)
    return {"ok": True}


@app.delete("/api/projects/{project_id}/papers/{paper_id}")
def remove_from_project(project_id: str, paper_id: str):
    db.execute("DELETE FROM project_papers WHERE project_id=? AND paper_id=?", (project_id, paper_id))
    return {"ok": True}


@app.post("/api/papers/upload")
async def upload(file: UploadFile = File(...), project_id: str = Form(""), attach_id: str = Form("")):
    data = await file.read(pdf.MAX_BYTES + 1)
    return await asyncio.to_thread(save_pdf, data, file.filename or "paper.pdf", project_id or None, attach_id or None)


@app.post("/api/papers/metadata")
def save_metadata(body: MetadataBody):
    if body.project_id:
        require("projects", body.project_id)
    if body.url and urlparse(body.url).scheme not in ("http", "https"):
        raise HTTPException(400, "文献链接需要使用 HTTP(S)。")
    duplicate = db.one("SELECT * FROM papers WHERE lower(title)=lower(?) OR (doi<>'' AND lower(doi)=lower(?))", (body.title.strip(), body.doi))
    if duplicate:
        links = json.loads(duplicate.get("source_links") or "[]")
        links.extend(s.model_dump() for s in body.source_links if s.url not in {v['url'] for v in links})
        db.execute("UPDATE papers SET arxiv_id=?,source_links=?,url=COALESCE(NULLIF(url,''),?),doi=COALESCE(NULLIF(doi,''),?) WHERE id=?",
                   (duplicate.get("arxiv_id") or body.arxiv_id, json.dumps(links[:30], ensure_ascii=False), body.url, body.doi, duplicate["id"]))
        if body.project_id:
            add_membership(duplicate["id"], body.project_id)
        return {"paper": paper_public(require("papers", duplicate["id"])), "duplicate": True}
    paper_id = db.uid()
    db.execute("INSERT INTO papers(id,title,authors,year,abstract,doi,url,source,created_at,arxiv_id,source_links) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (paper_id, body.title.strip(), body.authors, body.year, body.abstract, body.doi, body.url, body.source, db.now(),
                body.arxiv_id, json.dumps([s.model_dump() for s in body.source_links], ensure_ascii=False)))
    if body.project_id:
        add_membership(paper_id, body.project_id)
    return {"paper": paper_public(require("papers", paper_id)), "duplicate": False}


@app.post("/api/papers/arxiv")
async def import_arxiv(body: ArxivBody):
    value = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", body.arxiv.strip()).removesuffix(".pdf")
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?", value):
        raise HTTPException(400, "请输入 arXiv 编号或 arxiv.org 的论文链接。")
    if body.project_id:
        require("projects", body.project_id)
    try:
        async with httpx.AsyncClient(timeout=60, headers={"User-Agent": research.UA}) as client:
            url = "https://arxiv.org/pdf/" + value
            for _ in range(4):
                async with client.stream("GET", url) as res:
                    if res.is_redirect:
                        from urllib.parse import urljoin
                        next_url = urljoin(url, res.headers.get("location", ""))
                        if urlparse(next_url).hostname not in ("arxiv.org", "export.arxiv.org") or urlparse(next_url).scheme != "https":
                            raise HTTPException(400, "arXiv 跳转到不支持的下载地址，请手动上传 PDF。")
                        url = next_url
                        continue
                    res.raise_for_status()
                    content = bytearray()
                    async for chunk in res.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > pdf.MAX_BYTES:
                            raise HTTPException(413, "PDF 超过 40 MB。")
                    return await asyncio.to_thread(save_pdf, bytes(content), value.replace("/", "-") + ".pdf", body.project_id,
                                                   None, {"source": "arXiv", "url": "https://arxiv.org/abs/" + value})
            raise HTTPException(502, "arXiv 下载跳转过多。")
    except httpx.HTTPError as exc:
        raise HTTPException(502, "arXiv 下载失败，请稍后重试，或下载后手动上传 PDF。") from exc


@app.post("/api/papers/{paper_id}/fetch-pdf")
async def fetch_paper_pdf(paper_id: str):
    paper = require("papers", paper_id)
    if paper["pdf_path"]:
        return {"status": "ready", "paper": paper_public(paper), "message": "这篇文献已保存 PDF。", "attempts": []}
    if paper_id in PDF_FETCHING: raise HTTPException(409, "这篇文献正在获取 PDF，请等待当前下载完成。")
    PDF_FETCHING.add(paper_id)
    try:
        try:
            async with asyncio.timeout(150):
                result = await pdf_sources.find_pdf(paper)
        except TimeoutError:
            return {"status": "not_found", "message": "本次自动获取超时，文献和 idea 已保留。可以稍后重试。", "attempts": []}
        if not result.get("data"):
            return {"status": "not_found", "message": "暂未找到可直接下载的公开 PDF。来源可能需要登录，或尚未提供开放版本。文献和 idea 已保留。", "attempts": result["attempts"]}
        saved = await asyncio.to_thread(save_pdf, result["data"], "paper.pdf", None, paper_id)
        db.execute("UPDATE papers SET pdf_origin=?,pdf_fetched_at=? WHERE id=?", (result["url"], db.now(), paper_id))
        return {**saved, "status": "ready", "paper": paper_public(require("papers", paper_id)),
                "source": result["source"], "download_url": result["url"], "attempts": result["attempts"]}
    finally:
        PDF_FETCHING.discard(paper_id)


def save_url_pdf(result, original_url, project_id):
    metadata = {**result.get('metadata', {}), 'source': result['source'], 'url': original_url}
    # Reuse an existing metadata record, while file identity takes precedence over its title.
    duplicate = db.one('SELECT * FROM papers WHERE sha256=?', (hashlib.sha256(result['data']).hexdigest(),))
    existing = db.one("SELECT * FROM papers WHERE url=? OR (doi<>'' AND lower(doi)=lower(?)) ORDER BY created_at LIMIT 1",
                      (original_url, metadata.get('doi', '')))
    attach_id = existing['id'] if existing and not existing['pdf_path'] and not duplicate else None
    filename = unquote(Path(urlparse(result['url']).path).name)[:200] or 'paper.pdf'
    saved = save_pdf(result['data'], filename, project_id, attach_id, metadata)
    paper_id = saved['paper']['id']
    if not saved['duplicate']:
        db.execute("UPDATE papers SET pdf_origin=?,pdf_fetched_at=?,arxiv_id=COALESCE(NULLIF(arxiv_id,''),?) WHERE id=?",
                   (result['url'], db.now(), result.get('arxiv_id', ''), paper_id))
    saved['paper'] = paper_public(require('papers', paper_id))
    return saved


@app.post('/api/papers/from-url')
async def import_pdf_url(body: URLImportBody):
    url = pdf_sources.https_url(body.url.strip())
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise HTTPException(400, '网址格式不正确，请复制完整的 PDF 或论文页面网址。') from exc
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, '请输入公开的 PDF 或论文页面网址（HTTPS）。')
    url = parsed._replace(fragment='').geturl()
    project = require('projects', body.project_id) if body.project_id else None
    if url in URL_FETCHING:
        raise HTTPException(409, '这个网址正在下载，请等待当前导入完成。')
    URL_FETCHING.add(url)
    try:
        existing = db.one("SELECT * FROM papers WHERE (url=? OR pdf_origin=?) AND pdf_path IS NOT NULL LIMIT 1", (url, url))
        if existing:
            if body.project_id: add_membership(existing['id'], body.project_id)
            return {'status':'ready', 'paper':paper_public(existing), 'duplicate':True, 'warning':'',
                    'project':project, 'download_url':existing.get('pdf_origin') or url}
        try:
            async with asyncio.timeout(150):
                result = await pdf_sources.fetch_from_url(url)
            saved = await asyncio.to_thread(save_url_pdf, result, url, body.project_id)
        except pdf_sources.FetchError as exc:
            raise HTTPException(400, str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(504, '下载超时，尚未保存新论文；请稍后重试或使用 PDF 直链。') from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, '该网址暂时无法下载，可能已失效或受到访问限制。') from exc
        return {**saved, 'status':'ready', 'project':project, 'download_url':result['url']}
    finally:
        URL_FETCHING.discard(url)


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: str):
    p = require("papers", paper_id)
    db.execute("UPDATE papers SET last_opened=? WHERE id=?", (db.now(), paper_id))
    p["has_pdf"] = bool(p["pdf_path"])
    p["paragraphs"] = db.rows("SELECT * FROM paragraphs WHERE paper_id=? ORDER BY page,ordinal", (paper_id,))
    p["page_sizes"] = []
    if p["pdf_path"]:
        import pymupdf as fitz
        with fitz.open(db.DATA / p["pdf_path"]) as doc:
            p["page_sizes"] = [[page.rect.width, page.rect.height] for page in doc]
            for para in p['paragraphs']:
                page = doc[para['page']-1]
                box = fitz.Rect(json.loads(para['bbox'])) * page.rotation_matrix
                para['display_bbox'] = [box.x0/page.rect.width,box.y0/page.rect.height,box.x1/page.rect.width,box.y1/page.rect.height]
    p["project_ids"] = [r["project_id"] for r in db.rows("SELECT project_id FROM project_papers WHERE paper_id=?", (paper_id,))]
    return paper_public(p)


@app.get("/api/papers/{paper_id}/file")
def original_pdf(paper_id: str):
    p = require("papers", paper_id)
    if not p["pdf_path"]:
        raise HTTPException(404, "还没有 PDF。")
    return FileResponse(db.DATA / p["pdf_path"], media_type="application/pdf")


@app.get("/api/papers/{paper_id}/pages/{page}.png")
def page_image(paper_id: str, page: int, paragraph_id: str | None = None):
    p = require("papers", paper_id)
    if not p["pdf_path"]:
        raise HTTPException(404, "还没有 PDF。")
    box = None
    if paragraph_id:
        para = require("paragraphs", paragraph_id)
        if para["paper_id"] != paper_id or para["page"] != page:
            raise HTTPException(400, "段落和页码不匹配。")
        box = json.loads(para["bbox"])
    try:
        return Response(pdf.page_png(db.DATA / p["pdf_path"], page, box), media_type="image/png")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/papers/{paper_id}/selections')
async def create_pdf_selection(paper_id: str, body: SelectionBody):
    return await asyncio.to_thread(selections.create, require('papers', paper_id), body.page, body.kind, body.text, body.rects)


@app.get('/api/selections/{selection_id}')
def get_pdf_selection(selection_id: str):
    return selections.get(selection_id)


@app.get('/api/selections/{selection_id}/image.png')
def selection_image(selection_id: str):
    return Response(selections.image(selections.get(selection_id)), media_type='image/png')


@app.get("/api/ideas")
def get_ideas():
    return db.rows("""SELECT i.*,p.title paper_title,pr.name project_name FROM ideas i
                   LEFT JOIN papers p ON p.id=i.paper_id LEFT JOIN projects pr ON pr.id=i.project_id ORDER BY i.updated_at DESC""")


def validate_idea(body):
    if body.project_id:
        require("projects", body.project_id)
    if body.paper_id:
        require("papers", body.paper_id)
    if body.selection_id and selections.get(body.selection_id)['paper_id'] != body.paper_id:
        raise HTTPException(400, '选区不属于这篇论文。')
    if body.paragraph_id:
        para = require("paragraphs", body.paragraph_id)
        if para["paper_id"] != body.paper_id:
            raise HTTPException(400, "idea 引用的段落必须属于所选论文。")


@app.post("/api/ideas")
def create_idea(body: IdeaBody):
    validate_idea(body)
    idea_id, timestamp = db.uid(), db.now()
    db.execute("INSERT INTO ideas(id,title,body,status,project_id,paper_id,paragraph_id,quote,created_at,updated_at,selection_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (idea_id, body.title, body.body, body.status,
               body.project_id, body.paper_id, body.paragraph_id, body.quote, timestamp, timestamp, body.selection_id))
    return require("ideas", idea_id)


@app.put("/api/ideas/{idea_id}")
def edit_idea(idea_id: str, body: IdeaBody):
    require("ideas", idea_id)
    validate_idea(body)
    db.execute("UPDATE ideas SET title=?,body=?,status=?,project_id=?,paper_id=?,paragraph_id=?,quote=?,updated_at=?,selection_id=? WHERE id=?",
               (body.title, body.body, body.status, body.project_id, body.paper_id, body.paragraph_id, body.quote, db.now(), body.selection_id, idea_id))
    return require("ideas", idea_id)


@app.get("/api/papers/{paper_id}/conversations")
def conversations(paper_id: str):
    require("papers", paper_id)
    return db.rows("SELECT * FROM conversations WHERE paper_id=? ORDER BY created_at DESC", (paper_id,))


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    return reading_chat.conversation(conversation_id)


@app.post("/api/chat/runs", status_code=202)
async def start_chat_run(body: ChatRunBody):
    return await reading_chat.start(body)


@app.get("/api/chat/runs/{run_id}")
def get_chat_run(run_id: str):
    return reading_chat.get(run_id)


@app.post("/api/chat/runs/{run_id}/cancel")
async def cancel_chat_run(run_id: str):
    return await reading_chat.cancel(run_id)


@app.post("/api/chat")
async def chat(body: ChatBody):
    selection = selections.get(body.selection_id) if body.selection_id else None
    if selection and (selection['paper_id'] != body.paper_id or selection['paragraph_id'] != body.paragraph_id):
        raise HTTPException(400, '选区与论文或段落不匹配，请重新选择。')
    selected_text = selection['text'] if selection else body.selected_text
    paper, context, citations = ai.paper_context(body.paper_id, body.paragraph_id, body.question + " " + selected_text)
    history = []
    if body.conversation_id:
        conversation = require("conversations", body.conversation_id)
        if conversation["paper_id"] != body.paper_id or conversation["paragraph_id"] != body.paragraph_id or conversation.get('selection_id') != body.selection_id:
            raise HTTPException(400, "该对话属于另一篇论文或段落，请新建对话。")
        history = db.rows("SELECT role,content FROM messages WHERE conversation_id=? ORDER BY created_at DESC,rowid DESC LIMIT 10", (body.conversation_id,))[::-1]
        history = [{"role": m["role"], "content": m["content"][:10000]} for m in history]
    question = body.question + ("\n我选中的文字：\n" + selected_text if selected_text else "")
    if selection:
        question += f"\nPDF 选区 [S1]：第 {selection['page']} 页，类型 {selection['kind']}。只把选区作为当前关注范围，附近文字仅用于理解上下文。"
    content = [{"type": "input_text", "text": context + "\n\n用户问题：" + question}]
    if selection and selection['kind'] == 'region':
        png = await asyncio.to_thread(selections.image, selection)
        content.append({'type':'input_image', 'image_url':'data:image/png;base64,' + base64.b64encode(png).decode(), 'detail':'high'})
    if body.include_page:
        content.append(await asyncio.to_thread(ai.page_content, paper, body.page))
    result = await ai.respond("这是围绕一篇论文的持续阅读对话。当前请求中的 [P数字] 映射为准，历史标记不能跨轮复用。涉及未给出的内容要说明。", history + [{"role": "user", "content": content}])
    used = set(re.findall(r"\[P(\d+)\]", result["content"]))
    result["citations"] += [c for c in citations if c["label"][1:] in used]
    if selection:
        result['citations'].append({'type':'selection','label':'S1','selection_id':selection['id'],'paper_id':body.paper_id,'page':selection['page']})
    conversation_id = body.conversation_id or db.uid()
    with db.connect() as c:
        if not body.conversation_id:
            c.execute("INSERT INTO conversations(id,paper_id,paragraph_id,title,created_at,selection_id) VALUES (?,?,?,?,?,?)", (conversation_id, body.paper_id, body.paragraph_id, body.question[:70], db.now(), body.selection_id))
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (db.uid(), conversation_id, "user", question, "[]", db.now()))
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (db.uid(), conversation_id, "assistant", result["content"], json.dumps(result["citations"], ensure_ascii=False), db.now()))
    result["conversation_id"] = conversation_id
    return result


@app.post("/api/research/search")
async def search(body: SearchBody):
    return await research.search(body.query)


@app.post("/api/research/jobs")
async def begin_discovery(body: DiscoveryBody):
    if body.paper_id: require("papers", body.paper_id)
    return discovery.start(body.query, body.depth, body.web, body.paper_id)


@app.get("/api/research/jobs/{job_id}")
async def discovery_status(job_id: str, after: int | None = None):
    return discovery.read(job_id, after)


@app.post("/api/research/jobs/{job_id}/cancel")
async def stop_discovery(job_id: str):
    return await discovery.cancel(job_id)


@app.post("/api/papers/{paper_id}/similar")
async def similar_papers(paper_id: str):
    p = require("papers", paper_id)
    query = research.keywords(p["title"], 7)
    result = await research.search(query)
    result["papers"] = [x for x in result["papers"] if x["title"].lower().strip() != p["title"].lower().strip()]
    return result


@app.post("/api/organize/suggest")
def suggest_groups():
    papers = db.rows("SELECT id,title,abstract FROM papers")
    projects = db.rows("SELECT * FROM projects")
    suggestions = []
    for p in papers:
        existing = {x["project_id"] for x in db.rows("SELECT project_id FROM project_papers WHERE paper_id=?", (p["id"],))}
        choices = []
        for project in projects:
            if project["id"] in existing:
                continue
            members = db.rows("SELECT p.title,p.abstract FROM papers p JOIN project_papers pp ON pp.paper_id=p.id WHERE pp.project_id=?", (project["id"],))
            corpus = project["name"] + " " + project["description"] + " " + " ".join(x["title"] + " " + x["abstract"][:1000] for x in members)
            score = research.similarity(p["title"] + " " + p["abstract"][:1800], corpus)
            if score > .07:
                choices.append((score, project))
        if choices:
            score, project = max(choices, key=lambda c: c[0])
            suggestions.append({"paper_id": p["id"], "paper_title": p["title"], "project_id": project["id"],
                                "project_name": project["name"], "score": round(score, 3),
                                "reason": "标题、摘要与项目描述 / 已收录论文有关键词重合"})
    # Propose new projects for connected groups of papers, without mutating the library.
    unassigned = [p for p in papers if not db.one("SELECT 1 FROM project_papers WHERE paper_id=?", (p["id"],))]
    groups = []
    while unassigned:
        seed = unassigned.pop(0)
        members = [seed]
        for candidate in list(unassigned):
            if research.similarity(seed["title"] + " " + seed["abstract"][:1200], candidate["title"] + " " + candidate["abstract"][:1200]) >= .18:
                members.append(candidate)
                unassigned.remove(candidate)
        if len(members) >= 2:
            groups.append({"name": research.keywords(" ".join(p["title"] for p in members), 3).title(),
                           "paper_ids": [p["id"] for p in members], "titles": [p["title"] for p in members]})
    return {"suggestions": sorted(suggestions, key=lambda x: x["score"], reverse=True), "new_groups": groups,
            "method": "本地词项相似度建议；可审核后批量归入，同一论文可属于多个项目。"}


class ApplyGroups(BaseModel):
    assignments: list[dict[str, str]] = Field(max_length=2000)
    new_groups: list[dict] = Field(default_factory=list, max_length=50)


@app.post("/api/organize/apply")
def apply_groups(body: ApplyGroups):
    pairs = []
    for item in body.assignments:
        paper_id, project_id = item.get("paper_id"), item.get("project_id")
        require("papers", paper_id)
        require("projects", project_id)
        pairs.append((project_id, paper_id))
    with db.connect() as c:
        c.executemany("INSERT OR IGNORE INTO project_papers VALUES (?,?)", pairs)
        for group in body.new_groups:
            name = str(group.get("name", "")).strip()[:120]
            ids = group.get("paper_ids", [])
            if not name or not isinstance(ids, list) or len(ids) > 2000:
                raise HTTPException(400, "项目分组格式无效。")
            for paper_id in ids:
                require("papers", paper_id)
            project_id = db.uid()
            c.execute("INSERT INTO projects VALUES (?,?,?,?,?)", (project_id, name, "按论文主题归类", "#54786a", db.now()))
            c.executemany("INSERT OR IGNORE INTO project_papers VALUES (?,?)", [(project_id, pid) for pid in ids])
    return {"ok": True, "count": len(pairs)}


@app.post("/api/organize/ai")
async def ai_groups():
    papers = db.rows("SELECT id,title,abstract FROM papers ORDER BY created_at DESC LIMIT 120")
    projects = db.rows("SELECT id,name,description FROM projects")
    for p in papers:
        p["abstract"] = p["abstract"][:1000]
    result = await ai.respond("按AI研究问题和方法对论文进行语义归类。只输出JSON对象，不加Markdown："
                             '{"suggestions":[{"paper_id":"已有论文id","project_id":"已有项目id","reason":"归类原因"}],'
                             '"new_groups":[{"name":"具体研究方向","paper_ids":["已有论文id"]}]}。'
                             "优先复用合适的现有项目，新项目至少包含2篇论文；同一论文可以属于多个项目。不要编造id。",
                             [{"role": "user", "content": json.dumps({"papers": papers, "projects": projects}, ensure_ascii=False)}], max_tokens=5000)
    try:
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", result["content"].strip())
        parsed = json.loads(raw)
        paper_map, project_map = {p["id"]: p for p in papers}, {p["id"]: p for p in projects}
        suggestions, groups = [], []
        for s in parsed.get("suggestions", []):
            if s["paper_id"] in paper_map and s["project_id"] in project_map:
                suggestions.append({**s, "paper_title": paper_map[s["paper_id"]]["title"], "project_name": project_map[s["project_id"]]["name"]})
        for g in parsed.get("new_groups", []):
            ids = list(dict.fromkeys(pid for pid in g["paper_ids"] if pid in paper_map))
            if len(ids) >= 2:
                groups.append({"name": str(g["name"])[:120], "paper_ids": ids, "titles": [paper_map[pid]["title"] for pid in ids]})
        return {"suggestions": suggestions, "new_groups": groups, "method": "AI 语义归类建议，覆盖最近导入的最多 120 篇论文；应用前可以调整。"}
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "模型返回的分组格式无法解析，请重试或使用本地相似归类。") from exc


@app.get("/api/ideas/{idea_id}/analyses")
def analyses(idea_id: str):
    require("ideas", idea_id)
    result = db.rows("SELECT * FROM analyses WHERE idea_id=? ORDER BY created_at DESC", (idea_id,))
    for x in result:
        x["sources"] = json.loads(x["sources"])
        x["queries"] = json.loads(x["queries"])
    return result


@app.post("/api/ideas/{idea_id}/analyze")
async def analyze_idea(idea_id: str, body: AnalyzeBody):
    idea = require("ideas", idea_id)
    if ai.settings()["provider"] == "api" and not ai.key():
        raise HTTPException(428, "请在模型设置中配置 API Key，然后进行 idea 分析。")
    query = body.query.strip()
    if not query:
        generated = await ai.respond("将研究想法转成用于文献检索的一条精确英文关键词查询，包含任务与方法，只输出 4–8 个关键词，不要解释。",
                                     [{"role": "user", "content": idea["title"] + "\n" + idea["body"][:6000]}], max_tokens=500)
        query = generated["content"].strip().replace("\n", " ")[:300]
    discovered = await research.search(query, limit=12)
    task = {
        "novelty": "评估相关工作与潜在差异。按：研究问题；最接近工作对照表（链接、已知贡献、与idea重合、待确认差异）；可能成立的贡献；最危险的反例；下一步最小验证实验来写。给出高/中/低证据置信度。不要直接判定创新或保证顶会录用。",
        "resources": "寻找可用资源。按开源实现、数据集、基准与评价指标、预训练模型、算力与复现条件组织。每个具体资源需要已检索来源链接，说明适配性与许可证/访问条件是否已核实。只知道名称没有证据时列为待核实，禁止编造链接。",
        "experiment": "设计适合AI会议论文验证的最小实验：假设、强基线、数据划分、主指标、消融、预算估算的假设、失败判据、可复现记录。基于已找到的相关工作给基线，不要编造实验结果或硬件性能。"
    }[body.kind]
    web_enabled = bool(body.web and ai.settings()["web_search"])
    prompt = ("用户研究方向：AI / 顶会。\nIdea：" + idea["title"] + "\n" + idea["body"] +
              "\n触发原文：" + idea["quote"] + "\n检索关键词：" + query +
              "\n候选文献元数据（仅摘要/元数据，未读全文）：\n" + json.dumps(discovered, ensure_ascii=False) +
              "\n任务：" + task + ("\n请联网检索，优先OpenReview、会议官网、arXiv、作者官方代码仓库和数据集主页，查找截至今天的可验证证据。" if web_enabled else "\n本次未开启联网模型检索。仅分析给定元数据，明确检索覆盖不足；不能声称已核实代码或数据资源。"))
    result = await ai.respond(task, [{"role": "user", "content": prompt}], web=web_enabled, max_tokens=6500)
    sources = result["citations"] + [{"type": "candidate", "title": p["title"], "url": p["url"]} for p in discovered["papers"]]
    analysis_id = db.uid()
    record = {"id": analysis_id, "idea_id": idea_id, "kind": body.kind, "content": result["content"],
              "sources": sources, "queries": [query], "created_at": db.now()}
    db.execute("INSERT INTO analyses VALUES (?,?,?,?,?,?,?)", (analysis_id, idea_id, body.kind, result["content"], json.dumps(sources, ensure_ascii=False), json.dumps([query]), record["created_at"]))
    record["warnings"] = discovered["warnings"]
    record["resource_links"] = research.resource_links(query)
    return record


@app.get("/api/zotero/status")
async def zotero_status():
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get("http://127.0.0.1:23119/api/users/0/items/top", params={"limit": 1, "format": "json"}, headers={"Zotero-API-Version": "3"})
        return {"connected": response.status_code == 200, "status": response.status_code,
                "message": "已连接本机 Zotero" if response.status_code == 200 else "请在 Zotero 设置 → 高级中允许本机应用通信。"}
    except httpx.HTTPError:
        return {"connected": False, "message": "未检测到 Zotero。请打开 Zotero，并在设置 → 高级中允许本机应用通信。"}


@app.get("/api/zotero/items")
async def zotero_items(start: int = 0):
    if start < 0:
        raise HTTPException(400, "无效分页参数。")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get("http://127.0.0.1:23119/api/users/0/items/top", params={"start": start, "limit": 100, "format": "json"}, headers={"Zotero-API-Version": "3"})
            res.raise_for_status()
            items = res.json()
        return {"items": [{"key": p["key"], "title": p["data"].get("title", ""), "type": p["data"].get("itemType")} for p in items if p["data"].get("itemType") not in ("note", "attachment")],
                "next_start": start + 100 if len(items) == 100 else None}
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "无法读取 Zotero，请确认已打开并允许本机应用通信。") from exc


class ZoteroImportBody(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=100)
    project_id: str | None = None


@app.post("/api/zotero/import")
async def import_zotero(body: ZoteroImportBody):
    if body.project_id:
        require("projects", body.project_id)
    results, warnings = [], []
    async with httpx.AsyncClient(timeout=20, headers={"Zotero-API-Version": "3"}) as client:
        for key in body.keys:
            if not re.fullmatch(r"[A-Z0-9]{8}", key):
                raise HTTPException(400, "Zotero 条目 key 无效。")
            base = "http://127.0.0.1:23119/api/users/0/items/" + key
            try:
                res = await client.get(base)
                res.raise_for_status()
                server_id = res.headers.get("Zotero-Server-ID", "legacy")
                identity = server_id + ":" + key
                exists = db.one("SELECT id,page_count FROM papers WHERE zotero_key=?", (identity,))
                if exists:
                    if body.project_id:
                        add_membership(exists["id"], body.project_id)
                    if exists["page_count"]:
                        results.append(exists["id"])
                        continue
                d = res.json()["data"]
                meta = MetadataBody(title=d.get("title") or key,
                       authors=", ".join(a.get("name") or (a.get("firstName", "") + " " + a.get("lastName", "")).strip() for a in d.get("creators", [])),
                       year=d.get("date", "")[:20], abstract=d.get("abstractNote", ""), doi=d.get("DOI", ""),
                       url=d.get("url", "") if urlparse(d.get("url", "")).scheme in ("https", "http") else "",
                       source="Zotero", project_id=body.project_id)
                saved = save_metadata(meta)["paper"]
                paper_id = saved["id"]
                db.execute("UPDATE papers SET zotero_key=? WHERE id=?", (identity, paper_id))
                children_res = await client.get(base + "/children")
                children_res.raise_for_status()
                pdf_child = next((p for p in children_res.json() if p["data"].get("contentType") == "application/pdf"), None)
                if pdf_child and not saved.get("page_count"):
                    file_res = await client.get("http://127.0.0.1:23119/api/users/0/items/" + pdf_child["key"] + "/file", follow_redirects=False)
                    location = file_res.headers.get("location", "")
                    parsed = urlparse(location)
                    if file_res.status_code in (301, 302, 303, 307, 308) and parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
                        path_text = unquote(parsed.path)
                        if os.name == "nt" and re.match(r"^/[A-Za-z]:", path_text):
                            path_text = path_text[1:]
                        path = Path(path_text)
                        if path.suffix.lower() == ".pdf" and path.is_file() and path.stat().st_size <= pdf.MAX_BYTES:
                            await asyncio.to_thread(save_pdf, path.read_bytes(), path.name, None, paper_id)
                        else:
                            warnings.append(meta.title + "：PDF 不可读取或过大，已导入元数据。")
                    else:
                        warnings.append(meta.title + "：PDF 附件不在本机，已导入元数据。")
                elif not saved.get("page_count"):
                    warnings.append(meta.title + "：未找到 PDF，已导入元数据。")
                results.append(paper_id)
            except (httpx.HTTPError, ValueError, OSError, HTTPException) as exc:
                warnings.append(key + "：部分导入失败，请检查 Zotero 附件后重试。")
    return {"imported": results, "warnings": warnings}


@app.get("/api/export")
def export_backup():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        payload = {t: db.rows("SELECT * FROM " + t) for t in ("projects", "papers", "project_papers", "paragraphs", "ideas", "conversations", "messages", "analyses", "chat_runs")}
        archive.writestr("library.json", json.dumps(payload, ensure_ascii=False, indent=2))
        # A consistent SQLite snapshot allows an exact restore, even while the app is open.
        snapshot = db.DATA / ("backup-" + db.uid() + ".sqlite3")
        try:
            with db.connect() as source:
                with closing(sqlite3.connect(snapshot)) as target:
                    source.backup(target)
            archive.write(snapshot, "library.sqlite3")
        finally:
            snapshot.unlink(missing_ok=True)
        for p in payload["papers"]:
            if p["pdf_path"] and (db.DATA / p["pdf_path"]).exists():
                archive.write(db.DATA / p["pdf_path"], p["pdf_path"])
        for idea in payload["ideas"]:
            reports = [a for a in payload["analyses"] if a["idea_id"] == idea["id"]]
            text = "# " + idea["title"] + "\n\n" + idea["body"] + "\n\n> " + idea["quote"]
            for report in reports:
                text += "\n\n---\n\n" + report["content"]
            archive.writestr("ideas/" + idea["id"] + ".md", text)
    return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": "attachment; filename=yannian-backup.zip"})


@app.get("/")
def index():
    return FileResponse(db.ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=db.ROOT / "static"), name="static")
