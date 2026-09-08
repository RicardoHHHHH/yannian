import asyncio
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import pytest
import httpx
from app import ai, db, discovery, research, paper_sites


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "data")
    monkeypatch.setattr(discovery, "TASKS", {})
    db.init(); discovery.init()


def papers(query, source, offset, count):
    return [{"title": f"Medical agent {source} {query} {i}", "url": f"https://example.org/{source}/{query}/{i}",
             "source": source, "abstract": "Medical agent evaluation"} for i in range(offset, offset+count)]


def test_deep_search_translates_pages_and_waits_for_slow_source(database, monkeypatch):
    async def scenario():
        slow_started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def plan(*args, **kwargs):
            return {"content": json.dumps({"queries": ["medical agents", "medical world models"], "rationale": "Separate topics"})}
        async def fast(query, limit, offset=0):
            calls.append((query, offset))
            return papers(query, "arXiv", offset, 50 if offset == 0 else 7)
        async def slow(query, limit, offset=0):
            slow_started.set(); await release.wait()
            return papers(query, "Semantic Scholar", 0, 3)
        async def empty(*args, **kwargs): return []
        monkeypatch.setattr(ai, "respond", plan)
        monkeypatch.setattr(research, "arxiv", fast)
        monkeypatch.setattr(research, "semantic_scholar", slow)
        monkeypatch.setattr(research, "crossref", empty)
        job = discovery.start("一个测试研究方向", web=False)
        await slow_started.wait()
        partial = discovery.read(job["id"])
        assert partial["status"] == "running" and len(partial["papers"]) > 18
        assert partial["queries"] == ["medical agents", "medical world models"]
        assert discovery.start(job["query"], web=False)["id"] == job["id"]
        release.set(); await discovery.TASKS[job["id"]]
        result = discovery.read(job["id"])
        assert result["status"] == "completed" and len(result["papers"]) == 120
        assert ("medical agents", 50) in calls and ("medical world models", 50) in calls
        assert result["sources"]["arXiv"]["pages"] == 4
        assert discovery.read(job["id"], result["revision"])["unchanged"]
    asyncio.run(scenario())


def test_cancel_preserves_partial_results_and_restart_marks_interrupted(database, monkeypatch):
    async def scenario():
        started = asyncio.Event()
        async def fast(query, limit, **kwargs): return papers(query, "arXiv", 0, 5)
        async def slow(*args, **kwargs): started.set(); await asyncio.Event().wait()
        monkeypatch.setattr(research, "arxiv", fast)
        monkeypatch.setattr(research, "semantic_scholar", slow)
        monkeypatch.setattr(research, "crossref", fast)
        job = discovery.start("medical agents", depth="quick", web=False)
        await started.wait()
        result = await discovery.cancel(job["id"])
        assert result["status"] == "cancelled" and result["papers"]
        result["status"] = "running"; discovery.save(result)
        discovery.init()
        assert discovery.read(job["id"])["status"] == "interrupted"
        assert discovery.read(job["id"])["papers"]
    asyncio.run(scenario())


def test_limited_source_does_not_look_complete_or_discard_other_sources(database, monkeypatch):
    async def scenario():
        async def rate_limited(*args, **kwargs):
            r = httpx.Response(429, request=httpx.Request("GET", "https://example.org"))
            r.raise_for_status()
        async def ok(query, limit, **kwargs): return papers(query, "Crossref", 0, 22)
        monkeypatch.setattr(research, "semantic_scholar", rate_limited)
        monkeypatch.setattr(research, "arxiv", ok)
        monkeypatch.setattr(research, "crossref", ok)
        job = discovery.start("clinical agents", depth="quick", web=False)
        await discovery.TASKS[job["id"]]
        result = discovery.read(job["id"])
        assert result["status"] == "partial" and len(result["papers"]) == 22
        assert result["sources"]["Semantic Scholar"]["status"] == "failed"
        assert "429" in result["warnings"][0]
    asyncio.run(scenario())


