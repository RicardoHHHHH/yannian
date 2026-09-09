"""OpenAI-compatible Chat Completions, including cancellable answer streaming."""
import json

import httpx
from fastapi import HTTPException


def check_response(response):
    if not 200 <= response.status_code < 300:
        explanations = {400: "模型服务不支持当前参数，请检查模型、图片与思考设置。",
                        401: "API Key 未被服务接受。", 403: "账户或模型没有访问权限。",
                        404: "模型或 API 地址不存在，请填写基础地址而非完整接口路径。",
                        429: "API 额度不足或触发速率限制。"}
        # Never echo service response bodies, which may include credentials or prompts.
        raise HTTPException(502, explanations.get(response.status_code, "模型服务暂时不可用。"))


def messages_for_chat(instructions, messages):
    result = [{"role": "system", "content": instructions}]
    for message in messages:
        content = message["content"]
        if isinstance(content, list):
            converted = []
            for part in content:
                if part["type"] in {"input_text", "output_text"}:
                    converted.append({"type": "text", "text": part["text"]})
                elif part["type"] == "input_image":
                    image = {"url": part["image_url"]}
                    if part.get("detail"):
                        image["detail"] = part["detail"]
                    converted.append({"type": "image_url", "image_url": image})
                else:
                    raise HTTPException(400, "当前 API 不支持此附件类型。")
            content = converted
        result.append({"role": message["role"], "content": content})
    return result


async def sse_events(response):
    data = []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield "\n".join(data)


async def respond(config, credential, instructions, messages, max_tokens, model, effort, on_event):
    payload = {"model": model, "messages": messages_for_chat(instructions, messages),
               "max_tokens": max_tokens, "stream": bool(on_event)}
    if effort:
        if config["api_preset"] == "deepseek":
            payload["thinking"] = {"type": "disabled" if effort == "none" else "enabled"}
            if effort != "none":
                payload["reasoning_effort"] = effort
        else:
            payload["reasoning_effort"] = effort
    if config["api_preset"] == "deepseek" and effort and effort != "none":
        payload["max_tokens"] = max(max_tokens, 16384)  # Thinking and final answer share this budget.
    headers = {"Authorization": "Bearer " + credential} if credential else {}
    content, actual_model, usage, finish = "", model, None, None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(240, connect=20), follow_redirects=False) as client:
            endpoint = config["base_url"] + "/chat/completions"
            if on_event:
                async with client.stream("POST", endpoint, json=payload, headers=headers) as response:
                    check_response(response)
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        raise HTTPException(502, "模型服务未返回流式回答；请检查 Chat Completions 接口兼容性。")
                    done = False
                    async for event in sse_events(response):
                        if event.strip() == "[DONE]":
                            done = True
                            break
                        raw = json.loads(event)
                        if raw.get("error"):
                            raise HTTPException(502, "模型服务在生成过程中报错；已保留收到的回答。")
                        actual_model = raw.get("model") or actual_model
                        usage = raw.get("usage") or usage
                        choices = raw.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        # Only answer content is emitted. Reasoning text never enters history.
                        if delta.get("reasoning_content") and not content:
                            on_event({"phase": "模型正在思考", "model": actual_model})
                        text = delta.get("content") or delta.get("refusal") or ""
                        if isinstance(text, str) and text:
                            content += text
                            on_event({"content": content, "phase": "正在回答", "model": actual_model})
                        finish = choice.get("finish_reason") or finish
                    if not done and not finish:
                        raise HTTPException(502, "模型连接提前断开；已保留收到的回答，可以继续追问。")
            else:
                response = await client.post(endpoint, json=payload, headers=headers)
                check_response(response)
                raw = response.json()
                choice = (raw.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                content = message.get("content") or message.get("refusal") or ""
                actual_model, usage, finish = raw.get("model") or model, raw.get("usage"), choice.get("finish_reason")
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "模型请求超时；可以缩短问题或稍后重试。") from exc
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(502, "模型连接或返回格式异常，请检查 API 地址与协议。") from exc
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(502, "模型没有返回可显示的回答；可关闭思考或缩短问题后重试。")
    if finish == "length":
        content += "\n\n> 本次回答达到输出限制，内容可能尚未完成，可以继续追问。"
    elif finish not in {None, "stop", "content_filter"}:
        raise HTTPException(502, "模型未能正常完成回答；已保留收到的内容，可以重试。")
    return {"content": content.strip(), "model": actual_model, "citations": [], "usage": usage,
            "provider": "api", "web_searched": False}
