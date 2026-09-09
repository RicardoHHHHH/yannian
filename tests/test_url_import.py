import asyncio
import httpx
import pytest
from app import db, pdf_sources
from app import pdf
import pymupdf as fitz
from app.main import URL_FETCHING
from test_workbench import client, pdf_bytes, project
from test_pdf_sources import transport


def test_direct_url_download_save_classify_and_duplicate(client, monkeypatch):
    data=pdf_bytes('A practical medical agent')
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=data, headers={'Content-Type':'application/pdf'})
    transport(monkeypatch,handler)
    first,second=project(client,'Agents'),project(client,'Ideas')
    result=client.post('/api/papers/from-url',json={'url':'https://papers.example/download?id=1#page=2','project_id':first['id']})
    assert result.status_code==200,result.text
    saved=result.json();pid=saved['paper']['id']
    assert saved['paper']['title']=='A practical medical agent' and not saved['duplicate']
    assert saved['project']['id']==first['id']
    detail=client.get('/api/papers/'+pid).json()
    assert detail['has_pdf'] and detail['paragraphs']
    assert detail['pdf_origin']=='https://papers.example/download?id=1'
    assert client.get('/api/papers/'+pid+'/file').content==data
    same=client.post('/api/papers/from-url',json={'url':'https://papers.example/download?id=1','project_id':second['id']}).json()
    assert same['duplicate'] and same['paper']['id']==pid and len(calls)==1
    # The same PDF under a different URL is also reused, without rewriting its title or provenance.
    mirror=client.post('/api/papers/from-url',json={'url':'https://mirror.example/paper.pdf'}).json()
    assert mirror['duplicate'] and mirror['paper']['id']==pid
    library=client.get('/api/library').json()
    assert len(library['papers'])==1
    assert set(library['papers'][0]['project_ids'])=={first['id'],second['id']}


def test_article_citation_metadata_and_relative_pdf(client,monkeypatch):
    data=pdf_bytes('PDF embedded title')
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path=='/article':
            return httpx.Response(200,text='<meta name="citation_title" content="World Models for Medicine"><meta name="citation_author" content="A. Researcher"><meta name="citation_author" content="B. Researcher"><meta name="citation_publication_date" content="2026/02/01"><meta name="citation_doi" content="10.1234/example"><meta name="citation_pdf_url" content="http://journal.example/files/actual.pdf"><a href="related.pdf">Related PDF</a>')
        assert request.url.path=='/files/actual.pdf'
        assert request.url.scheme=='https'
        return httpx.Response(200,content=data,headers={'Content-Type':'application/pdf'})
    transport(monkeypatch,handler)
    response=client.post('/api/papers/from-url',json={'url':'https://journal.example/article'})
    assert response.status_code==200,response.text
    paper=response.json()['paper']
    assert paper['title']=='World Models for Medicine' and paper['authors']=='A. Researcher, B. Researcher'
    assert paper['year']=='2026' and paper['doi']=='10.1234/example'
    assert paper['url']=='https://journal.example/article'
    assert calls==['/article','/files/actual.pdf']


def test_url_import_attaches_to_existing_metadata_preserving_idea(client,monkeypatch):
    url='https://journal.example/paper.pdf'
    original=client.post('/api/papers/metadata',json={'title':'Existing curated title','url':url}).json()['paper']
    idea=client.post('/api/ideas',json={'title':'Keep this idea','body':'An original hypothesis','paper_id':original['id']}).json()
    transport(monkeypatch,lambda request:httpx.Response(200,content=pdf_bytes(),headers={'Content-Type':'application/pdf'}))
    response=client.post('/api/papers/from-url',json={'url':url})
    assert response.status_code==200,response.text
    assert response.json()['paper']['id']==original['id']
    assert response.json()['paper']['title']=='Existing curated title'
    assert client.get('/api/ideas').json()[0]['id']==idea['id']
    assert len(client.get('/api/library').json()['papers'])==1


@pytest.mark.parametrize('url,expected',[
    ('https://arxiv.org/abs/2602.03569','https://arxiv.org/pdf/2602.03569'),
    ('https://openreview.net/forum?id=abcd','https://openreview.net/pdf?id=abcd'),
])
def test_known_article_urls_download_the_corresponding_pdf(client,monkeypatch,url,expected):
    calls=[]
    def handler(request):
        calls.append(str(request.url));return httpx.Response(200,content=pdf_bytes(),headers={'Content-Type':'application/pdf'})
    transport(monkeypatch,handler)
    response=client.post('/api/papers/from-url',json={'url':url})
    assert response.status_code==200 and calls==[expected]


def test_one_attachment_is_supported_but_multiple_pdf_links_need_an_exact_url(client,monkeypatch):
    data=pdf_bytes('The actual attached paper')
    def handler(request):
        if request.url.path=='/single':return httpx.Response(200,text='<a href="file.pdf">Download</a>')
        if request.url.path=='/many':return httpx.Response(200,text='<a href="one.pdf">One</a><a href="two.pdf">Two</a>')
        assert request.url.path=='/file.pdf'
        return httpx.Response(200,content=data,headers={'Content-Type':'application/pdf'})
    transport(monkeypatch,handler)
    ok=client.post('/api/papers/from-url',json={'url':'https://papers.example/single'})
    assert ok.status_code==200 and ok.json()['paper']['title']=='The actual attached paper'
    bad=client.post('/api/papers/from-url',json={'url':'https://papers.example/many'})
    assert bad.status_code==400 and '多份 PDF' in bad.json()['detail']
    assert len(client.get('/api/library').json()['papers'])==1


