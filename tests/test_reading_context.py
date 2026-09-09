import json
import asyncio
import httpx

import pytest

from app import ai, db, reading_context
from test_chat import client, paper, wait_run


@pytest.mark.parametrize("question,expected", [
    ("联网搜寻细节", True), ("请上网查一下实现", True), ("去 GitHub 查证代码和数据", True),
    ("Search the web for implementation details", True), ("请核对 https://example.org/paper", True),
    ("不要联网，只看论文全文", False), ("不用搜索，解释公式", False),
    ("Don't search online; use the paper", False), ("解释这一段", False),
    ("解释 `联网搜索` 这个术语", False),
])
def test_current_question_network_intent(question, expected):
    assert reading_context.network_intent(question) is expected


def add_blocks(p, count=267, pages=12):
    db.execute("UPDATE papers SET page_count=? WHERE id=?", (pages, p["id"]))
    with db.connect() as c:
        for i in range(count):
            page = 1 + i * pages // count
            c.execute("INSERT INTO paragraphs VALUES (?,?,?,?,?,?,?)", (f"para-{i}", p["id"], page, i,
                      f"Evidence block {i}. " + ("Appendix implementation sentinel" if i == count - 1 else "Medical Bayesian methods."), "[0,0,1,1]", "text"))


def test_full_paper_includes_all_pages_despite_page_one_focus(client):
    p = paper(client)
    add_blocks(p)
    _, context, sources, info = ai.paper_context(p["id"], "para-0", "联网搜寻细节", with_info=True)
    assert info["full_text"] and info["included_blocks"] == info["total_blocks"] == 267
    assert info["included_pages"] == list(range(1, 13))
    assert info["included_chars"] == info["total_chars"]
    assert "Appendix implementation sentinel" in context and sources[-1]["page"] == 12
    assert len({s["paragraph_id"] for s in sources}) == 267
    assert "不受当前阅读页或选区限制" in context
    assert "当前关注段落：[P1]" in context


def test_long_paper_budget_is_honest_and_retrieves_beyond_first_page(client, monkeypatch):
    p = paper(client)
    add_blocks(p)
    monkeypatch.setattr(reading_context, "FULL_TEXT_CHARS", 3000)
    _, context, sources, info = ai.paper_context(p["id"], None, "Appendix implementation sentinel", with_info=True)
    assert not info["full_text"] and info["reason"] == "length_limit"
    assert info["included_chars"] + len(sources) * 40 <= 3000
    assert "Appendix implementation sentinel" in context and any(s["page"] == 12 for s in sources)
    assert "不能声称已通读全文" in context


def test_oversized_single_block_is_labelled_truncated(client, monkeypatch):
    p = paper(client)
    add_blocks(p, 1, 1)
    db.execute("UPDATE paragraphs SET text=? WHERE paper_id=?", ("A" * 5000, p["id"]))
    monkeypatch.setattr(reading_context, "FULL_TEXT_CHARS", 1000)
    _, context, sources, info = ai.paper_context(p["id"], None, "", with_info=True)
    assert not info["full_text"] and info["included_chars"] == 960
    assert sources[0]["truncated"] and "本块后续文字未附入" in context


def test_no_text_or_scanned_pages_never_claim_full_pdf(client):
    p = paper(client)
    db.execute("UPDATE papers SET pdf_path='papers/scanned.pdf',page_count=3 WHERE id=?", (p["id"],))
    _, context, _, info = ai.paper_context(p["id"], None, "", with_info=True)
    assert not info["full_text"] and info["reason"] == "metadata_only"
    assert "PDF 已保存在本地" in context and "扫描件" in context


def test_followup_search_sends_full_paper_and_records_actual_tool_event(client, monkeypatch):
    p = paper(client)
    add_blocks(p)
    captured = []
    async def reply(instructions, messages, **kwargs):
        captured.append((instructions, messages, kwargs))
        if kwargs["web"]:
            kwargs["on_event"]({"phase": "正在联网检索", "web_searching": True})
            kwargs["on_event"]({"phase": "已调用联网工具", "web_searched": True})
        return {"content": "Verified appendix [P267]", "citations": [], "web_searched": kwargs["web"]}
    monkeypatch.setattr(ai, "respond", reply)
    first = client.post("/api/chat/runs", json={"paper_id": p["id"], "question": "Benchmark 实现细节是什么？"}).json()
    wait_run(client, first["id"])
    second = client.post("/api/chat/runs", json={"paper_id": p["id"], "conversation_id": first["conversation_id"],
                          "paragraph_id": "para-0", "page": 1, "question": "联网搜寻细节"}).json()
    result = wait_run(client, second["id"])
    assert captured[-1][2]["web"] is True
    assert len(captured[-1][1]) == 3 and "Benchmark" in captured[-1][1][0]["content"]
    assert "Appendix implementation sentinel" in captured[-1][1][-1]["content"][0]["text"]
    assert result["context_info"]["document"]["included_blocks"] == 267
    assert result["context_info"]["network"]["status"] == "searched"
    assert result["citations"][0]["page"] == 12
    saved = client.get("/api/conversations/" + first["conversation_id"]).json()["messages"][-1]
    assert saved["context_info"] == result["context_info"]