def test_dedup_unifies_doi_arxiv_versions_and_keeps_english_metadata():
    records = [
        {"title": "医疗智能体", "doi": "10.1/a", "url": "https://doi.org/10.1/a", "source": "Crossref", "venue": "ICLR"},
        {"title": "Medical Agents", "arxiv_id": "2401.12345v1", "url": "https://arxiv.org/abs/2401.12345v1", "source": "arXiv", "abstract": "Longer original abstract"},
        {"title": "Medical Agents!", "doi": "https://doi.org/10.1/A", "arxiv_id": "2401.12345v2", "url": "https://example.org/a", "source": "Semantic Scholar"},
    ]
    merged = research.merge_papers(records, ["medical agents"])
    assert len(merged) == 1
    assert merged[0]["title"] == "Medical Agents" and merged[0]["venue"] == "ICLR"
    assert len(merged[0]["sources"]) == 3 and merged[0]["abstract"] == "Longer original abstract"


def test_relevance_does_not_privilege_provider_iteration_order():
    records = [{"title": "Unrelated chemistry", "url": "https://example.org/a", "source": "Semantic Scholar", "_rank": 1},
               {"title": "Clinical world models", "url": "https://example.org/b", "source": "Crossref", "_rank": 20}]
    assert research.merge_papers(records, ["clinical world models"])[0]["title"] == "Clinical world models"


def test_medical_query_prioritizes_domain_evidence_over_generic_world_models():
    records = [
        {"title": "World Models", "abstract": "Visual control in games", "url": "https://example.org/games", "source": "arXiv", "_rank": 1},
        {"title": "Clinical Decision Support", "url": "https://example.org/general", "source": "Crossref", "_rank": 1},
        {"title": "EHRWorld: A World Model for Clinical Decision Making", "abstract": "A medical world model of patient state transitions", "url": "https://example.org/medical", "source": "arXiv", "_rank": 25},
    ]
    assert research.merge_papers(records, ["medical agents", "world models healthcare"])[0]["url"].endswith('/medical')


def test_reading_guide_reorders_only_existing_ids_and_preserves_all_candidates(database, monkeypatch):
    async def scenario():
        async def answer(system, *args, **kwargs):
            if 'priorities' in system:
                return {"content": json.dumps({"priorities": [None, {"id": True}, {"id": 1000}, {"id": 27, "group": "医疗世界模型", "reason": "Based on abstract"}, {"id": 27}], "coverage": "Only titles and abstracts"})}
            return {"content": '{"queries":["clinical research"]}'}
        async def results(query, limit, **kwargs): return papers(query, "arXiv", 0, 31)
        async def empty(*args, **kwargs): return []
        monkeypatch.setattr(ai, "respond", answer)
        monkeypatch.setattr(research, "arxiv", results)
        monkeypatch.setattr(research, "semantic_scholar", empty)
        monkeypatch.setattr(research, "crossref", empty)
        job = discovery.start("clinical research", web=False)
        await discovery.TASKS[job["id"]]
        result = discovery.read(job["id"])
        assert result["status"] == "completed" and len(result["papers"]) == 31
        assert result["papers"][0]["url"].endswith('/27') and result["papers"][0]["reading_priority"] == 1
        assert result["papers"][0]["reading_group"] == "医疗世界模型"
        assert result["reading_guide"]["prioritized"] == 1 and result["reading_guide"]["reviewed_candidates"] == 31
        assert sum('reading_priority' in p for p in result["papers"]) == 1
    asyncio.run(scenario())


def test_medical_fallback_searches_both_english_topics_and_short_arxiv_concepts():
    queries = discovery.fallback_queries("医疗领域agent和世界模型")
    assert "medical agents" in queries and "medical world models" in queries
    assert all(not any("\u4e00" <= c <= "\u9fff" for c in q) for q in queries)
    query = research.arxiv_query('"world model" clinical decision making many unnecessary additional words')
    assert 'ti:"world model"' in query and query.count(" AND ") == 4


