import io
import json
import os
import zipfile

import httpx
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from app import ai, db, research
from app.main import app

HEADERS = {"X-Yannian": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "data")
    monkeypatch.setattr(ai, "_session_key", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    research._cache.clear()
    with TestClient(app, headers=HEADERS) as client:
        db.save_setting("provider", "api")  # These regression tests exercise the optional API transport.
        yield client


def pdf_bytes(title="Evidence grounded learning", body="This method retrieves evidence before producing an answer."):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((60, 70), title, fontsize=20)
        page.insert_textbox(fitz.Rect(60, 130, 520, 220), body, fontsize=12)
        page.insert_textbox(fitz.Rect(60, 260, 520, 330), "We evaluate accuracy using a held-out dataset and independent baselines.", fontsize=12)
        document.set_metadata({"title": title, "author": "Fixture Author"})
        return document.tobytes()


def upload(client, data=None, **fields):
    r = client.post("/api/papers/upload", files={"file": ("paper.pdf", data or pdf_bytes(), "application/pdf")}, data=fields)
    assert r.status_code == 200, r.text
    return r.json()["paper"]


def project(client, name="Retrieval research", description="retrieval evidence grounded learning"):
    return client.post("/api/projects", json={"name": name, "description": description}).json()


def test_renamed_header_accepts_old_tabs_and_keeps_origin_guard(client):
    client.headers.clear()
    for header in ("X-Yannian", "X-Yanji"):
        assert client.post("/api/projects", headers={header: "1"}, json={"name": header}).status_code == 200
        assert client.post("/api/projects", headers={header: "1", "Origin": "https://foreign.example"},
                           json={"name": "Blocked"}).status_code == 403
    assert client.post("/api/projects", json={"name": "No header"}).status_code == 403


def test_upload_extract_anchor_and_duplicate(client):
    data = pdf_bytes()
    p = upload(client, data)
    detail = client.get("/api/papers/" + p["id"]).json()
    assert detail["has_pdf"] and len(detail["paragraphs"]) == 3
    assert detail["page_sizes"] == [[595, 842]]
    assert detail["paragraphs"][1]["text"].startswith("This method")
    paragraph = detail["paragraphs"][1]
    image = client.get(f"/api/papers/{p['id']}/pages/1.png", params={"paragraph_id": paragraph["id"]})
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    group = project(client)
    duplicate = client.post("/api/papers/upload", files={"file": ("copy.pdf", data, "application/pdf")}, data={"project_id": group["id"]})
    assert duplicate.json()["duplicate"] is True
    assert len(client.get("/api/library").json()["papers"]) == 1
    assert client.get("/api/library").json()["projects"][1]["paper_count"] == 1


def test_invalid_pdf_and_encrypted_pdf(client):
    assert client.post("/api/papers/upload", files={"file": ("bad.pdf", b"not a pdf")}).status_code == 400
    with fitz.open(stream=pdf_bytes(), filetype="pdf") as d:
        data = d.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="reader")
    assert client.post("/api/papers/upload", files={"file": ("locked.pdf", data)}).status_code == 400
    assert client.get("/api/library").json()["papers"] == []


def test_scanned_document_still_readable(client):
    with fitz.open() as d:
        d.new_page().draw_rect(fitz.Rect(40, 40, 300, 400), color=(0, .5, 0))
        r = client.post("/api/papers/upload", files={"file": ("scan.pdf", d.tobytes())})
    assert r.status_code == 200 and "未提取到文字" in r.json()["warning"]
    p = r.json()["paper"]
    assert client.get(f"/api/papers/{p['id']}/pages/1.png").status_code == 200


