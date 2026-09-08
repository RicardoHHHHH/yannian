import asyncio
import io
import json
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import ai, chat, codex_bridge as bridge, db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(bridge, '_cached_status', {'ready': True, 'models': [
        {'id': 'model-a', 'name': 'A', 'is_default': True, 'efforts': ['low', 'high'], 'default_effort': 'low'},
        {'id': 'model-b', 'name': 'B', 'efforts': ['medium', 'xhigh'], 'default_effort': 'medium'}]})
    with TestClient(app, headers={'X-Yannian': '1'}) as client:
        yield client
    assert not chat._tasks and not chat._live


def paper(client):
    return client.post('/api/papers/metadata', json={'title': 'Test paper', 'abstract': 'Evidence and methods'}).json()['paper']


def wait_run(client, run_id, terminal=True):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = client.get('/api/chat/runs/' + run_id).json()
        if (run['status'] not in chat.ACTIVE) if terminal else bool(run['content']):
            return run
        time.sleep(.01)
    pytest.fail('Reading turn did not reach the expected state')


def test_stream_stop_persists_partial_history_then_continues_with_new_model(client, monkeypatch):
    cancelled = threading.Event()
    calls = []
    async def reply(instructions, messages, **kwargs):
        calls.append((messages, kwargs))
        if len(calls) == 1:
            kwargs['on_event']({'content': 'Partial answer [P1]', 'phase': '正在回答'})
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return {'content': 'Follow-up answer', 'citations': [], 'model': kwargs['model']}
    monkeypatch.setattr(ai, 'respond', reply)
    p = paper(client)
    r = client.post('/api/chat/runs', json={'paper_id': p['id'], 'question': 'First question', 'model': 'model-a', 'effort': 'high'})
    assert r.status_code == 202
    first = wait_run(client, r.json()['id'], terminal=False)
    assert first['status'] == 'running' and first['content'].startswith('Partial')
    overlap = client.post('/api/chat/runs', json={'paper_id': p['id'], 'question': 'Accidental duplicate'})
    assert overlap.status_code == 409
    stopped = client.post('/api/chat/runs/' + first['id'] + '/cancel').json()
    assert cancelled.is_set() and stopped['status'] == 'stopped'
    assert stopped['content'] == first['content']
    assert client.post('/api/chat/runs/' + first['id'] + '/cancel').json()['status'] == 'stopped'
    saved = client.get('/api/conversations/' + first['conversation_id']).json()['messages']
    assert len(saved) == 2 and saved[1]['status'] == 'stopped'
    assert saved[1]['model'] == 'model-a' and saved[1]['effort'] == 'high'
    second = client.post('/api/chat/runs', json={'paper_id': p['id'], 'conversation_id': first['conversation_id'], 'question': 'Continue', 'model': 'model-b', 'effort': 'xhigh'}).json()
    assert wait_run(client, second['id'])['status'] == 'completed'
    assert calls[1][0][0]['content'] == 'First question'
    assert '未完成' in calls[1][0][1]['content']
    assert calls[1][1]['model'] == 'model-b' and calls[1][1]['effort'] == 'xhigh'
    assert db.setting('codex_model') is None  # Per-turn choice must not alter other workflows.


def test_context_changes_keep_conversation_but_other_papers_are_rejected(client, monkeypatch):
    captured = []
    async def reply(instructions, messages, **kwargs):
        captured.append(messages)
        return {'content': 'Answer', 'citations': []}
    monkeypatch.setattr(ai, 'respond', reply)
    p = paper(client)
    db.execute('INSERT INTO paragraphs VALUES (?,?,?,?,?,?,?)', ('para-a', p['id'], 1, 1, 'A selected paragraph', '[0,0,1,1]', 'text'))
    first = client.post('/api/chat/runs', json={'paper_id': p['id'], 'question': 'First'}).json()
    wait_run(client, first['id'])
    second = client.post('/api/chat/runs', json={'paper_id': p['id'], 'conversation_id': first['conversation_id'], 'paragraph_id': 'para-a', 'selected_text': 'A selected paragraph', 'question': 'Explain this too'})
    assert second.status_code == 202
    result = wait_run(client, second.json()['id'])
    assert result['context_info']['included_messages'] == 2 and len(captured[1]) == 3
    assert 'A selected paragraph' in captured[1][-1]['content'][0]['text']
    other = client.post('/api/papers/metadata', json={'title': 'Different paper'}).json()['paper']
    assert client.post('/api/chat/runs', json={'paper_id': other['id'], 'conversation_id': first['conversation_id'], 'question': 'Wrong paper'}).status_code == 400


