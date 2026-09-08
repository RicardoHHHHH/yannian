import asyncio
import io
import json
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import ai, codex_bridge as bridge, db, research
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "data")
    monkeypatch.setattr(ai, "_session_key", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    report = {"ready": True, "installed": True, "auth_type": "chatgpt", "plan": "pro", "models": [], "message": "已连接"}
    monkeypatch.setattr(bridge, "_cached_status", report)
    monkeypatch.setattr(bridge, "status", lambda *args: report)
    with TestClient(app, headers={"X-Yannian": "1"}) as c:
        yield c


def test_codex_is_default_and_ready_without_api_key(client):
    settings = client.get("/api/settings").json()
    assert settings["provider"] == "codex" and settings["ready"] and not settings["has_key"]
    assert settings["codex_model"] == ""
    assert client.get("/api/codex/status").json()["auth_type"] == "chatgpt"
    assert client.put("/api/settings", json={"provider": "unknown"}).status_code == 422
    assert client.put("/api/settings", json={"codex_effort": "ultra"}).status_code == 422


def test_provider_switch_keeps_separate_models_and_never_requires_key_for_codex(client):
    r = client.put("/api/settings", json={"provider": "api", "api_key": "sk-test-secret", "model": "api-test"})
    assert r.json()["ready"]
    r = client.put("/api/settings", json={"provider": "codex", "model": "api-test", "codex_model": "codex-test", "codex_effort": "high"})
    s = r.json()
    assert s["codex_model"] == "codex-test" and s["model"] == "api-test" and s["ready"]
    assert "sk-test-secret" not in r.text


def test_codex_routes_all_ai_workflows_and_keeps_local_history(client, monkeypatch):
    monkeypatch.setattr(db, "now", lambda: "2026-09-07T00:00:00Z")  # Timestamps may tie on Windows.
    captured = []
    async def reply(instructions, messages, **kwargs):
        captured.append((instructions, messages, kwargs))
        content = '{"suggestions":[],"new_groups":[]}' if "只输出JSON" in instructions else "分析结果与依据 [P1]"
        return {"content": content, "citations": [], "model": "codex-test", "provider": "codex"}
    async def search(*args, **kwargs):
        return {"papers": [], "warnings": ["fixture"], "searched_at": "2026-09-07"}
    monkeypatch.setattr(bridge, "respond", reply)
    monkeypatch.setattr(research, "search", search)
    paper = client.post("/api/papers/metadata", json={"title": "Research"}).json()["paper"]
    first = client.post("/api/chat", json={"paper_id": paper["id"], "question": "First question"}).json()
    second = client.post("/api/chat", json={"paper_id": paper["id"], "question": "Follow-up", "conversation_id": first["conversation_id"]})
    assert second.status_code == 200 and len(captured[-1][1]) == 3
    assert captured[-1][1][0]["content"] == "First question"
    history = client.get("/api/conversations/" + first["conversation_id"]).json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    idea = client.post("/api/ideas", json={"title": "Idea", "body": "Hypothesis"}).json()
    assert client.post(f"/api/ideas/{idea['id']}/analyze", json={"query": "retrieval test", "web": True}).status_code == 200
    assert captured[-1][2]["web"] is True
    assert client.post("/api/organize/ai").status_code == 200
    assert client.post("/api/settings/test").json()["provider"] == "codex"
    assert not ai.key()


def test_codex_failure_never_falls_back_to_api(client, monkeypatch):
    monkeypatch.setattr(ai, "_session_key", "sk-existing-key")
    async def unavailable(*args, **kwargs):
        raise HTTPException(429, "Codex 额度已用完")
    monkeypatch.setattr(bridge, "respond", unavailable)
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kwargs: pytest.fail("API fallback must not happen"))
    response = client.post("/api/settings/test")
    assert response.status_code == 429 and "Codex" in response.json()["detail"]


class FakeServer:
    account_type = "chatgpt"
    stream = []
    calls = []
    sent = []
    closed = False

    def __init__(self, **kwargs):
        self.events = iter(self.stream)
        self.serial = 3
        self.calls.clear()
        self.sent.clear()

    def __enter__(self): return self
    def __exit__(self, *_): self.closed = True
    def send(self, message): self.sent.append(message)
    def next_event(self): return next(self.events)
    def rpc(self, method, params):
        self.calls.append((method, params))
        if method == "account/read": return {"account": {"type": self.account_type}}
        if method == "thread/start": return {"thread": {"id": "fixture-thread"}, "model": "actual-model"}