def test_idea_persistence_and_foreign_paragraph_rejected(client):
    p = upload(client)
    q = upload(client, pdf_bytes("Another paper", "A different body text."))
    para = client.get("/api/papers/" + p["id"]).json()["paragraphs"][1]
    payload = {"title": "Ground retrieval decisions", "body": "Test a calibration criterion.", "paper_id": p["id"], "paragraph_id": para["id"], "quote": para["text"]}
    saved = client.post("/api/ideas", json=payload)
    assert saved.status_code == 200
    assert client.get("/api/ideas").json()[0]["quote"] == para["text"]
    invalid = client.post("/api/ideas", json={**payload, "paper_id": q["id"]})
    assert invalid.status_code == 400
    changed = client.put("/api/ideas/" + saved.json()["id"], json={**payload, "status": "testing"})
    assert changed.status_code == 200 and changed.json()["status"] == "testing"
    # Open a fresh DB connection: the durable state is not merely frontend memory.
    assert db.one("SELECT status FROM ideas WHERE id=?", (saved.json()["id"],))["status"] == "testing"


def test_no_key_explicit_error_and_no_fake_answer(client):
    p = upload(client)
    r = client.post("/api/chat", json={"paper_id": p["id"], "question": "What is the method?"})
    assert r.status_code == 428
    assert client.get(f"/api/papers/{p['id']}/conversations").json() == []


def test_chat_citations_history_and_scope(client, monkeypatch):
    p = upload(client)
    paragraphs = client.get("/api/papers/" + p["id"]).json()["paragraphs"]
    captured = []
    async def mock_response(instructions, messages, **kwargs):
        captured.append(messages)
        return {"content": "The evidence says this. [P1]", "citations": [], "model": "test-model", "usage": None}
    monkeypatch.setattr(ai, "respond", mock_response)
    body = {"paper_id": p["id"], "paragraph_id": paragraphs[1]["id"], "question": "Explain this method"}
    r = client.post("/api/chat", json=body)
    assert r.status_code == 200
    result = r.json()
    assert result["citations"][0]["paper_id"] == p["id"]
    assert result["citations"][0]["paragraph_id"] in {p["id"] for p in paragraphs}
    assert "This method retrieves" in captured[0][-1]["content"][0]["text"]
    second = client.post("/api/chat", json={**body, "question": "What are the assumptions?", "conversation_id": result["conversation_id"]})
    assert second.status_code == 200 and len(captured[1]) == 3
    history = client.get("/api/conversations/" + result["conversation_id"]).json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant", "user", "assistant"]
    invalid = client.post("/api/chat", json={**body, "paragraph_id": paragraphs[0]["id"], "conversation_id": result["conversation_id"]})
    assert invalid.status_code == 400


def test_settings_do_not_disclose_or_persist_key(client):
    secret = "sk-test-nonfunctional-fixture"
    r = client.put("/api/settings", json={"model": "model-for-test", "api_key": secret})
    assert r.status_code == 200 and r.json()["has_key"]
    assert secret not in r.text
    assert secret not in client.get("/api/settings").text
    assert all(secret not in row["value"] for row in db.rows("SELECT * FROM settings"))
    assert client.put("/api/settings", json={"base_url": "http://example.com/v1"}).status_code == 400