def test_invalid_model_and_effort_do_not_create_turns(client):
    p = paper(client)
    for payload in ({'model': 'unavailable'}, {'model': 'model-a', 'effort': 'xhigh'}, {'question': '   '}):
        response = client.post('/api/chat/runs', json={'paper_id': p['id'], 'question': 'Question', **payload})
        assert response.status_code == 400
    assert not db.rows('SELECT * FROM conversations')


def test_text_only_model_rejects_page_image_before_creating_a_turn(client):
    p = paper(client)
    bridge._cached_status['models'][0]['input_modalities'] = ['text']
    response = client.post('/api/chat/runs', json={'paper_id': p['id'], 'question': 'Look at this', 'model': 'model-a', 'include_page': True})
    assert response.status_code == 400 and '不支持图像' in response.json()['detail']
    assert not db.rows('SELECT * FROM messages')


def test_history_keeps_more_than_ten_messages_and_reports_omitted_context(client, monkeypatch):
    p = paper(client)
    db.execute('INSERT INTO conversations(id,paper_id,title,created_at) VALUES (?,?,?,?)', ('conversation-a', p['id'], 'Conversation', db.now()))
    for i in range(24):
        db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', (str(i), 'conversation-a', 'user' if i % 2 == 0 else 'assistant', 'message ' + str(i), '[]', '2026-01-01'))
    messages, info = chat.history('conversation-a')
    assert len(messages) == 24 and info['omitted_messages'] == 0
    monkeypatch.setattr(chat, 'HISTORY_CHARS', 75)
    messages, info = chat.history('conversation-a')
    assert 0 < info['included_messages'] < 24 and info['omitted_messages'] > 0
    assert messages[0]['role'] == 'user' and messages[-1]['content'] == 'message 23'
    assert len(client.get('/api/conversations/conversation-a').json()['messages']) == 24


def test_failed_turn_and_restart_are_not_presented_as_completed(client, monkeypatch):
    async def fail(*args, **kwargs):
        raise HTTPException(429, 'Codex 额度暂时不可用')
    monkeypatch.setattr(ai, 'respond', fail)
    p = paper(client)
    started = client.post('/api/chat/runs', json={'paper_id': p['id'], 'question': 'Q'}).json()
    failed = wait_run(client, started['id'])
    assert failed['status'] == 'failed' and '额度' in failed['error']
    failed.update(status='running', content='Saved partial')
    chat.persist(failed)
    chat.init()
    recovered = chat.get(failed['id'])
    assert recovered['status'] == 'interrupted' and recovered['content'] == 'Saved partial'
    assert '重启' in recovered['error']


def test_transport_sends_turn_interrupt_and_waits_for_completion():
    cancel = threading.Event()
    server = bridge.AppServer(cancelled=cancel)
    output = io.StringIO()
    server.process = type('Process', (), {'stdin': output})()
    server.active_thread, server.active_turn = 'thread-a', 'turn-a'
    cancel.set()
    server.events.put({'id': 1, 'result': {}})
    server.events.put({'method': 'turn/completed', 'params': {'turn': {'status': 'interrupted'}}})
    with pytest.raises(HTTPException) as e:
        server.next_event()
    assert e.value.status_code == 499
    request = json.loads(output.getvalue())
    assert request['method'] == 'turn/interrupt'
    assert request['params'] == {'threadId': 'thread-a', 'turnId': 'turn-a'}