def test_protocol_keeps_final_answer_images_and_restricted_thread(monkeypatch):
    monkeypatch.setattr(bridge, "AppServer", FakeServer)
    monkeypatch.setattr(FakeServer, "stream", [
        {"method": "item/completed", "params": {"item": {"id": "a", "type": "agentMessage", "text": "Working...", "phase": "commentary"}}},
        {"method": "item/completed", "params": {"item": {"id": "b", "type": "agentMessage", "text": "Answer [P2] [Source](https://example.org/paper) \ue200cite\ue202turn1search0\ue201", "phase": "final_answer"}}},
        {"method": "item/completed", "params": {"item": {"id": "w", "type": "webSearch"}}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed", "items": []}}},
    ])
    result = bridge.run("Test", [{"role": "user", "content": [{"type": "input_text", "text": "Question"}, {"type": "input_image", "image_url": "data:image/png;base64,test"}]}], web=True)
    assert result["content"] == "Answer [P2] [Source](https://example.org/paper)"
    assert result["web_searched"] and result["model"] == "actual-model" and len(result["citations"]) == 1
    params = FakeServer.calls[1][1]
    assert params["ephemeral"] and params["environments"] == [] and params["sandbox"] == "read-only"
    assert params["approvalPolicy"] == "never" and params["config"]["web_search"] == "live"
    assert FakeServer.sent[0]["params"]["input"][1]["type"] == "image"


def test_api_authenticated_codex_is_rejected_before_any_turn(monkeypatch):
    monkeypatch.setattr(bridge, "AppServer", FakeServer)
    monkeypatch.setattr(FakeServer, "account_type", "apiKey")
    with pytest.raises(HTTPException) as e:
        bridge.run("Test", [])
    assert e.value.status_code == 428
    assert [name for name, _ in FakeServer.calls] == ["account/read"]


def test_completed_without_web_search_has_explicit_limitation(monkeypatch):
    monkeypatch.setattr(bridge, "AppServer", FakeServer)
    monkeypatch.setattr(FakeServer, "stream", [{"method": "turn/completed", "params": {"turn": {"status": "completed", "items": [{"type": "agentMessage", "id": "1", "text": "Answer", "phase": "final_answer"}]}}}])
    result = bridge.run("Test", [], web=True)
    assert not result["web_searched"] and "未检测到" in result["content"]


def test_failed_turn_is_not_persisted_as_success(monkeypatch):
    monkeypatch.setattr(bridge, "AppServer", FakeServer)
    monkeypatch.setattr(FakeServer, "stream", [{"method": "turn/completed", "params": {"turn": {"status": "failed", "error": {"codexErrorInfo": "UsageLimitExceeded", "message": "private-token"}}}}])
    with pytest.raises(HTTPException) as e:
        bridge.run("Test", [])
    assert e.value.status_code == 429 and "private-token" not in e.value.detail
    assert not bridge._run_lock.locked()


def test_transport_rejects_approval_requests_and_honors_timeout():
    server = bridge.AppServer()
    output = io.StringIO()
    server.process = type("Process", (), {"stdin": output})()
    server.events.put({"id": "approval", "method": "item/commandExecution/requestApproval"})
    server.events.put({"id": 1, "result": {"ok": True}})
    assert server.next_event()["result"]["ok"]
    assert json.loads(output.getvalue())["result"]["decision"] == "decline"
    server.deadline = time.monotonic() - 1
    with pytest.raises(HTTPException) as e: server.next_event()
    assert e.value.status_code == 504


def test_cancel_and_overlap_are_explicit():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(HTTPException) as e: bridge.AppServer(cancelled=cancel).next_event()
    assert e.value.status_code == 499
    bridge._run_lock.acquire()
    try:
        with pytest.raises(HTTPException) as e: bridge.run("Test", [])
        assert e.value.status_code == 409
    finally:
        bridge._run_lock.release()


def test_arbitrary_image_urls_are_not_forwarded():
    with pytest.raises(HTTPException) as e:
        bridge.turn_input([{"content": [{"type": "input_image", "image_url": "file:///private/key"}]}])
    assert e.value.status_code == 400