@pytest.mark.parametrize('body,headers,status',[
    (b'<html>Login required</html>',{'Content-Type':'text/html'},200),
    (b'%PDF-broken',{'Content-Type':'application/pdf'},200),
    (b'',{},403),
    (b'%PDF-oversized',{'Content-Type':'application/pdf','Content-Length':'50000000'},200),
])
def test_failed_download_creates_no_empty_record_and_is_retryable(client,monkeypatch,body,headers,status):
    transport(monkeypatch,lambda request:httpx.Response(status,content=body,headers=headers))
    url='https://papers.example/a.pdf'
    response=client.post('/api/papers/from-url',json={'url':url})
    assert response.status_code==400,response.text
    assert client.get('/api/library').json()['papers']==[]
    assert not list((db.DATA/'papers').glob('*.pdf'))
    assert url not in URL_FETCHING


@pytest.mark.parametrize('url',['file:///private.pdf','https://user:pass@example.org/a.pdf','https://[broken','https://127.0.0.1/a.pdf'])
def test_invalid_or_private_urls_are_rejected_without_fetching(client,monkeypatch,url):
    def handler(request):raise AssertionError('Invalid URL must not be fetched')
    transport(monkeypatch,handler)
    assert client.post('/api/papers/from-url',json={'url':url}).status_code==400


def test_unknown_project_is_rejected_before_downloading(client,monkeypatch):
    async def fetch(url):raise AssertionError('Project must be checked first')
    monkeypatch.setattr(pdf_sources,'fetch_from_url',fetch)
    response=client.post('/api/papers/from-url',json={'url':'https://papers.example/a.pdf','project_id':'missing'})
    assert response.status_code==404


def test_timeout_and_in_progress_do_not_leave_stuck_imports(client,monkeypatch):
    async def timeout(url):raise TimeoutError()
    monkeypatch.setattr(pdf_sources,'fetch_from_url',timeout)
    url='https://papers.example/a.pdf'
    assert client.post('/api/papers/from-url',json={'url':url}).status_code==504
    assert url not in URL_FETCHING
    URL_FETCHING.add(url)
    try:assert client.post('/api/papers/from-url',json={'url':url}).status_code==409
    finally:URL_FETCHING.discard(url)


def test_pdf_without_content_type_uses_pdf_size_limit(monkeypatch):
    # Download endpoints do not always send .pdf URLs or the application/pdf header.
    data=b'%PDF-'+b'0'*3_100_000
    transport(monkeypatch,lambda request:httpx.Response(200,content=data,headers={'Content-Type':'application/octet-stream'}))
    result=asyncio.run(pdf_sources.fetch_from_url('https://papers.example/download?id=3'))
    assert result['data']==data


def test_multiline_title_keeps_both_lines_with_slightly_different_font_sizes():
    with fitz.open() as doc:
        page=doc.new_page()
        page.insert_text((70,90),'Medical Agents: Language Models for',fontsize=14.22)
        page.insert_text((170,106),'Medical Reasoning',fontsize=14.35)
        page.insert_text((160,144),'Alice and Bob',fontsize=11)
        page.insert_text((70,220),'Abstract',fontsize=12)
        parsed=pdf.parse_pdf(doc.tobytes(),'paper.pdf')
    assert parsed['title']=='Medical Agents: Language Models for Medical Reasoning'


def test_import_reports_transfer_then_parse_and_completed_progress(client, monkeypatch):
    from app.main import PDF_IMPORT_PROGRESS, pdf_import_progress
    progress_id='test-progress-complete'
    PDF_IMPORT_PROGRESS.pop(progress_id, None)
    phases=[]
    async def fetch(url, on_progress):
        on_progress({'phase':'downloading','downloaded_bytes':2048,'total_bytes':4096,'parallel':True})
        snapshot=pdf_import_progress(progress_id)
        assert snapshot['downloaded_bytes']==2048 and snapshot['total_bytes']==4096
        assert snapshot['phase']=='downloading' and snapshot['elapsed_seconds']>=0
        return {'data':pdf_bytes(),'url':url,'source':'test'}
    original_parse=pdf.parse_pdf
    def parse(*args):
        phases.append(PDF_IMPORT_PROGRESS[progress_id]['phase'])
        return original_parse(*args)
    monkeypatch.setattr(pdf_sources,'fetch_from_url',fetch)
    monkeypatch.setattr(pdf,'parse_pdf',parse)
    response=client.post('/api/papers/from-url',json={'url':'https://papers.example/progress.pdf','progress_id':progress_id})
    assert response.status_code==200,response.text
    assert phases==['parsing']
    state=client.get('/api/pdf-imports/'+progress_id).json()
    assert state['phase']=='completed' and not any(k.startswith('_') for k in state)
    assert client.get('/api/pdf-imports/not-found').status_code==404


def test_failed_import_marks_progress_terminal(client, monkeypatch):
    from app.main import PDF_IMPORT_PROGRESS
    progress_id='test-progress-failed'
    PDF_IMPORT_PROGRESS.pop(progress_id,None)
    async def fetch(url,on_progress):raise pdf_sources.FetchError('Source unavailable')
    monkeypatch.setattr(pdf_sources,'fetch_from_url',fetch)
    response=client.post('/api/papers/from-url',json={'url':'https://papers.example/fail.pdf','progress_id':progress_id})
    assert response.status_code==400
    assert client.get('/api/pdf-imports/'+progress_id).json()['phase']=='failed'
    assert not URL_FETCHING
