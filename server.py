#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OneSearch 2API 网关 (OpenAI 兼容)
================================================
上游: https://chat-ai.begamob.com  (com.chat.chatai.chatbot.aichatbot v1.5.5)
认证: 自签 HS256 JWT (密钥内嵌 APK BuildConfig.SECRET_KEY) — 无需登录/号池/设备注册
端点: POST /api/v3/chat/stream (SSE 真流式)

本网关暴露标准 OpenAI 接口，供 new-api / 任意 OpenAI 客户端接入:
  POST /v1/chat/completions   (流式 + 非流式, 支持多模态 image_url, web_search)
  GET  /v1/models
  GET  /health
  GET  /stats

运行:  python server.py            # 默认 0.0.0.0:8920
       python server.py --port 8920 --host 127.0.0.1
环境变量:
       ONESEARCH_API_KEY   网关自身的鉴权 key(可选, 设了则客户端需配对)
       HTTP_PROXY          上游代理(可选, 如 http://127.0.0.1:10808)
"""
import os, sys, json, time, uuid, base64, hmac, hashlib, argparse, asyncio
from typing import List, Optional, Union, Dict, Any

import aiohttp
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ============ 上游常量 (逆向自 APK) ============
UPSTREAM_BASE = "https://chat-ai.begamob.com/"
SECRET_KEY = "stteam-ikameglobal-chatapiopenai"       # BuildConfig.SECRET_KEY
BUNDLE_ID = "com.chat.chatai.chatbot.aichatbot"       # BuildConfig.APPLICATION_ID
VERSION = "1.5.5"                                      # BuildConfig.VERSION_NAME
CHAT_PATH = "api/v3/chat/stream"                       # StreamChatService.streamChatV3
UA = "okhttp/4.12.0"
GATEWAY_API_KEY = os.environ.get("ONESEARCH_API_KEY", "")
HTTP_PROXY = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or None

# ============ 模型目录 (逆向实测 68 个全部 200) ============
# 内置 UI 列表(11) + 枚举实测可用别名
MODELS_UI = [
    {"id": "no_id", "title": "Smart Mode", "type": "smart", "image": True},
    {"id": "Claude Opus 4.6", "title": "Claude Opus 4.6", "type": "pro", "image": True},
    {"id": "claude-sonnet-4.5", "title": "Claude Sonnet 4.5", "type": "pro", "image": True},
    {"id": "Llama 4 Maverick", "title": "Llama 4 Maverick", "type": "pro", "image": True},
    {"id": "gemini-3-flash-preview", "title": "Gemini 3 Flash", "type": "pro", "image": True},
    {"id": "gemini-3-pro-preview", "title": "Gemini 3 Pro", "type": "pro", "image": True},
    {"id": "gemini-2.5-flash", "title": "Gemini 2.5 Flash", "type": "normal", "image": True},
    {"id": "gemini-2.5-pro", "title": "Gemini 2.5 Pro", "type": "pro", "image": True},
    {"id": "Grok-4.1 Fast", "title": "Grok-4.1 Fast", "type": "pro", "image": True},
    {"id": "gpt-5-mini", "title": "GPT-5 mini", "type": "normal", "image": True},
    {"id": "gpt-4o-mini", "title": "GPT-4o mini", "type": "normal", "image": True},
]
# 实测额外可用的标准模型名 (后端真实接受的 stringId)
MODELS_EXTRA = [
    "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-5", "gpt-5-nano",
    "gpt-5.1", "gpt-5.1-mini", "gpt-3.5-turbo", "gpt-4-turbo",
    "o1", "o1-mini", "o1-preview", "o3", "o3-mini", "o3-pro", "o4-mini",
    "claude-3-5-sonnet", "claude-3-5-haiku", "claude-3-7-sonnet",
    "claude-sonnet-4", "claude-opus-4", "claude-opus-4-1", "claude-opus-4-5",
    "claude-opus-4-6", "claude-haiku-4.5",
    "gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-3-flash", "gemini-3-pro",
    "grok-3", "grok-3-mini", "grok-4", "grok-4-fast", "grok-4.1-fast", "grok-2",
    "llama-4-maverick", "llama-4-scout", "llama-3.3-70b", "llama-3.1-70b", "llama-3.1-405b",
    "deepseek-chat", "deepseek-r1", "deepseek-v3", "deepseek-reasoner",
    "qwen", "qwen-max", "qwen2.5", "mistral-large", "kimi",
]
ALL_MODEL_IDS = [m["id"] for m in MODELS_UI] + MODELS_EXTRA
MODEL_TITLE = {m["id"]: m["title"] for m in MODELS_UI}

# ============ 自签 JWT ============
def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def make_jwt(secret: str = SECRET_KEY, exp_sec: int = 86400) -> str:
    """还原 ApiKeyFactoryImpl.generateToken(): iat=毫秒, exp=秒, HS256"""
    now_ms = int(time.time() * 1000)
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iat": now_ms,
        "exp": int((now_ms + exp_sec * 1000) / 1000),
        "bundleId": BUNDLE_ID,
        "os": "Android",
        "versionApp": VERSION,
        "timezone": "Asia/Shanghai",
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"

_device_id = str(uuid.uuid4())

def upstream_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Authorization": "Bearer " + make_jwt(),
        "user-header": f"bundleId:{BUNDLE_ID}/versionApp:{VERSION}/OS:Android/osVersion:34/userId:/deviceId:{_device_id}",
        "x-adjust-id": "",
        "x-app-version": VERSION,
        "x-app-bundle-id": BUNDLE_ID,
        "x-os": "android",
        "Accept": "text/event-stream",
    }

# ============ 请求体转换 (OpenAI -> OneSearch) ============
def convert_messages(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for m in msgs:
        role = m.get("role", "user")
        content = m.get("content")
        parts = []
        if isinstance(content, str):
            parts.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                t = c.get("type")
                if t == "text":
                    parts.append({"type": "text", "text": c.get("text", "")})
                elif t == "image_url":
                    iu = c.get("image_url") or {}
                    url = iu.get("url") if isinstance(iu, dict) else iu
                    if url:
                        parts.append({"type": "image_url",
                                      "image_url": {"url": url, "detail": (iu.get("detail") if isinstance(iu,dict) else "auto") or "auto"}})
        out.append({"role": role, "content": parts})
    return out

def build_upstream_body(req: "ChatRequest") -> Dict[str, Any]:
    tools = []
    if req.web_search:
        tools.append("web_search")
    if req.deep_search:
        tools.append("deep_search")
    return {
        "max_tokens": req.max_tokens or 4096,
        "messages": convert_messages(req.messages),
        "model": req.model or "gpt-4o-mini",
        "response_length": req.response_length or "medium",
        "response_tone": req.response_tone or "default",
        "topic_type": "general",
        "image_and_analytic": False,
        "tools": tools,
        "is_image_vip": False,
    }

# ============ FastAPI ============
app = FastAPI(title="OneSearch-2API Gateway", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ChatRequest(BaseModel):
    model: str = "gpt-4o-mini"
    messages: List[Dict[str, Any]]
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    response_length: Optional[str] = None
    response_tone: Optional[str] = None
    web_search: bool = False
    deep_search: bool = False
    # 允许透传 tools 数组（OpenAI 客户端可能传工具定义，这里仅识别联网开关）

_stats = {"requests": 0, "errors": 0, "streams": 0, "start": time.time()}

def _check_auth(request: Request):
    if not GATEWAY_API_KEY:
        return
    auth = request.headers.get("authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
    if key != GATEWAY_API_KEY:
        raise HTTPException(status_code=401, detail="invalid gateway api key")

def _gen_id(prefix="chatcmpl"):
    return f"{prefix}-{uuid.uuid4().hex[:24]}"

@app.get("/")
async def index():
    import pathlib
    p = pathlib.Path(__file__).parent / "demo.html"
    if p.exists():
        from fastapi.responses import HTMLResponse
        return HTMLResponse(p.read_text(encoding="utf-8"))
    return {"service": "OneSearch-2API", "docs": "/v1/models"}

@app.get("/health")
async def health():
    return {"status": "ok", "upstream": UPSTREAM_BASE, "models": len(ALL_MODEL_IDS),
            "uptime": round(time.time() - _stats["start"], 1)}

@app.get("/stats")
async def stats():
    return {**_stats, "uptime": round(time.time() - _stats["start"], 1)}

@app.get("/v1/models")
async def list_models():
    data = []
    for mid in ALL_MODEL_IDS:
        data.append({"id": mid, "object": "model", "created": int(time.time()),
                     "owned_by": "onesearch", "title": MODEL_TITLE.get(mid, mid)})
    return {"object": "list", "data": data}

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, request: Request):
    _check_auth(request)
    _stats["requests"] += 1
    body = build_upstream_body(req)
    url = UPSTREAM_BASE + CHAT_PATH
    cid = _gen_id()
    created = int(time.time())

    if req.stream:
        _stats["streams"] += 1
        return StreamingResponse(_stream_upstream(url, body, req.model, cid, created),
                                 media_type="text/event-stream")
    else:
        # 聚合非流式
        full, finish, err = await _collect_upstream(url, body)
        if err:
            _stats["errors"] += 1
            return JSONResponse(status_code=502, content={"error": {"message": err, "type": "upstream_error"}})
        prompt_tokens = sum(len(json.dumps(m)) for m in req.messages) // 4
        completion_tokens = len(full) // 4
        return {
            "id": cid, "object": "chat.completion", "created": created,
            "model": req.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": full},
                         "finish_reason": finish or "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens},
        }

async def _iter_upstream(url, body):
    """迭代上游 SSE, yield 每行 data: 后的 JSON 对象"""
    timeout = aiohttp.ClientTimeout(total=300, sock_read=120)
    conn = aiohttp.TCPConnector(limit=64, force_close=False, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(timeout=timeout, connector=conn) as sess:
        async with sess.post(url, json=body, headers=upstream_headers(), proxy=HTTP_PROXY) as resp:
            if resp.status not in (200, 201):
                txt = await resp.text()
                yield {"__http_error__": f"HTTP {resp.status}: {txt[:300]}"}
                return
            async for raw in resp.content:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    yield json.loads(payload)
                except Exception:
                    continue

async def _stream_upstream(url, body, model, cid, created):
    """上游 SSE -> OpenAI SSE 格式"""
    try:
        async for obj in _iter_upstream(url, body):
            if "__http_error__" in obj:
                _stats["errors"] += 1
                err = {"error": {"message": obj["__http_error__"], "type": "upstream_error"}}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return
            # 上游错误块
            if "error" in obj and obj.get("error"):
                e = obj["error"]
                msg = e.get("message") if isinstance(e, dict) else str(e)
                _stats["errors"] += 1
                yield f"data: {json.dumps({'error': {'message': msg, 'type': 'upstream_error'}}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return
            d = obj.get("data") or {}
            piece = d.get("contentText") or d.get("content") or ""
            finish = d.get("finishReason")
            if piece:
                chunk = {"id": cid, "object": "chat.completion.chunk", "created": created,
                         "model": model,
                         "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            if finish:
                chunk = {"id": cid, "object": "chat.completion.chunk", "created": created,
                         "model": model,
                         "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        _stats["errors"] += 1
        yield f"data: {json.dumps({'error': {'message': f'stream exception: {e}', 'type': 'gateway_error'}}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

async def _collect_upstream(url, body):
    full = ""
    finish = ""
    err = ""
    try:
        async for obj in _iter_upstream(url, body):
            if "__http_error__" in obj:
                err = obj["__http_error__"]; break
            if "error" in obj and obj.get("error"):
                e = obj["error"]
                err = (e.get("message") if isinstance(e, dict) else str(e)); break
            d = obj.get("data") or {}
            if d.get("contentText"):
                full += d["contentText"]
            if d.get("finishReason"):
                finish = d["finishReason"]
    except Exception as e:
        err = f"collect exception: {e}"
    return full, finish, err

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8920)
    args = ap.parse_args()
    import uvicorn
    print(f"OneSearch-2API Gateway  上游={UPSTREAM_BASE}")
    print(f"模型 {len(ALL_MODEL_IDS)} 个 | 监听 {args.host}:{args.port} | 代理={HTTP_PROXY}")
    print(f"网关鉴权键={'<设置>' if GATEWAY_API_KEY else '无(开放)'}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

if __name__ == "__main__":
    main()
