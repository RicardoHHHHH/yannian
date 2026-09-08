"""Real bibliographic discovery. Failures are reported, never replaced with fake papers."""
import asyncio
import copy
import html
import math
import os
import re
import time
import threading
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import quote
import httpx

UA = "YannianWorkbench/0.6.1 (personal open-source literature reader)"
_cache = {}
_rate_lock = threading.Lock()
_next_request = {}
STOP = set("a an the of and or in on to for by with from as is are this that we our paper study research using based approach method model learning".split())


def tokens(text):
    words = [x for x in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower()) if x not in STOP]
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(segment[i:i+2] for i in range(len(segment)-1))
    return words


def similarity(a, b):
    x, y = Counter(tokens(a)), Counter(tokens(b))
    dot = sum(v * y.get(k, 0) for k, v in x.items())
    return dot / (math.sqrt(sum(v*v for v in x.values()) * sum(v*v for v in y.values())) or 1)


def keywords(text, count=8):
    return " ".join(x for x, _ in Counter(tokens(text)).most_common(count)) or text[:120]


def text_only(raw):
    return html.unescape(re.sub(r"<[^>]*>", " ", raw or "")).strip()


async def source_get(client, source, url, **kwargs):
    for attempt in range(3):
        # arXiv asks clients to leave three seconds between requests.
        interval = 3.1 if source == "arXiv" else 1.1 if source == "Semantic Scholar" else .2
        with _rate_lock:
            now = time.monotonic()
            slot = max(now, _next_request.get(source, now))
            _next_request[source] = slot + interval
        if slot > now:
            await asyncio.sleep(slot - now)
        try:
            response = await client.get(url, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                try:
                    wait = float(response.headers.get("Retry-After", 2 ** (attempt + 1)))
                except ValueError:
                    wait = 2 ** (attempt + 1)
                if wait > 30:  # Do not retry earlier than a long server-mandated cooldown.
                    response.raise_for_status()
                await asyncio.sleep(max(interval, wait))
                continue
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** (attempt + 1))


async def crossref(query, limit=10, offset=0, sort="relevance"):
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=12), headers={"User-Agent": UA}) as client:
        response = await source_get(client, "Crossref", "https://api.crossref.org/works", params={
            "query.bibliographic": query[:500], "rows": limit, "offset": offset,
            "sort": "published" if sort == "recent" else "score", "order": "desc",
            "select": "DOI,title,author,published,abstract,URL,type,container-title"})
        response.raise_for_status()
        result = []
        for p in response.json().get("message", {}).get("items", []):
            date = p.get("published", {}).get("date-parts", [[]])[0]
            result.append({"title": (p.get("title") or ["Untitled"])[0],
                           "authors": ", ".join(" ".join([a.get("given", ""), a.get("family", "")]).strip() for a in p.get("author", [])[:8]),
                           "year": str(date[0]) if date else "", "abstract": text_only(p.get("abstract")),
                           "doi": p.get("DOI", ""), "url": p.get("URL", ""), "source": "Crossref",
                           "venue": (p.get("container-title") or [""])[0]})
        return result


def arxiv_query(query):
    concepts = re.findall(r'"([^"\n]+)"|([\w-]+)', query, re.UNICODE)
    parts = [phrase or word for phrase, word in concepts if phrase or word.lower() not in STOP]
    # A long sentence is not a useful conjunction. Query expansion supplies other facets separately.
    return " AND ".join(f'(ti:"{term}" OR abs:"{term}")' for term in parts[:5]) or 'all:"' + query.replace('"', '')[:120] + '"'


