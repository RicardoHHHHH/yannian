"""Find public full text from paper identifiers and publisher citation metadata."""
import asyncio
import ipaddress
import json
import re
import socket
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urljoin, urlparse
import httpx
from . import pdf, research


class FetchError(ValueError):
    pass


def normalized_title(value):
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", research.text_only(value)).casefold())


def same_title(a, b):
    # Title-only fallbacks must be exact apart from punctuation/spacing, not merely related.
    a, b = normalized_title(a), normalized_title(b)
    return len(a) >= 12 and a == b


async def public_url(url):
    try:
        p = urlparse(url)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
            raise FetchError("下载地址需要是公开的 HTTPS 链接")
        if p.hostname == "localhost" or p.hostname.endswith((".localhost", ".local", ".internal")):
            raise FetchError("不能下载本机或内网地址")
        addresses = await asyncio.to_thread(socket.getaddrinfo, p.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise FetchError("不能下载本机或内网地址")
    except (OSError, ValueError) as exc:
        if isinstance(exc, FetchError): raise
        raise FetchError("下载地址不可解析") from exc


def https_url(url):
    return re.sub(r"^http://", "https://", url or "", flags=re.I)


def arxiv_id(value):
    value = value or ""
    if "://" in value:
        p = urlparse(value)
        if p.hostname not in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"): return ""
        value = re.sub(r"^/(?:abs|pdf)/", "", p.path).removesuffix(".pdf")
    elif value.lower().startswith("10.48550/arxiv."):
        value = value[len("10.48550/arxiv."):]
    return value if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?", value) else ""


def known_pdf(url):
    p = urlparse(url)
    identifier = arxiv_id(url)
    if identifier: return "https://arxiv.org/pdf/" + identifier
    if p.hostname == "openreview.net" and p.path in ("/forum", "/pdf"):
        key = parse_qs(p.query).get("id", [""])[0]
        if key: return "https://openreview.net/pdf?id=" + quote(key)
    if p.hostname == "aclanthology.org" and re.fullmatch(r"/[A-Za-z0-9_.-]+/", p.path):
        return url.rstrip("/") + ".pdf"
    if p.hostname == "openaccess.thecvf.com" and "/html/" in p.path and p.path.endswith('.html'):
        return url.replace('/html/', '/papers/').removesuffix('.html') + '.pdf'
    if p.path.lower().endswith('.pdf'): return url
    return ""


class PDFLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.links = []
        self.anchors = []
        self.authors = []
        self.metadata = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("citation_title", "dc.title"): self.title = a.get("content", "")
            if name in ("citation_pdf_url", "eprints.document_url", "wkhealth_pdf_url"):
                self.links.insert(0, a.get("content", ""))
            content = research.text_only(a.get("content", "")).strip()
            if name in ("citation_author", "dc.creator") and content:
                self.authors.append(content)
            if name in ("citation_doi", "dc.identifier") and re.match(r"^(?:https?://doi.org/)?10\.\d{4,9}/", content):
                self.metadata['doi'] = re.sub(r'^https?://doi.org/', '', content)[:300]
            if name in ("citation_publication_date", "citation_date", "dc.date"):
                year = re.search(r'\b(?:19|20)\d{2}\b', content)
                if year: self.metadata['year'] = year[0]
            if name in ("citation_abstract", "dcterms.abstract"):
                self.metadata['abstract'] = content[:30000]
        elif tag == "link" and a.get("type", "").lower() == "application/pdf" and "alternate" in a.get("rel", "").lower():
            self.links.append(a.get("href", ""))
        elif tag == "a":
            try:
                if urlparse(a.get("href", "")).path.lower().endswith('.pdf') or a.get('type', '').lower() == 'application/pdf':
                    self.anchors.append(a.get('href', ''))
            except ValueError:
                pass


async def fetch_bytes(client, url, require_pdf=False, allow_pdf=False):
    """Check every redirect, cap streamed bytes, and reject login/HTML pages as PDFs."""
    for _ in range(5):
        await public_url(url)
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                target = response.headers.get("location")
                if not target: raise FetchError("来源返回空跳转")
                url = urljoin(str(response.url), target)
                continue
            if response.status_code in (401, 403): raise FetchError("来源要求登录或限制自动访问")
            if response.status_code == 429: raise FetchError("来源暂时限流")
            response.raise_for_status()
            is_pdf = "application/pdf" in response.headers.get("content-type", "").lower() or require_pdf or allow_pdf
            limit = pdf.MAX_BYTES if is_pdf else 3_000_000
            try:
                if int(response.headers.get("content-length", 0)) > limit: raise FetchError("文件超过大小限制（PDF 最多 40 MB）")
            except ValueError as exc:
                if isinstance(exc, FetchError): raise
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > limit: raise FetchError("文件超过大小限制（PDF 最多 40 MB）")
            data = bytes(body)
            if require_pdf and not data.lstrip().startswith(b"%PDF-"):
                raise FetchError("来源返回网页或验证页面，未得到 PDF")
            if allow_pdf and not data.lstrip().startswith(b"%PDF-") and len(data) > 3_000_000:
                raise FetchError("论文网页过大，请粘贴 PDF 文件的直接链接")
            return data, str(response.url)
    raise FetchError("下载跳转次数过多")


async def fetch_from_url(url):
    """Resolve only the user's supplied page/document; do not run a literature search."""
    url = https_url(url.strip())
    await public_url(url)
    async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=10), headers={"User-Agent": research.UA}, follow_redirects=False) as client:
        def result(data, final, metadata=None):
            return {'data': data, 'url': final, 'source': 'arXiv' if arxiv_id(url) else (urlparse(final).hostname or '网址导入'),
                    'metadata': metadata or {}, 'arxiv_id': arxiv_id(url)}

        direct = known_pdf(url)
        if direct and direct != url:
            try:
                data, final = await fetch_bytes(client, direct, require_pdf=True)
                return result(data, final)
            except (httpx.HTTPError, FetchError):
                pass  # The original article page can still expose an alternate PDF location.
        data, final = await fetch_bytes(client, url, allow_pdf=True)
        if data.lstrip().startswith(b'%PDF-'):
            return result(data, final)
        parser = PDFLinks()
        parser.feed(data.decode('utf-8', errors='replace'))
        links = list(dict.fromkeys(link for link in parser.links if link))
        metadata = {**parser.metadata, 'title': research.text_only(parser.title).strip()[:1000],
                    'authors': ', '.join(dict.fromkeys(parser.authors))[:3000]}
        if not links:
            links = list(dict.fromkeys(link for link in parser.anchors if link))
            if len(links) > 1:
                raise FetchError('这个页面包含多份 PDF，无法确定你要哪篇；请复制具体 PDF 的链接')
            # A standalone attachment may have a different title from its containing page.
            metadata = {}
        if not links:
            raise FetchError('这个网址未提供可识别的公开 PDF，请复制 PDF 文件的直接链接')
        last_error = None
        for link in links[:4]:
            try:
                content, pdf_url = await fetch_bytes(client, https_url(urljoin(final, link)), require_pdf=True)
                return result(content, pdf_url, metadata)
            except (httpx.HTTPError, FetchError) as exc:
                last_error = exc
        raise FetchError(str(last_error) if isinstance(last_error, FetchError) else '网页中的 PDF 暂时无法下载，请稍后重试')