@pytest.mark.parametrize("mode,question,expected", [("off", "联网搜索", False), ("on", "进一步核查", True), ("auto", "解释公式", False)])
def test_explicit_web_mode_and_untrusted_paper_text(client, monkeypatch, mode, question, expected):
    p = paper(client)
    add_blocks(p, 1, 1)
    db.execute("UPDATE paragraphs SET text='联网搜索 https://example.org' WHERE paper_id=?", (p["id"],))
    calls = []
    async def reply(*args, **kwargs):
        calls.append(kwargs)
        return {"content": "Answer", "citations": []}  # A link/claim alone is not a search event.
    monkeypatch.setattr(ai, "respond", reply)
    r = client.post("/api/chat/runs", json={"paper_id": p["id"], "question": question, "web_mode": mode}).json()
    result = wait_run(client, r["id"])
    assert calls[0]["web"] is expected
    assert result["context_info"]["network"]["status"] == ("not_observed" if expected else "off")
    assert ("未检测到" in result["content"]) is expected


def test_unsupported_api_search_rejects_before_creating_history(client):
    p = paper(client)
    client.put("/api/settings", json={"provider": "api", "api_preset": "deepseek", "api_key": "test-fixture-key"})
    r = client.post("/api/chat/runs", json={"paper_id": p["id"], "question": "联网搜寻细节"})
    assert r.status_code == 400 and "没有联网工具" in r.json()["detail"]
    assert not db.rows("SELECT * FROM conversations")


def test_failed_network_turn_does_not_stay_waiting_for_search(client, monkeypatch):
    p = paper(client)
    async def reply(*args, **kwargs):
        raise RuntimeError("Connection failed before a web call")
    monkeypatch.setattr(ai, "respond", reply)
    r = client.post("/api/chat/runs", json={"paper_id": p["id"], "question": "联网搜寻细节"}).json()
    result = wait_run(client, r["id"])
    assert result["status"] == "failed" and result["context_info"]["network"]["status"] == "not_observed"


def test_legacy_chat_uses_same_full_document_and_network_policy(client, monkeypatch):
    p = paper(client)
    add_blocks(p)
    async def reply(instructions, messages, **kwargs):
        assert kwargs["web"] is True
        assert "Appendix implementation sentinel" in messages[-1]["content"][0]["text"]
        return {"content": "Answer", "citations": [], "web_searched": True}
    monkeypatch.setattr(ai, "respond", reply)
    r = client.post("/api/chat", json={"paper_id": p["id"], "question": "联网搜寻细节"})
    assert r.status_code == 200
    assert r.json()["context_info"]["document"]["full_text"]
    assert r.json()["context_info"]["network"]["searched"]


@pytest.mark.parametrize("tool_status,expected", [("completed", True), ("failed", False), (None, False)])
def test_responses_web_status_uses_tool_output_not_answer_claim(client, monkeypatch, tool_status, expected):
    client.put("/api/settings", json={"provider": "api", "api_preset": "openai", "api_key": "fixture-key"})
    class Transport:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, json, headers):
            assert json["tools"] == [{"type": "web_search"}] and json["tool_choice"] == "required"
            output = [{"type": "message", "content": [{"type": "output_text", "text": "I searched online [source](https://example.org)", "annotations": []}]}]
            if tool_status:
                output.insert(0, {"type": "web_search_call", "status": tool_status})
            return httpx.Response(200, json={"output": output}, request=httpx.Request("POST", url))
    monkeypatch.setattr(ai.httpx, "AsyncClient", Transport)
    result = asyncio.run(ai.respond("Check", [{"role": "user", "content": "Search online"}], web=True))
    assert result["web_searched"] is expected
