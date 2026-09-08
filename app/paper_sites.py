"""Verify model-discovered paper links against primary-page citation metadata."""
from html.parser import HTMLParser
import re
from urllib.parse import urlparse
import httpx
from .research import UA, text_only

HOSTS = {"arxiv.org", "export.arxiv.org", "openreview.net", "aclanthology.org", "openaccess.thecvf.com",
         "proceedings.neurips.cc", "papers.nips.cc", "proceedings.mlr.press"}


def allowed_url(url):
    try:
        p = urlparse(url)
        return p.scheme == "https" and p.hostname in HOSTS and not p.username and not p.password and p.port in (None, 443)
    except ValueError:
        return False


class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "meta":
            attrs = dict(attrs)
            name = (attrs.get("name") or attrs.get("property") or "").lower()
            if name and attrs.get("content"):
                self.meta.setdefault(name, []).append(text_only(attrs["content"]))


def parse_metadata(html, url):
    parser = MetaParser()
    parser.feed(html)
    m = parser.meta
    first = lambda *keys: next((m[k][0] for k in keys if m.get(k)), "")
    # A generic HTML title is insufficient evidence that a model-suggested paper exists.
    title = first("citation_title", "dc.title")
    if not title or len(title) < 6:
        return None
    host = urlparse(url).hostname
    venue = first("citation_conference_title", "citation_journal_title")
    if host in ("arxiv.org", "export.arxiv.org"):
        venue = venue or "arXiv · 预印本"
    elif host == "openreview.net":
        venue = venue or "OpenReview · 录用状态待核实"
    elif host == "openaccess.thecvf.com":
        match = re.search(r"/content/([^/]+)/", url)
        venue = venue or (match[1] if match else "CVF Open Access")
    date = first("citation_publication_date", "citation_date", "dc.date", "citation_online_date")
    year = re.search(r"(?:19|20)\d{2}", date)
    return {"title": title, "authors": ", ".join(m.get("citation_author", m.get("dc.creator", []))[:12]),
            "year": year.group() if year else "", "abstract": first("description", "og:description", "dc.description")[:6000],
            "doi": first("citation_doi"), "url": url, "source": "论文官网", "venue": venue,
            "metadata_verified": True, "verification": "论文官网 citation 元数据", "arxiv_id": first("citation_arxiv_id")}


async def verify(url):
    if not allowed_url(url):
        return None
    if urlparse(url).hostname in ("arxiv.org", "export.arxiv.org"):
        url = re.sub(r"/pdf/([^?#]+?)(?:\.pdf)?$", r"/abs/\1", url)
    async with httpx.AsyncClient(timeout=httpx.Timeout(18, connect=8), headers={"User-Agent": UA}) as client:
        for _ in range(4):
            if not allowed_url(url): return None
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers.get("location", "")))
                    continue
                response.raise_for_status()
                if "html" not in response.headers.get("content-type", "").lower(): return None
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 2_000_000: return None
                return parse_metadata(body.decode("utf-8", errors="replace"), str(response.url))
    return None