async def arxiv(query, limit=8, offset=0, sort="relevance"):
    async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=12), headers={"User-Agent": UA}, follow_redirects=True) as client:
        response = await source_get(client, "arXiv", "https://export.arxiv.org/api/query", params={"search_query": arxiv_query(query),
                                   "start": offset, "max_results": limit, "sortBy": "submittedDate" if sort == "recent" else "relevance", "sortOrder": "descending"})
        response.raise_for_status()
        root = ET.fromstring(response.text)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        results = []
        for p in root.findall("a:entry", ns):
            get = lambda field: p.findtext("a:" + field, "", ns)
            url = get("id").replace("http://", "https://")
            if "api/errors" in url or get("title").strip().lower() == "error":
                raise ValueError("arXiv returned an API error feed")
            if "arxiv.org/abs/" not in url:
                continue
            results.append({"title": " ".join(get("title").split()),
                            "authors": ", ".join(a.findtext("a:name", "", ns) for a in p.findall("a:author", ns)[:8]),
                            "year": get("published")[:4], "abstract": " ".join(get("summary").split()),
                            "doi": p.findtext("{http://arxiv.org/schemas/atom}doi", ""), "arxiv_id": url.split("/abs/")[-1],
                            "url": url, "source": "arXiv", "venue": "arXiv · 预印本"})
        return results


async def semantic_scholar(query, limit=10, offset=0, sort="relevance"):
    headers = {"User-Agent": UA}
    if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=12), headers=headers) as client:
        res = await source_get(client, "Semantic Scholar", "https://api.semanticscholar.org/graph/v1/paper/search", params={
            "query": query[:500], "offset": offset, "limit": limit, "fields": "title,authors,year,abstract,url,venue,externalIds,citationCount"})
        res.raise_for_status()
        return [{"title": p["title"], "authors": ", ".join(a["name"] for a in p.get("authors", [])[:8]),
                 "year": str(p.get("year") or ""), "abstract": p.get("abstract") or "", "url": p.get("url", ""),
                 "doi": (p.get("externalIds") or {}).get("DOI", ""), "arxiv_id": (p.get("externalIds") or {}).get("ArXiv", ""),
                 "citation_count": p.get("citationCount"), "source": "Semantic Scholar", "venue": p.get("venue") or ""}
                for p in res.json().get("data", [])]


def identities(p):
    ids = []
    doi = re.sub(r"^https?://(?:dx\.)?doi.org/", "", p.get("doi", ""), flags=re.I).lower().strip()
    if doi: ids.append("doi:" + doi)
    arxiv_id = p.get("arxiv_id") or (p.get("url", "").split("/abs/")[-1] if "arxiv.org/abs/" in p.get("url", "") else "")
    if arxiv_id: ids.append("arxiv:" + re.sub(r"v\d+$", "", arxiv_id))
    title = re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", text_only(p.get("title", ""))).casefold())
    if title: ids.append("title:" + title)
    return ids


