import asyncio
import json
import threading
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import ai, api_providers, codex_bridge, db, research
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(ai, '_session_key', '')
    for name in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL', 'DEEPSEEK_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    report = {'ready': True, 'models': [], 'message': 'Test Codex status'}
    monkeypatch.setattr(codex_bridge, '_cached_status', report)
    monkeypatch.setattr(codex_bridge, 'status', lambda *args: report)
    with TestClient(app, headers={'X-Yannian': '1'}) as client:
        yield client


def configure(client, **extra):
    response = client.put('/api/settings', json={'provider': 'api', 'api_preset': 'deepseek', 'api_key': 'test-key', **extra})
    assert response.status_code == 200, response.text
    return response.json()


def mock_transport(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))


def test_preset_defaults_and_partial_updates(client):
    settings = configure(client)
    assert settings['model'] == 'deepseek-v4-flash'
    assert settings['api_protocol'] == 'chat_completions' and settings['ready']
    assert not settings['supports_web_search'] and not settings['api_capabilities']['images']
    cleared = client.put('/api/settings', json={'clear_key': True}).json()
    assert not cleared['ready'] and cleared['base_url'] == settings['base_url'] and cleared['model'] == settings['model']
    assert client.put('/api/settings', json={'api_protocol': 'invalid'}).status_code == 422


def test_credentials_follow_only_their_configured_destination(client, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'openai-environment-secret')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'deepseek-environment-secret')
    configure(client)
    assert ai.key() == 'test-key'
    client.put('/api/settings', json={'clear_key': True})
    assert ai.key() == 'deepseek-environment-secret'
    changed = client.put('/api/settings', json={'base_url': 'https://other.example/v1'}).json()
    assert not changed['ready'] and ai.key() == ''
    client.put('/api/settings', json={'api_preset': 'openai'})
    assert ai.key() == 'openai-environment-secret'
    assert 'secret' not in client.get('/api/settings').text
    assert all('secret' not in row['value'] and 'test-key' not in row['value'] for row in db.rows('SELECT * FROM settings'))


