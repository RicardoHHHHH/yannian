"""Persistent, cancellable literature searches with observable source coverage."""
import asyncio
import copy
import json
import re
import time
from fastapi import HTTPException
import httpx
from . import ai, db, paper_sites, research

TASKS = {}
RUNNING = {"queued", "running"}
PROVIDERS = ("Semantic Scholar", "arXiv", "Crossref")


def init():
    db.execute("""CREATE TABLE IF NOT EXISTS discovery_jobs (
        id TEXT PRIMARY KEY, query TEXT NOT NULL, status TEXT NOT NULL,
        payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    for row in db.rows("SELECT payload FROM discovery_jobs WHERE status IN ('running','queued')"):
        job = json.loads(row["payload"])
        job.update(status="interrupted", phase="服务重启，检索已中断；已找到的结果保留。")
        save(job)


def save(job):
    job["revision"] = job.get("revision", 0) + 1
    job["updated_at"] = db.now()
    job["elapsed_seconds"] = round(time.time() - job["started_epoch"])
    db.execute("INSERT OR REPLACE INTO discovery_jobs VALUES (?,?,?,?,?,?)",
        (job["id"], job["query"], job["status"], json.dumps(job, ensure_ascii=False), job["created_at"], job["updated_at"]))


def read(job_id, after=None):
    row = db.one("SELECT payload FROM discovery_jobs WHERE id=?", (job_id,))
    if not row: raise HTTPException(404, "检索记录不存在。")
    job = json.loads(row["payload"])
    job["elapsed_seconds"] = round(time.time() - job["started_epoch"]) if job["status"] in RUNNING else job["elapsed_seconds"]
    if after == job["revision"]:
        return {k: job[k] for k in ("id", "status", "revision", "elapsed_seconds")} | {"unchanged": True}
    return job


def start(query, depth="deep", web=True, paper_id=None):
    query = query.strip()
    if not query: raise HTTPException(400, "请输入研究问题或论文关键词。")
    for job_id, task in list(TASKS.items()):
        if task.done():
            TASKS.pop(job_id, None)
        else:
            old = read(job_id)
            if old["query"] == query and old["depth"] == depth and old["web"] == web: return old
            raise HTTPException(409, "已有检索正在进行。请先停止当前检索，再开始新的问题。")
    job = {"id": db.uid(), "query": query, "queries": [query], "depth": depth, "web": web,
           "paper_id": paper_id, "status": "queued", "phase": "准备检索", "papers": [], "warnings": [],
           "sources": {name: {"status": "pending", "count": 0, "pages": 0, "attempts": 0, "details": []} for name in PROVIDERS},
           "raw_count": 0, "revision": 0, "started_epoch": time.time(), "created_at": db.now(),
           "scope": "真实学术索引和论文官网元数据；结果仍是候选文献，不代表已阅读全文或穷尽相关工作。"}
    save(job)
    TASKS[job["id"]] = asyncio.create_task(execute(job))
    return copy.deepcopy(job)


async def cancel(job_id):
    task = TASKS.get(job_id)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    job = read(job_id)
    if job["status"] in RUNNING:
        job.update(status="cancelled", phase="检索已停止，已找到的结果保留。")
        save(job)
    return job


async def shutdown():
    for job_id in list(TASKS):
        await cancel(job_id)
    TASKS.clear()


def json_object(content):
    content = re.sub(r"^```(?:json)?\s*", "", content.strip())
    result = json.JSONDecoder().raw_decode(content[content.index("{"):])[0]
    if not isinstance(result, dict): raise ValueError("Expected JSON object")
    return result


def fallback_queries(query):
    result = [query]
    if any(word in query for word in ("医疗", "医学", "临床")):
        medical = []
        if "agent" in query.lower() or "智能体" in query: medical += ["medical agents", "clinical language model agents"]
        if "世界模型" in query or "world model" in query.lower(): medical += ["medical world models", "world models healthcare"]
        if medical: return medical
    synonyms = {"rag": "retrieval augmented generation", "检索增强": "retrieval augmented generation",
                "智能体": "large language model agents", "多模态": "multimodal reasoning",
                "扩散": "diffusion models", "推理时": "test time scaling", "强化学习": "reinforcement learning",
                "思维链": "chain of thought reasoning", "微调": "parameter efficient fine tuning"}
    for term, replacement in synonyms.items():
        if term in query.lower() and replacement.lower() not in {s.lower() for s in result}:
            result.append(replacement)
    if len(research.tokens(query)) > 5:
        broad = research.keywords(query, 4)
        if broad not in result: result.append(broad)
    return result[:4]


async def plan_queries(job):
    queries = fallback_queries(job["query"])
    try:
        result = await ai.respond("制定AI论文文献检索策略。只输出JSON：{\"queries\":[\"英文检索式\"],\"rationale\":\"中文说明\"}。"
            "给出4条互补的简短英文查询，每条2–5个核心词或带双引号的术语短语。包含规范术语、别名、"
            "相邻方法或问题表述；覆盖经典和近期工作。不要把同义词全部AND在一起，不要编造论文。"
            "如果问题包含多个研究方向，应分别检索各方向，再考虑交集，避免强制所有方向同时命中。"
            "用户提供论文标题时，第一条保留准确标题，其他查询围绕其问题与方法。",
            [{"role": "user", "content": job["query"]}], max_tokens=850)
        proposed = json_object(result["content"])
        english = [q.strip()[:240] for q in proposed.get("queries", []) if isinstance(q, str) and q.strip() and re.search(r"[a-zA-Z]", q)]
        if not english: raise ValueError("No English queries")
        seeds = [q for q in queries if not re.search(r"[\u4e00-\u9fff]", q)]
        queries = list(dict.fromkeys(seeds + english))[:6]
        job["strategy"] = str(proposed.get("rationale", ""))[:1200]
    except (HTTPException, ValueError, KeyError, TypeError):
        job["warnings"].append("模型未能扩展检索式，已使用原始关键词及可识别的术语别名；可用英文同义词再次检索。")
    return queries


def failure_reason(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return "来源限流（429）" if exc.response.status_code == 429 else f"来源返回 HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException): return "请求超时，重试后仍未完成"
    return "来源连接或数据解析失败"


async def execute(job):
    records = []
    guide = {}
    excluded = db.one("SELECT title,doi,url FROM papers WHERE id=?", (job["paper_id"],)) if job.get("paper_id") else None
    def publish():
        job["papers"] = research.merge_papers(records, job["queries"])
        if excluded:
            skip = set(research.identities(excluded))
            job["papers"] = [p for p in job["papers"] if not skip.intersection(research.identities(p))]
        for paper in job["papers"]:
            paper.update(guide.get(paper["url"], {}))
        if guide:
            job["papers"].sort(key=lambda p: (p.get("reading_priority", 1000), -p["relevance"]))
        job["raw_count"] = len(records)
        save(job)
    async def source_worker(name, fetch):
        state = job["sources"][name]
        state["status"] = "running"
        errors = 0
        size, pages = (50, 2) if job["depth"] == "deep" else (30, 1)
        for query in job["queries"]:
            for page in range(pages):
                state.update(current_query=query, current_page=page+1)
                state["attempts"] += 1
                publish()
                try:
                    batch = await fetch(query, size, offset=page*size)
                    state["pages"] += 1
                    state["count"] += len(batch)
                    state["details"].append({"query": query, "page": page+1, "count": len(batch), "status": "ok"})
                    records.extend({**p, "_query": query, "_rank": page*size+i+1} for i, p in enumerate(batch))
                    publish()
                    if len(batch) < size: break
                except Exception as exc:
                    errors += 1
                    reason = failure_reason(exc)
                    state["details"].append({"query": query, "page": page+1, "count": 0, "status": "failed", "reason": reason})
                    state["error"] = reason
                    publish()
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403, 429):
                        state["status"] = "partial" if state["count"] else "failed"
                        job["warnings"].append(f"{name}：{reason}，后续查询未完成；已有结果保留。")
                        publish()
                        return
                    break
        state["status"] = ("partial" if state["pages"] else "failed") if errors else "completed"
        if errors: job["warnings"].append(f"{name} 有 {errors} 次分页请求未完成，结果覆盖不完整。")
        publish()

    async def curate():
        job["phase"] = "比较研究问题与方法，整理优先阅读的论文；全部候选仍保留"
        publish()
        all_papers = job["papers"]
        candidates = list(all_papers[:100])
        for p in all_papers:
            if p.get("metadata_verified") and p not in candidates: candidates.append(p)
        candidates = candidates[:125]
        if not candidates: return
        payload = [{"id": i, "title": p["title"], "abstract": p.get("abstract", "")[:650], "year": p.get("year", ""),
                    "venue": p.get("venue", "")} for i, p in enumerate(candidates)]
        try:
            result = await ai.respond("对已检索的候选论文做研究问题初筛，而不是按关键词数量排序。只输出JSON："
                '{"priorities":[{"id":0,"group":"研究方向分类","reason":"中文简短相关性依据"}],"coverage":"筛选范围和限制"}。'
                "选出最值得优先阅读的15–30篇，按与用户研究问题的直接相关性排序，可以不足15篇。不要编造id或论文。"
                "用户含多个方向时覆盖各方向。医疗agent与世界模型问题应区分医疗Agent、医疗世界模型、"
                "临床模拟/相邻方法、通用基础方法；通用世界模型不能当作已在医疗验证的论文。"
                "缺少摘要时明确仅基于标题初筛。泛泛临床决策支持、卫生服务体系模型、非医疗agent不能挤占核心工作。"
                "reason仅依据给定标题/摘要，不要虚构实验结论或会议录用。",
                [{"role": "user", "content": "研究问题：" + job["query"] + "\n候选：" + json.dumps(payload, ensure_ascii=False)}], max_tokens=3500)
            result = json_object(result["content"])
            if not isinstance(result.get("priorities"), list): raise ValueError("Missing reading priorities")
            for item in result.get("priorities", [])[:30]:
                if not isinstance(item, dict): continue
                i = item.get("id")
                if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(candidates): continue
                p = candidates[i]
                if p["url"] in guide: continue
                guide[p["url"]] = {"reading_priority": len(guide)+1, "reading_group": str(item.get("group", "相关方向"))[:100],
                                   "reading_reason": str(item.get("reason", "需核对原文"))[:600]}
            job["reading_guide"] = {"reviewed_candidates": len(candidates), "prioritized": len(guide),
                                    "coverage": str(result.get("coverage", ""))[:3000]}
        except (HTTPException, ValueError, KeyError, TypeError):
            job["warnings"].append("模型阅读初筛未完成，保留全部候选并按本地综合相关性排序。")
        publish()

    async def website_worker():
        state = job["sources"]["官网补查"] = {"status": "running", "count": 0, "pages": 0, "attempts": 1, "details": []}
        publish()
        try:
            result = await ai.respond("你负责补查AI领域的原始论文页面。必须实际联网搜索，至少使用3组互补关键词，"
                "兼顾经典论文和近期工作，并针对适用领域查OpenReview、NeurIPS、PMLR、ACL Anthology或CVF等会议来源。"
                "不要只做一次宽泛搜索就结束。只输出JSON对象：{\"papers\":[{\"url\":\"原始论文的HTML落地页\"}],"
                "\"coverage\":\"用中文记录实际查过的查询、来源和仍有缺口的地方\"}。目标15–25个不同论文链接，"
                "不足时如实返回，禁止凑数。只使用 arxiv.org/abs/、openreview.net/forum、aclanthology.org、"
                "openaccess.thecvf.com、proceedings.neurips.cc、papers.nips.cc、proceedings.mlr.press 的论文页面。"
                "不要返回网站首页、搜索页、GitHub仓库或直接PDF下载地址。程序会逐一核验网页元数据。",
                [{"role": "user", "content": "研究问题：" + job["query"] + "\n检索式：" + json.dumps(job["queries"], ensure_ascii=False)}],
                web=True, max_tokens=4200)
            if result.get("provider") == "codex" and not result.get("web_searched"):
                raise ValueError("No observed web search")
            payload = json_object(result["content"])
            urls = list(dict.fromkeys(p["url"] for p in payload.get("papers", [])
                                     if isinstance(p, dict) and isinstance(p.get("url"), str) and p["url"]))[:25]
            state["coverage"] = str(payload.get("coverage", ""))[:4000]
            state["candidates"] = len(urls)
            state["status"] = "verifying"
            publish()
            semaphore = asyncio.Semaphore(3)
            async def validate(url):
                async with semaphore:
                    try: paper = await paper_sites.verify(url)
                    except (httpx.HTTPError, ValueError): paper = None
                    state["pages"] += 1
                    state["details"].append({"url": url[:1500], "status": "verified" if paper else "unverified"})
                    if paper:
                        records.append({**paper, "_query": "官网补查", "_rank": urls.index(url)+1})
                        state["count"] += 1
                    publish()
            await asyncio.gather(*(validate(url) for url in urls))
            state["status"] = "completed" if state["count"] == len(urls) and urls else "partial"
            if state["count"] < len(urls) or not urls:
                job["warnings"].append(f"官网补查发现 {len(urls)} 个链接，核验通过 {state['count']} 个；未通过的链接不混入论文结果。")
        except (HTTPException, ValueError, KeyError, TypeError) as exc:
            state.update(status="failed", error=exc.detail if isinstance(exc, HTTPException) else "未得到可核验的官网检索结果")
            job["warnings"].append("官网补查未完成；已保留各学术索引返回的文献。")
        publish()

    try:
        job.update(status="running", phase="扩展英文术语、同义词和相邻研究问题" if job["depth"] == "deep" else "检索各来源第一页")
        publish()
        if job["depth"] == "deep": job["queries"] = await plan_queries(job)
        job["phase"] = "按检索式逐页检索；各来源和官网补查结束后再完成"
        job["page_size"] = 50 if job["depth"] == "deep" else 30
        job["pages_per_query"] = 2 if job["depth"] == "deep" else 1
        publish()
        work = [source_worker("Semantic Scholar", research.semantic_scholar), source_worker("arXiv", research.arxiv), source_worker("Crossref", research.crossref)]
        if job["depth"] == "deep" and job["web"]: work.append(website_worker())
        # A hard deadline marks remaining sources as incomplete, never as successfully searched.
        async with asyncio.timeout(780):
            async with asyncio.TaskGroup() as group:
                for worker in work: group.create_task(worker)
        if job["depth"] == "deep": await curate()
        incomplete = any(s["status"] != "completed" for s in job["sources"].values())
        job.update(status="partial" if incomplete else "completed", phase="本轮检索结束，部分来源未完成；请查看覆盖记录。" if incomplete else "本轮检索完成；可继续翻页查看全部候选。")
    except asyncio.CancelledError:
        job.update(status="cancelled", phase="检索已停止，已找到的结果保留。")
        for s in job["sources"].values():
            if s["status"] in ("pending", "running", "verifying"): s["status"] = "cancelled"
    except TimeoutError:
        job.update(status="partial", phase="本轮检索达到时间上限，已找到的结果保留。")
        for s in job["sources"].values():
            if s["status"] in ("pending", "running", "verifying"): s["status"] = "timeout"
        job["warnings"].append("仍在进行的来源未完成，不能视为完整检索。")
    except Exception:
        job.update(status="failed", phase="检索发生异常，已找到的结果保留，可重新检索。")
        for s in job["sources"].values():
            if s["status"] in ("pending", "running", "verifying"): s["status"] = "failed"
    publish()
