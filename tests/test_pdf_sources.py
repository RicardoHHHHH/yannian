import asyncio
import json
import socket
import httpx
import pytest
from app import pdf_sources, research
from test_workbench import client, pdf_bytes, project


@pytest.mark.parametrize('page,expected', [
    ('https://arxiv.org/abs/2602.03569v1', 'https://arxiv.org/pdf/2602.03569v1'),
    ('https://aclanthology.org/2024.findings-acl.33/', 'https://aclanthology.org/2024.findings-acl.33.pdf'),
    ('https://openreview.net/forum?id=test123', 'https://openreview.net/pdf?id=test123'),
    ('https://openaccess.thecvf.com/content/CVPR2025/html/Example.html', 'https://openaccess.thecvf.com/content/CVPR2025/papers/Example.pdf'),
])
def test_primary_pdf_urls(page, expected):
    assert pdf_sources.known_pdf(page) == expected
    assert not pdf_sources.arxiv_id('https://arxiv.org.evil.test/abs/2602.03569')


def transport(monkeypatch, handler):
    constructor = httpx.AsyncClient
    monkeypatch.setattr(pdf_sources.httpx, 'AsyncClient', lambda **kw: constructor(transport=httpx.MockTransport(handler), **kw))
    async def public(url):
        if '127.0.0.1' in url or url.startswith('file:'): raise pdf_sources.FetchError('private address')
    monkeypatch.setattr(pdf_sources, 'public_url', public)


def test_landing_page_uses_article_citation_pdf_not_related_paper(monkeypatch):
    data = pdf_bytes('Relevant Medical Agents')
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path == '/paper':
            return httpx.Response(200, text='<meta name="citation_title" content="Relevant Medical Agents"><a href="/other.pdf">Related work PDF</a><meta name="citation_pdf_url" content="files/full.pdf">')
        assert request.url.path == '/files/full.pdf'
        return httpx.Response(200, content=data, headers={'Content-Type':'application/pdf'})
    transport(monkeypatch, handler)
    result = asyncio.run(pdf_sources.find_pdf({'title':'Relevant Medical Agents', 'url':'https://journal.example/paper'}))
    assert result['data'] == data and result['url'].endswith('/files/full.pdf')
    assert not any('other.pdf' in c for c in calls)


def test_doi_page_failure_continues_to_registered_fulltext(monkeypatch):
    data = pdf_bytes()
    def handler(request):
        if request.url.host == 'doi.org': return httpx.Response(302, headers={'Location':'https://journal.example/article'})
        if request.url.host == 'journal.example': return httpx.Response(403)
        if request.url.host == 'api.crossref.org':
            return httpx.Response(200, json={'message':{'link':[{'URL':'https://archive.example/full.pdf','content-type':'application/pdf'}]}})
        assert request.url.host == 'archive.example'
        return httpx.Response(200, content=data, headers={'Content-Type':'application/pdf'})
    transport(monkeypatch, handler)
    result = asyncio.run(pdf_sources.find_pdf({'title':'Evidence grounded learning','doi':'10.123/example'}))
    assert result['data'] == data and result['source'] == 'Crossref 登记的全文'
    assert any(a['status'] == 'unavailable' for a in result['attempts'])


def test_title_only_fallback_does_not_download_a_similar_paper(monkeypatch):
    def handler(request):
        assert request.url.host == 'api.semanticscholar.org'
        return httpx.Response(200, json={'data':[{'title':'Medical World Models: A Different Study','openAccessPdf':{'url':'https://wrong.example/a.pdf'}}]})
    transport(monkeypatch, handler)
    async def similar(*args, **kwargs):
        return [{'title':'Medical World Models: A Different Study','url':'https://arxiv.org/abs/2602.03569'}]
    monkeypatch.setattr(research, 'arxiv', similar)
    result = asyncio.run(pdf_sources.find_pdf({'title':'Medical World Models: The Actual Study'}))
    assert result['data'] is None


def test_redirect_to_private_address_is_rejected_before_fetch(monkeypatch):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'Location':'https://127.0.0.1/private.pdf'})
    transport(monkeypatch, handler)
    async def scenario():
        async with pdf_sources.httpx.AsyncClient() as c:
            with pytest.raises(pdf_sources.FetchError): await pdf_sources.fetch_bytes(c, 'https://journal.example/a.pdf', True)
    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize('body,headers', [
    (b'<html>Sign in to download</html>', {'Content-Type':'text/html'}),
    (b'%PDF-data', {'Content-Length':str(50_000_000), 'Content-Type':'application/pdf'}),
])
def test_html_and_oversized_responses_are_not_saved_as_pdf(monkeypatch, body, headers):
    transport(monkeypatch, lambda r: httpx.Response(200, content=body, headers=headers))
    async def scenario():
        async with pdf_sources.httpx.AsyncClient() as c:
            with pytest.raises(pdf_sources.FetchError): await pdf_sources.fetch_bytes(c, 'https://journal.example/a.pdf', True)
    asyncio.run(scenario())


def test_dns_private_and_non_https_addresses_rejected(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET,socket.SOCK_STREAM,6,'',('192.168.1.3',443))])
    async def scenario():
        for url in ('https://journal.example/a', 'https://localhost/a', 'http://public.example/a', 'https://user:pass@public.example/a', 'https://public.example:8443/a'):
            with pytest.raises(pdf_sources.FetchError): await pdf_sources.public_url(url)
    asyncio.run(scenario())