def test_chat_completions_converts_context_and_uses_answer_only(client, monkeypatch):
    configure(client)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'model': 'deepseek-v4-flash', 'choices': [{'message': {'content': 'Final answer', 'reasoning_content': 'private reasoning'}, 'finish_reason': 'stop'}]})
    mock_transport(monkeypatch, handler)
    result = asyncio.run(ai.respond('Explain evidence.', [{'role': 'user', 'content': 'Earlier question'},
        {'role': 'assistant', 'content': 'Earlier answer'}, {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Current excerpt'}]}]))
    payload = json.loads(requests[0].content)
    assert str(requests[0].url) == 'https://api.deepseek.com/chat/completions'
    assert requests[0].headers['authorization'] == 'Bearer test-key'
    assert payload['messages'][0]['role'] == 'system' and payload['messages'][2]['content'] == 'Earlier answer'
    assert payload['messages'][-1]['content'] == [{'type': 'text', 'text': 'Current excerpt'}]
    assert payload['thinking'] == {'type': 'disabled'} and 'tools' not in payload
    assert result['content'] == 'Final answer' and 'private reasoning' not in json.dumps(result)


@pytest.mark.parametrize('effort', ['none', 'low', 'high', 'max'])
def test_deepseek_thinking_parameters(client, monkeypatch, effort):
    configure(client)
    payloads = []
    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Answer'}, 'finish_reason': 'stop'}]})
    mock_transport(monkeypatch, handler)
    asyncio.run(ai.respond('Test', [{'role': 'user', 'content': 'Q'}], effort=effort))
    assert payloads[0]['thinking']['type'] == ('disabled' if effort == 'none' else 'enabled')
    assert payloads[0].get('reasoning_effort') == (None if effort == 'none' else effort)
    if effort != 'none':
        assert payloads[0]['max_tokens'] >= 16384


def test_images_require_a_visual_model_and_keep_original_payload(client, monkeypatch):
    configure(client)
    image = {'type': 'input_image', 'image_url': 'data:image/png;base64,aGVsbG8=', 'detail': 'high'}
    messages = [{'role': 'user', 'content': [image]}]
    with pytest.raises(HTTPException, match='不支持图像'):
        asyncio.run(ai.respond('Read figure', messages))
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Visual answer'}, 'finish_reason': 'stop'}]})
    mock_transport(monkeypatch, handler)
    asyncio.run(ai.respond('Read figure', messages, model='deepseek-v4-flash-vision-exp'))
    sent = requests[0]['messages'][-1]['content'][0]
    assert sent == {'type': 'image_url', 'image_url': {'url': image['image_url'], 'detail': 'high'}}


class Stream(httpx.AsyncByteStream):
    def __init__(self, events):
        self.events, self.closed = events, False
    async def __aiter__(self):
        for event in self.events:
            data = (': keepalive\n\ndata: ' + (json.dumps(event, ensure_ascii=False) if isinstance(event, dict) else event) + '\n\n').encode()
            # Split inside UTF-8 and JSON to exercise network chunk boundaries.
            for start in range(0, len(data), 7):
                yield data[start:start+7]
    async def aclose(self):
        self.closed = True


def test_streamed_answer_and_reasoning_status_are_separate(client, monkeypatch):
    configure(client)
    stream = Stream([
        {'choices': [{'delta': {'reasoning_content': 'private thought'}}]},
        {'choices': [{'delta': {'content': '第一段'}}]},
        {'choices': [{'delta': {'content': '，结论'}, 'finish_reason': 'stop'}]}, '[DONE]'])
    mock_transport(monkeypatch, lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream))
    events = []
    result = asyncio.run(ai.respond('Test', [{'role': 'user', 'content': 'Q'}], on_event=events.append))
    assert result['content'] == events[-1]['content'] == '第一段，结论'
    assert events[0]['phase'] == '模型正在思考'
    assert 'private thought' not in json.dumps(events) and stream.closed


def test_cancelling_stream_closes_connection_and_keeps_partial_event(client, monkeypatch):
    configure(client)
    events = []
    class SlowStream(Stream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Partial answer"}}]}\n\n'
            await asyncio.sleep(30)
    stream = SlowStream([])
    mock_transport(monkeypatch, lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream))
    async def exercise():
        task = asyncio.create_task(ai.respond('Test', [{'role': 'user', 'content': 'Q'}], on_event=events.append))
        for _ in range(100):
            if events:
                break
            await asyncio.sleep(.001)
        assert events[-1]['content'] == 'Partial answer'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(exercise())
    assert stream.closed


def test_premature_stream_is_not_reported_as_complete(client, monkeypatch):
    configure(client)
    stream = Stream([{'choices': [{'delta': {'content': 'Partial'}}]}])
    mock_transport(monkeypatch, lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream))
    events = []
    with pytest.raises(HTTPException, match='提前断开'):
        asyncio.run(ai.respond('Test', [], on_event=events.append))
    assert events[-1]['content'] == 'Partial' and stream.closed


def test_interrupted_stream_persists_partial_answer_in_conversation(client, monkeypatch):
    configure(client)
    paper = client.post('/api/papers/metadata', json={'title': 'Test paper', 'abstract': 'Evidence in a paper.'}).json()
    assert 'paper' in paper, paper
    stream = Stream([{'choices': [{'delta': {'content': 'Saved partial answer'}}]}])
    mock_transport(monkeypatch, lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream))
    response = client.post('/api/chat/runs', json={'paper_id': paper['paper']['id'], 'question': 'Explain evidence'})
    assert response.status_code == 202, response.text
    run = response.json()
    for _ in range(100):
        result = client.get('/api/chat/runs/' + run['id']).json()
        if result['status'] not in {'running', 'stopping'}:
            break
        time.sleep(.01)
    assert result['status'] == 'failed' and result['content'] == 'Saved partial answer'
    messages = client.get('/api/conversations/' + run['conversation_id']).json()['messages']
    assert messages[-1]['content'] == 'Saved partial answer'


def test_pending_turn_uses_its_original_connection_after_settings_change(client, monkeypatch):
    configure(client)
    connection = (ai.settings(), ai.key())
    client.put('/api/settings', json={'api_preset': 'custom', 'model': 'other-model', 'base_url': 'https://other.example/v1', 'api_key': 'other-key'})
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Answer'}, 'finish_reason': 'stop'}]})
    mock_transport(monkeypatch, handler)
    asyncio.run(ai.respond('Test', [], connection=connection))
    assert requests[0].url.host == 'api.deepseek.com'
    assert requests[0].headers['authorization'] == 'Bearer test-key'


def test_custom_local_service_optional_key_and_minimal_parameters(client, monkeypatch):
    configure(client, api_preset='custom', base_url='http://127.0.0.1:11434/v1', model='local-model', api_key='', api_key_optional=True)
    assert ai.settings()['ready'] and not ai.key()
    payloads = []
    def handler(request):
        assert 'authorization' not in request.headers
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Local answer'}, 'finish_reason': 'stop'}]})
    mock_transport(monkeypatch, handler)
    asyncio.run(ai.respond('Test', [{'role': 'user', 'content': 'Q'}]))
    assert 'thinking' not in payloads[0] and 'reasoning_effort' not in payloads[0] and 'tools' not in payloads[0]
    assert client.put('/api/settings', json={'base_url': 'https://remote.example/v1', 'api_key_optional': True}).status_code == 400


def test_catalog_refresh_and_unsupported_web_search(client, monkeypatch):
    configure(client)
    mock_transport(monkeypatch, lambda request: httpx.Response(200, json={'data': [{'id': 'deepseek-v4-pro'}, {'id': 'deepseek-v4-flash-vision-exp'}, {'id': 'deepseek-v4-pro'}]}))
    models = client.get('/api/models').json()['models']
    assert len(models) == 2 and any(m['images'] for m in models)
    assert client.get('/api/settings').json()['api_models'] == models
    with pytest.raises(HTTPException, match='联网'):
        asyncio.run(ai.respond('Search', [], web=True))


@pytest.mark.parametrize('status', [400, 401, 404, 429, 500, 307])
def test_service_errors_never_disclose_raw_payload_or_follow_redirects(client, monkeypatch, status):
    configure(client)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={'error': {'message': 'sensitive body test-key'}}, headers={'location': 'https://other.example'})
    mock_transport(monkeypatch, handler)
    with pytest.raises(HTTPException) as error:
        asyncio.run(ai.respond('Test', []))
    assert 'sensitive' not in str(error.value.detail) and 'test-key' not in str(error.value.detail)
    assert len(requests) == 1