async def find_pdf(paper):
    attempts, seen, landing_seen = [], set(), set()
    client_options = {"timeout": httpx.Timeout(35, connect=10), "headers": {"User-Agent": research.UA}, "follow_redirects": False}
    async with httpx.AsyncClient(**client_options) as client:
        async def attempt(url, source):
            url = https_url(url)
            if not url or url in seen or len(seen) >= 14: return None
            seen.add(url)
            try:
                data, final = await fetch_bytes(client, url, require_pdf=True)
                attempts.append({"source": source, "url": url, "status": "downloaded"})
                return {"data": data, "url": final, "source": source, "attempts": attempts}
            except (httpx.HTTPError, FetchError) as exc:
                reason = str(exc) if isinstance(exc, FetchError) else (f"来源返回 HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else "连接超时或失败")
                attempts.append({"source": source, "url": url, "status": "failed", "reason": reason})

        async def landing(url):
            url = https_url(url)
            if not url or url in landing_seen or len(landing_seen) >= 6: return None
            landing_seen.add(url)
            direct = known_pdf(url)
            if direct:
                found = await attempt(direct, urlparse(url).hostname)
                if found: return found
            try:
                data, final = await fetch_bytes(client, url)
                if data.lstrip().startswith(b"%PDF-"):
                    attempts.append({"source": urlparse(final).hostname, "url": final, "status": "downloaded"})
                    return {"data": data, "url": final, "source": urlparse(final).hostname, "attempts": attempts}
                parser = PDFLinks(); parser.feed(data.decode('utf-8', errors='replace'))
                if parser.title and not same_title(parser.title, paper['title']):
                    raise FetchError("落地页标题与所选文献不一致，已跳过")
                links = list(dict.fromkeys(parser.links))
                if not links: raise FetchError("论文页面没有提供可识别的 PDF 链接")
                # Citation/alternate metadata identifies this article; related-paper anchors are ignored.
                for link in links[:4]:
                    candidate = urljoin(final, link)
                    found = await attempt(candidate, urlparse(final).hostname)
                    if found: return found
            except (httpx.HTTPError, FetchError) as exc:
                attempts.append({"source": urlparse(url).hostname, "url": url, "status": "unavailable",
                                 "reason": str(exc) if isinstance(exc, FetchError) else "论文页面暂时无法访问"})

        identifiers = [paper.get('arxiv_id'), paper.get('doi'), paper.get('url')]
        for value in identifiers:
            identifier = arxiv_id(value)
            if identifier:
                found = await attempt('https://arxiv.org/pdf/' + identifier, 'arXiv')
                if found: return found
        links = paper.get('source_links') or []
        if isinstance(links, str): links = json.loads(links)
        pages = [paper.get('url', '')] + [s.get('url', '') for s in links if isinstance(s, dict)]
        doi = re.sub(r'^https?://(?:dx\.)?doi.org/', '', paper.get('doi', ''), flags=re.I).strip()
        if doi: pages.append('https://doi.org/' + quote(doi, safe='/'))
        for url in dict.fromkeys(pages):
            found = await landing(url)
            if found: return found

        if doi:
            try:
                data, _ = await fetch_bytes(client, 'https://api.crossref.org/works/' + quote(doi, safe=''))
                record = json.loads(data).get('message', {})
                for link in record.get('link', [])[:4]:
                    if 'pdf' in link.get('content-type', '').lower():
                        found = await attempt(link.get('URL'), 'Crossref 登记的全文')
                        if found: return found
            except (httpx.HTTPError, ValueError):
                attempts.append({"source": "Crossref", "status": "unavailable", "reason": "全文链接查询暂时不可用"})

        # Public PDF lookup needs no model/API account; never accept an approximate-title match.
        try:
            fields = 'title,openAccessPdf,externalIds'
            s2_id = ('DOI:' + doi) if doi else ''
            if s2_id:
                endpoint = 'https://api.semanticscholar.org/graph/v1/paper/' + quote(s2_id, safe='') + '?fields=' + fields
            else:
                endpoint = 'https://api.semanticscholar.org/graph/v1/paper/search?query=' + quote(paper['title']) + '&limit=5&fields=' + fields
            data, _ = await fetch_bytes(client, endpoint)
            payload = json.loads(data)
            for candidate in ([payload] if s2_id else payload.get('data', [])):
                if not same_title(candidate.get('title', ''), paper['title']): continue
                found = await attempt((candidate.get('openAccessPdf') or {}).get('url'), 'Semantic Scholar 开放全文')
                if found: return found
                identifier = (candidate.get('externalIds') or {}).get('ArXiv')
                if arxiv_id(identifier):
                    found = await attempt('https://arxiv.org/pdf/' + identifier, 'arXiv · 同标题预印本')
                    if found: return found
            attempts.append({"source": "Semantic Scholar", "status": "not_found", "reason": "未找到同标题且可下载的开放全文"})
        except (httpx.HTTPError, ValueError):
            attempts.append({"source": "Semantic Scholar", "status": "unavailable", "reason": "开放全文查询暂时不可用或限流"})
        try:
            matches = await research.arxiv('"' + paper['title'].replace('"', '') + '"', limit=5)
            for match in matches:
                if same_title(match['title'], paper['title']):
                    found = await attempt(known_pdf(match['url']), 'arXiv · 同标题预印本')
                    if found: return found
            attempts.append({"source": "arXiv", "status": "not_found", "reason": "未找到同标题且可下载的预印本"})
        except (httpx.HTTPError, ValueError):
            attempts.append({"source": "arXiv", "status": "unavailable", "reason": "同标题预印本查询暂时不可用"})
    return {"data": None, "attempts": attempts}