def test_primary_page_metadata_is_required_and_unsafe_urls_rejected():
    assert paper_sites.parse_metadata('<title>An invented paper</title>', 'https://arxiv.org/abs/2401.00001') is None
    meta = '<meta name="citation_title" content="Medical World Models"><meta name="citation_author" content="A. Author"><meta name="citation_date" content="2025/01/01">'
    p = paper_sites.parse_metadata(meta, 'https://arxiv.org/abs/2401.00001')
    assert p["title"] == "Medical World Models" and p["year"] == "2025" and p["metadata_verified"]
    for url in ('http://arxiv.org/abs/1', 'https://arxiv.org.evil.test/p', 'https://127.0.0.1/private', 'file:///private', 'https://user:password@arxiv.org/abs/1'):
        assert not paper_sites.allowed_url(url)


def test_transient_limit_retried_and_long_retry_after_respected(monkeypatch):
    async def scenario():
        attempts, waits = [], []
        async def sleep(n): waits.append(n)
        def transport(request):
            attempts.append(request)
            return httpx.Response(429 if len(attempts) < 3 else 200, json={}, headers={"Retry-After": "2"})
        monkeypatch.setattr(research.asyncio, "sleep", sleep)
        monkeypatch.setattr(research, "_next_request", {})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as c:
            r = await research.source_get(c, "Crossref", "https://example.org")
            assert r.status_code == 200 and len(attempts) == 3 and waits.count(2) == 2
        attempts.clear()
        def long_wait(request):
            attempts.append(request)
            return httpx.Response(429, headers={"Retry-After": "120"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(long_wait)) as c:
            with pytest.raises(httpx.HTTPStatusError): await research.source_get(c, "Crossref", "https://example.org")
        assert len(attempts) == 1
    asyncio.run(scenario())


def test_website_candidates_are_not_imported_without_metadata_verification(database, monkeypatch):
    async def scenario():
        async def answer(*args, **kwargs):
            if kwargs.get("web"):
                return {"provider": "codex", "web_searched": True, "content": '{"papers":[{"url":"https://arxiv.org/abs/1"},{"url":"https://arxiv.org/abs/2"}]}'}
            return {"content": '{"queries":["medical agents"]}'}
        async def no_results(*args, **kwargs): return []
        async def verify(url):
            return {"title": "Verified medical paper", "url": url, "source": "论文官网"} if url.endswith('/1') else None
        monkeypatch.setattr(ai, "respond", answer)
        monkeypatch.setattr(paper_sites, "verify", verify)
        for name in ('arxiv', 'crossref', 'semantic_scholar'): monkeypatch.setattr(research, name, no_results)
        job = discovery.start("medical agents")
        await discovery.TASKS[job["id"]]
        result = discovery.read(job["id"])
        assert len(result["papers"]) == 1 and result["sources"]["官网补查"]["pages"] == 2
        assert result["status"] == "partial"
    asyncio.run(scenario())


def launcher_module():
    path = Path(__file__).resolve().parent.parent / "launch.pyw"
    loader = importlib.machinery.SourceFileLoader("yannian_launcher_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    return module


def test_launcher_reuses_running_service_without_spawning(monkeypatch):
    launcher = launcher_module()
    monkeypatch.setattr(launcher, "service_state", lambda url: "ready")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Unexpected second server"))
    assert launcher.ensure_server(8765) == "http://127.0.0.1:8765"


def test_launcher_does_not_replace_another_service(monkeypatch):
    launcher = launcher_module()
    monkeypatch.setattr(launcher, "service_state", lambda url: "occupied")
    with pytest.raises(RuntimeError): launcher.ensure_server(8765)


@pytest.mark.parametrize('identity,expected', [('yannian-workbench', 'ready'), ('yanji-workbench', 'ready'), ('unrelated', 'occupied')])
def test_launcher_recognizes_both_names_during_upgrade(monkeypatch, identity, expected):
    import io
    from types import SimpleNamespace
    launcher = launcher_module()
    opener = SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(json.dumps({'app': identity}).encode()))
    monkeypatch.setattr(launcher.urllib.request, 'build_opener', lambda *args: opener)
    assert launcher.service_state('http://127.0.0.1:8765') == expected