def test_changing_destination_does_not_reuse_old_secret(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-fixture")
    assert ai.settings()["has_key"]
    client.put("/api/settings", json={"api_key": "sk-session-fixture"})
    changed = client.put("/api/settings", json={"base_url": "https://different.example/v1"})
    assert not changed.json()["has_key"]


def test_cross_origin_and_missing_header_rejected(client):
    r = client.post("/api/projects", json={"name": "intruder"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    with TestClient(app) as outsider:
        assert outsider.post("/api/projects", json={"name": "x"}).status_code == 403


def test_metadata_then_pdf_attachment_preserves_idea(client):
    result = client.post("/api/papers/metadata", json={"title": "Metadata title", "abstract": "Important metadata evidence"})
    p = result.json()["paper"]
    i = client.post("/api/ideas", json={"title": "Idea", "body": "Body", "paper_id": p["id"]}).json()
    _, context, _ = ai.paper_context(p["id"], None, "evidence")
    assert "Important metadata evidence" in context
    attached = upload(client, attach_id=p["id"])
    assert attached["id"] == p["id"] and attached["title"] == "Metadata title"
    assert client.get("/api/ideas").json()[0]["id"] == i["id"]


def test_group_suggestions_and_memberships(client):
    p = upload(client)
    group = project(client)
    result = client.post("/api/organize/suggest").json()
    assert any(s["paper_id"] == p["id"] and s["project_id"] == group["id"] for s in result["suggestions"])
    assert client.post("/api/organize/apply", json={"assignments": [{"paper_id": p["id"], "project_id": group["id"]}]}).status_code == 200
    assert group["id"] in client.get("/api/library").json()["papers"][0]["project_ids"]
    assert client.delete(f"/api/projects/{group['id']}/papers/{p['id']}").status_code == 200
    assert client.get("/api/library").json()["papers"][0]["has_pdf"]


def test_group_apply_rolls_back_on_bad_group(client):
    p = upload(client)
    group = project(client)
    r = client.post("/api/organize/apply", json={"assignments": [{"paper_id": p["id"], "project_id": group["id"]}], "new_groups": [{"name": "Bad", "paper_ids": ["missing-id"]}]})
    assert r.status_code == 404
    assert db.rows("SELECT * FROM project_papers") == []


def test_export_restorable_and_has_no_credentials(client):
    p = upload(client)
    client.put("/api/settings", json={"api_key": "sk-test-secret"})
    client.post("/api/ideas", json={"title": "Durable idea", "body": "Test", "paper_id": p["id"]})
    r = client.get("/api/export")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        assert "library.sqlite3" in archive.namelist()
        assert "papers/" + p["id"] + ".pdf" in archive.namelist()
        assert any(name.startswith("ideas/") for name in archive.namelist())
        data = archive.read("library.json")
        assert b"sk-test-secret" not in data
        assert json.loads(data)["ideas"][0]["title"] == "Durable idea"


def test_search_partial_failure_is_explicit(client, monkeypatch):
    async def fail(*args, **kwargs):
        raise httpx.ReadTimeout("fixture timeout")
    async def ok(*args, **kwargs):
        return [{"title": "Real source fixture", "url": "https://arxiv.org/abs/2005.11401", "source": "arXiv"}]
    monkeypatch.setattr(research, "semantic_scholar", fail)
    monkeypatch.setattr(research, "crossref", fail)
    monkeypatch.setattr(research, "arxiv", ok)
    r = client.post("/api/research/search", json={"query": "retrieval"}).json()
    assert len(r["papers"]) == 1 and len(r["warnings"]) == 2
    assert all("不覆盖" in w for w in r["warnings"])


def test_idea_analysis_keeps_sources_queries_and_scope(client, monkeypatch):
    monkeypatch.setattr(ai, "_session_key", "sk-fixture-only")
    async def found(*args, **kwargs):
        return {"papers": [{"title": "Verified metadata", "url": "https://example.org/paper", "abstract": "Partial evidence"}], "warnings": []}
    captured = []
    async def response(instructions, messages, **kwargs):
        captured.append((messages, kwargs))
        return {"content": "Evidence is insufficient for novelty. [Source](https://example.org/paper)", "citations": []}
    monkeypatch.setattr(research, "search", found)
    monkeypatch.setattr(ai, "respond", response)
    idea = client.post("/api/ideas", json={"title": "Research idea", "body": "Compare retrieval quality"}).json()
    r = client.post(f"/api/ideas/{idea['id']}/analyze", json={"query": "retrieval quality", "web": False})
    assert r.status_code == 200 and not captured[0][1]["web"]
    assert "未开启联网" in captured[0][0][0]["content"]
    saved = client.get(f"/api/ideas/{idea['id']}/analyses").json()[0]
    assert saved["queries"] == ["retrieval quality"]
    assert saved["sources"][0]["type"] == "candidate"


def test_unsafe_arxiv_url_and_metadata_link_rejected(client):
    for url in ("http://127.0.0.1/secrets", "https://arxiv.org.evil.example/abs/2409.13740", "../../private"):
        assert client.post("/api/papers/arxiv", json={"arxiv": url}).status_code == 400
    assert client.post("/api/papers/metadata", json={"title": "Bad URL", "url": "javascript:alert(1)"}).status_code == 400


def test_page_cross_paper_anchor_rejected(client):
    p = upload(client)
    q = upload(client, pdf_bytes("Distinct PDF"))
    para = client.get("/api/papers/" + q["id"]).json()["paragraphs"][0]
    assert client.get(f"/api/papers/{p['id']}/pages/1.png", params={"paragraph_id": para["id"]}).status_code == 400
    assert client.get(f"/api/papers/{p['id']}/pages/600.png").status_code == 400


def test_provider_request_schema_citations_and_error_redaction(client, monkeypatch):
    monkeypatch.setattr(ai, "_session_key", "sk-fixture-only")
    captured = []
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            captured.append((url, kwargs))
            raw = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Evidence HERE.", "annotations": [{"type": "url_citation", "start_index": 9, "end_index": 13, "title": "Primary paper", "url": "https://example.org/paper"}]}]}]}
            return httpx.Response(200, json=raw, request=httpx.Request("POST", url))
    monkeypatch.setattr(ai.httpx, "AsyncClient", FakeClient)
    import asyncio
    result = asyncio.run(ai.respond("Test", [{"role": "user", "content": "Question"}], web=True))
    assert captured[0][0].endswith("/v1/responses")
    payload = captured[0][1]["json"]
    assert payload["store"] is False and payload["tools"] == [{"type": "web_search"}]
    assert "[Primary paper](https://example.org/paper)" in result["content"]
    assert result["citations"][0]["url"] == "https://example.org/paper"


def test_static_app_assets_are_available(client):
    assert client.get("/").status_code == 200
    for path in ("/static/app.js", "/static/style.css", "/static/vendor/marked.js", "/static/vendor/purify.js"):
        assert client.get(path).status_code == 200
    assert "frame-ancestors 'self'" in client.get("/").headers["content-security-policy"]


def test_zotero_reads_metadata_and_copies_local_pdf(client, monkeypatch, tmp_path):
    from app import main
    path = tmp_path / "zotero-paper.pdf"
    path.write_bytes(pdf_bytes("Zotero source document"))
    class ZoteroClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            request = httpx.Request("GET", url)
            if url.endswith("/children"):
                return httpx.Response(200, json=[{"key": "PDF12345", "data": {"itemType": "attachment", "contentType": "application/pdf"}}], request=request)
            if url.endswith("/file"):
                return httpx.Response(302, headers={"location": path.as_uri()}, request=request)
            return httpx.Response(200, headers={"Zotero-Server-ID": "test-library"}, json={"key": "ABCD1234", "data": {"title": "Zotero source document", "creators": [{"firstName": "A", "lastName": "Researcher"}], "date": "2025", "itemType": "conferencePaper"}}, request=request)
    monkeypatch.setattr(main.httpx, "AsyncClient", ZoteroClient)
    r = client.post("/api/zotero/import", json={"keys": ["ABCD1234"]})
    assert r.status_code == 200 and not r.json()["warnings"]
    p = client.get("/api/library").json()["papers"][0]
    assert p["has_pdf"] and p["source"] == "Zotero" and p["authors"] == "A Researcher"
    assert path.exists(), "Source PDF must not be moved or modified"
    duplicate = client.post("/api/zotero/import", json={"keys": ["ABCD1234"]})
    assert len(duplicate.json()["imported"]) == 1
    assert len(client.get("/api/library").json()["papers"]) == 1