def test_async_codex_cancel_waits_for_worker_cleanup(monkeypatch):
    entered, cleaned = threading.Event(), threading.Event()
    def worker(*args, cancelled, **kwargs):
        entered.set()
        assert cancelled.wait(3)
        cleaned.set()
        raise HTTPException(499, 'Cancelled')
    monkeypatch.setattr(bridge, 'run', worker)
    async def exercise():
        task = asyncio.create_task(bridge.respond('Instructions', []))
        while not entered.is_set():
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
    asyncio.run(exercise())


def test_api_model_effort_and_cancel_close_the_response_connection(client, monkeypatch):
    started, closed = threading.Event(), threading.Event()
    payloads = []
    class Transport:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): closed.set()
        async def post(self, url, json, **kwargs):
            payloads.append(json)
            started.set()
            await asyncio.sleep(30)
    monkeypatch.setattr(ai.httpx, 'AsyncClient', Transport)
    monkeypatch.setattr(ai, '_session_key', 'fixture-key-never-sent')
    db.save_setting('provider', 'api')
    p = paper(client)
    run = client.post('/api/chat/runs', json={'paper_id': p['id'], 'question': 'Q', 'model': 'api-selected', 'effort': 'high'}).json()
    assert started.wait(2)
    stopped = client.post('/api/chat/runs/' + run['id'] + '/cancel').json()
    assert stopped['status'] == 'stopped' and closed.is_set()
    assert payloads[0]['model'] == 'api-selected' and payloads[0]['reasoning'] == {'effort': 'high'}


def test_region_question_displays_image_but_sends_pixels_and_auxiliary_text(client, monkeypatch):
    import pymupdf as fitz
    captured = []
    async def reply(instructions, messages, **kwargs):
        captured.append(messages)
        return {'content': 'Figure answer', 'citations': []}
    monkeypatch.setattr(ai, 'respond', reply)
    with fitz.open() as document:
        page = document.new_page(width=400, height=300)
        page.draw_rect(fitz.Rect(20, 60, 380, 210), color=(1, 0, 0))
        page.insert_text((30, 90), 'Figure labels: Challenger Executor Reward')
        pdf = document.tobytes()
    p = client.post('/api/papers/upload', files={'file': ('fixture.pdf', pdf, 'application/pdf')}).json()['paper']
    selection = client.post('/api/papers/' + p['id'] + '/selections', json={'page': 1, 'kind': 'region', 'rects': [[0, 0, 1, 1]]}).json()
    run = client.post('/api/chat/runs', json={'paper_id': p['id'], 'paragraph_id': selection['paragraph_id'], 'selection_id': selection['id'], 'question': 'Explain this figure'}).json()
    wait_run(client, run['id'])
    user = client.get('/api/conversations/' + run['conversation_id']).json()['messages'][0]
    assert user['content'] == user['display_content'] == 'Explain this figure'
    assert user['attachments'][0]['kind'] == 'region'
    assert user['attachments'][0]['image_url'] == selection['image_url']
    sent = captured[0][-1]['content']
    assert sent[1]['type'] == 'input_image' and sent[1]['image_url'].startswith('data:image/png;base64,')
    assert 'Challenger' in sent[0]['text'] and '可能乱序' in sent[0]['text']
    # Read legacy conversations cleanly without rewriting their stored messages.
    legacy = 'Explain this figure\n我选中的文字：\n' + selection['text'] + '\nPDF 选区 [S1]：第 1 页，类型 region。'
    db.execute("UPDATE messages SET content=?,citations='[]' WHERE id=?", (legacy, user['id']))
    restored = client.get('/api/conversations/' + run['conversation_id']).json()['messages'][0]
    assert restored['display_content'] == 'Explain this figure' and restored['attachments'][0]['kind'] == 'region'
    assert db.one('SELECT content FROM messages WHERE id=?', (user['id'],))['content'] == legacy
    history, _ = chat.history(run['conversation_id'])
    assert history[0]['content'].count('Challenger') == 1
    assert '可能乱序' in history[0]['content']