def test_auto_attach_preserves_paper_idea_and_project_and_records_source(client, monkeypatch):
    group = project(client)
    p = client.post('/api/papers/metadata', json={'title':'Metadata title','arxiv_id':'2602.03569','source_links':[{'source':'PMLR','url':'https://proceedings.mlr.press/v97/hafner19a.html'}],'project_id':group['id']}).json()['paper']
    idea = client.post('/api/ideas', json={'title':'Original idea','body':'Keep me','paper_id':p['id']}).json()
    count = []
    async def found(paper):
        count.append(paper)
        assert paper['arxiv_id'] == '2602.03569' and json.loads(paper['source_links'])[0]['source'] == 'PMLR'
        return {'data':pdf_bytes(), 'url':'https://arxiv.org/pdf/2602.03569','source':'arXiv','attempts':[]}
    monkeypatch.setattr(pdf_sources, 'find_pdf', found)
    result = client.post('/api/papers/'+p['id']+'/fetch-pdf')
    assert result.status_code == 200 and result.json()['status'] == 'ready'
    detail = client.get('/api/papers/'+p['id']).json()
    assert detail['title'] == 'Metadata title' and detail['has_pdf'] and detail['paragraphs']
    assert detail['pdf_origin'] == 'https://arxiv.org/pdf/2602.03569'
    assert client.get('/api/ideas').json()[0]['id'] == idea['id']
    assert group['id'] in client.get('/api/library').json()['papers'][0]['project_ids']
    assert client.post('/api/papers/'+p['id']+'/fetch-pdf').json()['status'] == 'ready' and len(count) == 1


def test_auto_attach_failure_keeps_metadata_and_invalid_pdf_unattached(client, monkeypatch):
    p = client.post('/api/papers/metadata', json={'title':'Keep this metadata'}).json()['paper']
    async def missing(paper): return {'data':None, 'attempts':[{'source':'test','reason':'not open'}]}
    monkeypatch.setattr(pdf_sources, 'find_pdf', missing)
    response = client.post('/api/papers/'+p['id']+'/fetch-pdf')
    assert response.json()['status'] == 'not_found' and response.json()['attempts']
    async def invalid(paper): return {'data':b'%PDF-corrupt','url':'https://example.org/a.pdf','source':'test','attempts':[]}
    monkeypatch.setattr(pdf_sources, 'find_pdf', invalid)
    assert client.post('/api/papers/'+p['id']+'/fetch-pdf').status_code == 400
    detail = client.get('/api/papers/'+p['id']).json()
    assert not detail['has_pdf'] and not detail['paragraphs']


def test_large_stable_pdf_downloads_four_ranges_concurrently(monkeypatch):
    data = b'%PDF-' + bytes(range(256)) * 16000
    etag = '"stable-pdf"'
    events, ranges = [], []
    ready = asyncio.Event()
    async def handler(request):
        value = request.headers.get('range')
        if not value:
            return httpx.Response(200, content=data, headers={'Content-Type':'application/pdf','Accept-Ranges':'bytes','ETag':etag})
        assert request.headers['if-range'] == etag
        assert request.headers['accept-encoding'] == 'identity'
        start, end = map(int, value.removeprefix('bytes=').split('-'))
        ranges.append((start, end))
        if len(ranges) == 4: ready.set()
        await asyncio.wait_for(ready.wait(), 1)
        return httpx.Response(206, content=data[start:end+1], headers={'Content-Range':f'bytes {start}-{end}/{len(data)}','ETag':etag})
    transport(monkeypatch, handler)
    result = asyncio.run(pdf_sources.fetch_from_url('https://papers.example/big.pdf', on_progress=events.append))
    assert result['data'] == data and len(ranges) == 4
    assert sorted(ranges)[0][0] == 0 and sorted(ranges)[-1][1] == len(data)-1
    assert events[-1]['downloaded_bytes'] == len(data)
    assert events[-1]['parallel'] is True


@pytest.mark.parametrize('failure', ['ignored', 'changed-etag', 'wrong-range', 'truncated'])
def test_unreliable_ranges_fall_back_without_saving_mixed_or_truncated_bytes(monkeypatch, failure):
    data = b'%PDF-' + b'a' * 4_000_000
    calls, events = [], []
    def handler(request):
        value = request.headers.get('range')
        calls.append(value)
        if not value or failure == 'ignored':
            return httpx.Response(200, content=data, headers={'Content-Type':'application/pdf','Accept-Ranges':'bytes','ETag':'"original"'})
        start, end = map(int, value.removeprefix('bytes=').split('-'))
        return httpx.Response(206, content=data[start:end+1-(1 if failure=='truncated' else 0)],
                              headers={'Content-Range':f'bytes {start}-{end}/{len(data)+(1 if failure=="wrong-range" else 0)}',
                                       'ETag':'"new"' if failure=='changed-etag' else '"original"'})
    transport(monkeypatch, handler)
    result = asyncio.run(pdf_sources.fetch_from_url('https://papers.example/big.pdf', on_progress=events.append))
    assert result['data'] == data
    assert calls.count(None) == 2
    assert any(event['phase'] == 'retrying' for event in events)
    assert events[-1]['parallel'] is False


def test_weak_etag_uses_one_stream_even_when_ranges_are_advertised(monkeypatch):
    data = b'%PDF-' + b'a' * 4_000_000
    calls = []
    def handler(request):
        calls.append(request)
        assert 'range' not in request.headers
        return httpx.Response(200, content=data, headers={'Content-Type':'application/pdf','Accept-Ranges':'bytes','ETag':'W/"weak"'})
    transport(monkeypatch, handler)
    assert asyncio.run(pdf_sources.fetch_from_url('https://papers.example/big.pdf'))['data'] == data
    assert len(calls) == 1