def merge_papers(records, queries):
    groups, lookup = {}, {}
    for ordinal, source in enumerate(records):
        p = copy.deepcopy(source)
        ids = identities(p)
        if not ids or not p.get("title") or not p.get("url", "").startswith(("https://", "http://")):
            continue
        hits = {lookup[i] for i in ids if i in lookup}
        key = min(hits) if hits else ordinal
        if key not in groups:
            groups[key] = {**p, "sources": [], "matched_queries": [], "source_links": [], "_ranks": {}}
        target = groups[key]
        # A bridging DOI/title record can unify groups formed from earlier incomplete metadata.
        merged = [groups.pop(other) for other in hits if other != key] + [p]
        for item in merged:
            if re.search(r"[\u4e00-\u9fff]", target["title"]) and re.search(r"[a-zA-Z]{3}", item.get("title", "")) and not re.search(r"[\u4e00-\u9fff]", item["title"]):
                target["title"] = item["title"]
            for field in ("authors", "year", "doi", "arxiv_id"):
                if not target.get(field) and item.get(field): target[field] = item[field]
            if len(item.get("abstract") or "") > len(target.get("abstract") or ""):
                target["abstract"] = item["abstract"]
            if item.get("venue") and (not target.get("venue") or target["venue"].startswith("arXiv")):
                target["venue"] = item["venue"]
            if item.get("arxiv_id"): target["arxiv_id"] = item["arxiv_id"]
            target["metadata_verified"] = bool(target.get("metadata_verified") or item.get("metadata_verified"))
            target["citation_count"] = max(target.get("citation_count") or 0, item.get("citation_count") or 0)
            for name in item.get("sources") or [item.get("source", "未知来源")]:
                if name not in target["sources"]: target["sources"].append(name)
            for q in item.get("matched_queries") or [item.get("_query", "")]:
                if q and q not in target["matched_queries"]: target["matched_queries"].append(q)
            for link in item.get("source_links") or [{"source": item.get("source", "来源"), "url": item.get("url", "")}]:
                if link.get("url") and link not in target["source_links"]: target["source_links"].append(link)
            ranks = item.get("_ranks") or {item.get("source", "") + "|" + item.get("_query", ""): item.get("_rank", ordinal + 1)}
            for name, rank in ranks.items(): target["_ranks"][name] = min(rank, target["_ranks"].get(name, rank))
        if hits:
            for identity, group in list(lookup.items()):
                if group in hits: lookup[identity] = key
        for identity in ids: lookup[identity] = key
    result = []
    for p in groups.values():
        relevant = max((similarity(q, p["title"]) + .3 * similarity(q, p.get("abstract", "")) for q in queries), default=0)
        # Domain evidence prevents broad "World Models" / "Clinical Decision Support" titles
        # from dominating a medical agents + world-model query just through short-title cosine scores.
        query_text = " ".join(queries).lower()
        corpus = (p["title"] + " " + p.get("abstract", "")).lower()
        if re.search(r"medical|clinical|healthcare|patient", query_text):
            domain = bool(re.search(r"medic|clinical|healthcare|patient|hospital|diagnos|treatment|\behr\b|medagent|medworld", corpus))
            direction = bool(re.search(r"agent|world[- ]model|model[- ]based|reinforcement|patient simulat|transition dynamic", corpus))
            if not domain: relevant *= .25
            elif re.search(r"agent|world|reinforcement", query_text) and not direction: relevant *= .4
            elif direction: relevant += .35
        rrf = sum(1 / (60 + rank) for rank in p.pop("_ranks").values())
        p["relevance"] = round(relevant + 2 * rrf, 5)
        p["source"] = " / ".join(p["sources"])
        p.pop("_rank", None); p.pop("_query", None)
        result.append(p)
    return sorted(result, key=lambda p: p["relevance"], reverse=True)


async def search(query, limit=18):
    key = (query.strip().lower(), limit)
    if key in _cache and time.time() - _cache[key][0] < 600:
        return copy.deepcopy(_cache[key][1])
    fetch_limit = min(100, max(30, limit))
    results = await asyncio.gather(semantic_scholar(query, fetch_limit), arxiv(query, fetch_limit), crossref(query, fetch_limit), return_exceptions=True)
    records, warnings = [], []
    for source, result in zip(("Semantic Scholar", "arXiv", "Crossref"), results):
        if isinstance(result, Exception):
            warnings.append(source + " 暂时不可用或请求受限；本次结果不覆盖该来源。")
            continue
        records.extend({**p, "_query": query, "_rank": i+1} for i, p in enumerate(result))
    papers = merge_papers(records, [query])
    response = {"query": query, "papers": papers[:limit], "warnings": warnings,
                "searched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                "scope": "Semantic Scholar / arXiv / Crossref 元数据；检索候选不等同于已确认相关工作。"}
    if papers and not warnings:
        if len(_cache) >= 128: _cache.pop(next(iter(_cache)))
        _cache[key] = (time.time(), copy.deepcopy(response))
    return response


def resource_links(query):
    # These are visibly labeled search entrances, never represented as verified resources.
    return [{"title": "GitHub · 开源实现", "url": "https://github.com/search?q=" + quote(query) + "&type=repositories"},
            {"title": "Hugging Face · 数据集", "url": "https://huggingface.co/datasets?search=" + quote(query)},
            {"title": "Hugging Face · 模型", "url": "https://huggingface.co/models?search=" + quote(query)},
            {"title": "Zenodo · 研究数据", "url": "https://zenodo.org/search?q=" + quote(query)}]
