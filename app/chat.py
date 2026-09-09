"""Durable, cancellable reading turns. Only public answer text is streamed/stored."""
import asyncio
import base64
import json
import re
import threading
import time

from fastapi import HTTPException
from . import ai, db, selections

ACTIVE = {"running", "stopping"}
HISTORY_CHARS = 80000
_tasks = {}
_live = {}


def init():
    db.execute("""CREATE TABLE IF NOT EXISTS chat_runs (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        status TEXT NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    for row in db.rows("SELECT * FROM chat_runs WHERE status IN ('running','stopping')"):
        snapshot = json.loads(row["snapshot"])
        finish(snapshot, "interrupted", "服务已重启，本次回答未完成；可以继续提问。")


def get(run_id):
    if run_id in _live:
        return dict(_live[run_id])
    row = db.one("SELECT snapshot FROM chat_runs WHERE id=?", (run_id,))
    if not row:
        raise HTTPException(404, "这次回答不存在。")
    return json.loads(row["snapshot"])


def conversation(conversation_id):
    record = db.one("SELECT * FROM conversations WHERE id=?", (conversation_id,))
    if not record:
        raise HTTPException(404, "对话不存在。")
    messages = db.rows("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,rowid", (conversation_id,))
    runs = {r["message_id"]: get(r["id"]) for r in db.rows("SELECT id,message_id FROM chat_runs WHERE conversation_id=?", (conversation_id,))}
    for message in messages:
        message["citations"] = json.loads(message["citations"])
        if message["role"] == "user":
            present_user_message(message, record)
        if message["id"] in runs:
            run = runs[message["id"]]
            message.update({k: run.get(k) for k in ("content", "citations", "status", "model", "effort", "provider", "context_info", "error")})
            message["run_id"] = run["id"]
    return {"conversation": record, "messages": messages}


def present_user_message(message, record):
    """Separate visible questions/attachments from legacy extracted-text prompt suffixes."""
    message["display_content"] = message["content"]
    message["attachments"] = []
    ids = [s["selection_id"] for s in message["citations"] if s.get("type") == "selection" and s.get("selection_id")]
    if not ids and record.get("selection_id") and "\nPDF 选区 [S1]：" in message["content"]:
        ids = [record["selection_id"]]  # Pre-0.7 user messages had no selection citations.
    for selection_id in dict.fromkeys(ids):
        try:
            selection = selections.get(selection_id)
        except HTTPException:
            continue
        if selection["paper_id"] != record["paper_id"]:
            continue
        attachment = {k: selection[k] for k in ("kind", "page", "text", "image_url")}
        attachment["selection_id"] = selection_id
        message["attachments"].append(attachment)
        suffix = ("\n我选中的文字：\n" + selection["text"] if selection["text"] else "")
        suffix += f"\nPDF 选区 [S1]：第 {selection['page']} 页，类型 {selection['kind']}。"
        start = message["display_content"].rfind(suffix)
        if start >= 0:
            tail = message["display_content"][start + len(suffix):]
            if re.fullmatch(r"(?:只把选区作为当前关注范围，附近文字仅用于理解上下文。)?(?:\n\[本轮附有 PDF 第 \d+ 页图像\])?", tail):
                message["display_content"] = message["display_content"][:start]


def history(conversation_id):
    rows = conversation(conversation_id)["messages"] if conversation_id else []
    included, size = [], 0
    for message in reversed(rows):
        text = message.get("display_content", message["content"]) or ""
        for attachment in message.get("attachments", []):
            label = "历史图片的辅助提取文字（可能乱序）" if attachment["kind"] == "region" else "历史选中文字"
            text += f"\n{label} · 第 {attachment['page']} 页：\n{attachment['text']}"
        if message.get("status") in {"stopped", "failed", "interrupted"}:
            text += "\n[这条回答未完成，不代表最终结论。]"
        # Resolve old labels to stable page references instead of reusing the current P mapping.
        for source in message.get("citations", []):
            if source.get("type") == "paragraph" and source.get("label"):
                text = text.replace("[" + source["label"] + "]", f"[历史原文：第 {source['page']} 页]")
        if size + len(text) > HISTORY_CHARS:
            break
        included.append({"role": message["role"], "content": text})
        size += len(text)
    # Do not begin with an orphaned assistant reply when the preceding question was omitted.
    included.reverse()
    if included and included[0]["role"] == "assistant":
        included.pop(0)
    return included, {"total_messages": len(rows), "included_messages": len(included),
                      "omitted_messages": len(rows) - len(included), "history_char_limit": HISTORY_CHARS,
                      "historical_images": "历史图片保留来源和文字记录；要重新分析像素内容，请再次框选。"}


def options(body):
    config = ai.settings()
    model = body.model if body.model is not None else config["codex_model" if config["provider"] == "codex" else "model"]
    effort = body.effort if body.effort is not None else (config["codex_effort"] if config["provider"] == "codex" else None)
    if config["provider"] == "codex":
        models = config.get("codex", {}).get("models", [])
        selected = next((m for m in models if m["id"] == model), None) if model else next((m for m in models if m.get("is_default")), None)
        if models and model and selected is None:
            raise HTTPException(400, "所选模型不在当前 Codex 可用列表中，请刷新模型列表。")
        if selected and selected.get("efforts") and effort not in selected["efforts"]:
            if body.effort is not None:
                raise HTTPException(400, "该模型不支持所选思考深度，请切换档位。")
            effort = selected.get("default_effort") or selected["efforts"][0]
        if selected and "image" not in selected.get("input_modalities", ["text", "image"]) and (body.include_page or body.selection_id):
            selection = selections.get(body.selection_id) if body.selection_id else None
            if body.include_page or (selection and selection["kind"] == "region"):
                raise HTTPException(400, "所选模型不支持图像，请切换模型后分析 PDF 选区。")
    else:
        capabilities = ai.api_providers.capabilities(config, model)
        if effort and effort not in capabilities["efforts"]:
            raise HTTPException(400, "当前模型或接口不支持此思考档位。")
        selection = selections.get(body.selection_id) if body.selection_id else None
        if not capabilities["images"] and (body.include_page or (selection and selection["kind"] == "region")):
            raise HTTPException(400, "所选模型不支持图像，请切换视觉模型，或改为选中文字提问。")
    return config["provider"], model, effort


async def start(body):
    # Single local model turn at a time, with persistent IDs to survive page reloads.
    if _tasks:
        raise HTTPException(409, "已有回答正在生成，请先停止或等待完成。")
    if not body.question.strip():
        raise HTTPException(400, "请输入问题。")
    selection = selections.get(body.selection_id) if body.selection_id else None
    if selection and (selection["paper_id"] != body.paper_id or selection["paragraph_id"] != body.paragraph_id):
        raise HTTPException(400, "选区与论文或段落不匹配，请重新选择。")
    if body.conversation_id and conversation(body.conversation_id)["conversation"]["paper_id"] != body.paper_id:
        raise HTTPException(400, "该对话属于另一篇论文，请新建对话。")
    selected_text = selection["text"] if selection else body.selected_text
    paper, context, sources = ai.paper_context(body.paper_id, body.paragraph_id, body.question + " " + selected_text)
    provider, model, effort = options(body)
    previous, context_info = history(body.conversation_id)
    region = bool(selection and selection["kind"] == "region")
    context_info.update(sources=sources, focus=("PDF 第 " + str(selection["page"]) + " 页图片区域" if region else selected_text or paper["title"]))
    label = "图片的辅助提取文字（可能乱序，以所附图像为准）" if region else "我选中的文字"
    question = body.question + ("\n" + label + "：\n" + selected_text if selected_text else "")
    user_sources = []
    if selection:
        question += f"\nPDF 选区 [S1]：第 {selection['page']} 页，类型 {selection['kind']}。"
        user_sources.append({"type": "selection", "label": "S1", "selection_id": selection["id"], "paper_id": body.paper_id, "page": selection["page"]})
    if body.include_page:
        question += f"\n[本轮附有 PDF 第 {body.page} 页图像]"
    conversation_id, run_id, message_id = body.conversation_id or db.uid(), db.uid(), db.uid()
    snapshot = {"id": run_id, "conversation_id": conversation_id, "paper_id": body.paper_id,
                "message_id": message_id, "status": "running", "phase": "正在读取论文上下文", "content": "", "citations": [],
                "model": model or "Codex 默认模型", "effort": effort, "provider": provider, "error": "",
                "context_info": context_info, "created_at": db.now()}
    with db.connect() as c:
        if not body.conversation_id:
            c.execute("INSERT INTO conversations(id,paper_id,paragraph_id,title,created_at,selection_id) VALUES (?,?,?,?,?,?)",
                      (conversation_id, body.paper_id, body.paragraph_id, body.question[:70], db.now(), body.selection_id))
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (db.uid(), conversation_id, "user", body.question if selection else question, json.dumps(user_sources, ensure_ascii=False), db.now()))
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (message_id, conversation_id, "assistant", "", "[]", db.now()))
        c.execute("INSERT INTO chat_runs VALUES (?,?,?,?,?,?,?)", (run_id, conversation_id, message_id, "running", json.dumps(snapshot, ensure_ascii=False), db.now(), db.now()))
    _live[run_id] = snapshot
    connection = (ai.settings(), ai.key())  # Private, in-memory snapshot; never persisted with the run.
    _tasks[run_id] = asyncio.create_task(execute(snapshot, body, paper, selection, context, previous, question, sources, user_sources, model, connection))
    return dict(snapshot)


def persist(snapshot):
    with db.connect() as c:
        c.execute("UPDATE chat_runs SET status=?,snapshot=?,updated_at=? WHERE id=?", (snapshot["status"], json.dumps(snapshot, ensure_ascii=False), db.now(), snapshot["id"]))
        c.execute("UPDATE messages SET content=?,citations=? WHERE id=?", (snapshot["content"], json.dumps(snapshot["citations"], ensure_ascii=False), snapshot["message_id"]))


def finish(snapshot, status, error=""):
    snapshot.update(status=status, error=error, phase={"completed": "回答完成", "stopped": "已停止", "failed": "回答失败", "interrupted": "回答中断"}[status])
    if not snapshot["content"]:
        snapshot["content"] = error or "本次回答已停止，可以继续提问。"
    persist(snapshot)


async def execute(snapshot, body, paper, selection, context, previous, question, sources, user_sources, model, connection):
    loop, last_save = asyncio.get_running_loop(), [0.0]
    loop_thread = threading.get_ident()

    def receive(event):
        if snapshot["status"] != "running":
            return
        snapshot.update({k: v for k, v in event.items() if k in ("content", "model", "phase")})
        used = set(re.findall(r"\[P(\d+)\]", snapshot["content"]))
        snapshot["citations"] = [c for c in sources if c["label"][1:] in used] + user_sources
        if time.monotonic() - last_save[0] > 1:
            persist(snapshot)
            last_save[0] = time.monotonic()

    def dispatch(event):
        if threading.get_ident() == loop_thread:
            receive(event)
        else:
            loop.call_soon_threadsafe(receive, event)

    try:
        content = [{"type": "input_text", "text": context + "\n\n用户问题：" + question}]
        if selection and selection["kind"] == "region":
            png = await asyncio.to_thread(selections.image, selection)
            content.append({"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png).decode(), "detail": "high"})
        if body.include_page:
            content.append(await asyncio.to_thread(ai.page_content, paper, body.page))
        snapshot["phase"] = "正在连接模型"
        instructions = ("这是围绕一篇论文的持续阅读对话。结合历史继续回答最后一个问题，选区可随本轮变化。"
                        "当前请求中的 [P数字] 映射为准，历史标记不能跨轮复用。历史图片仅有文字记录，不能假装仍看见其像素。"
                        "涉及未给出的内容要说明。")
        if snapshot["context_info"]["omitted_messages"]:
            instructions += "较早的部分历史因长度限制未附入本轮，不要虚构记忆。"
        result = await ai.respond(instructions, previous + [{"role": "user", "content": content}],
                                  model=model, effort=snapshot["effort"], on_event=dispatch, connection=connection)
        snapshot.update(content=result["content"], model=result.get("model") or snapshot["model"])
        used = set(re.findall(r"\[P(\d+)\]", result["content"]))
        snapshot["citations"] = result.get("citations", []) + [c for c in sources if c["label"][1:] in used] + user_sources
        finish(snapshot, "completed")
    except asyncio.CancelledError:
        finish(snapshot, "stopped")
    except HTTPException as exc:
        finish(snapshot, "failed", str(exc.detail))
    except Exception:
        finish(snapshot, "failed", "这次回答未能完成，请检查模型连接后重试。")
    finally:
        _tasks.pop(snapshot["id"], None)
        _live.pop(snapshot["id"], None)


async def cancel(run_id):
    snapshot = _live.get(run_id) or get(run_id)
    task = _tasks.get(run_id)
    if task and snapshot["status"] == "running":
        snapshot["status"] = "stopping"
        snapshot["phase"] = "正在停止模型"
        persist(snapshot)
        task.cancel()
    if task:
        await asyncio.gather(task, return_exceptions=True)
        if snapshot["status"] in ACTIVE:  # Cancellation before execute entered its try block.
            finish(snapshot, "stopped")
            _tasks.pop(run_id, None)
            _live.pop(run_id, None)
    return get(run_id)


async def shutdown():
    for run_id in list(_tasks):
        await cancel(run_id)
