#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OneSearch (com.chat.chatai.chatbot.aichatbot v1.5.5) 上游协议探针
独立还原 + 实测：deviceId 游客登录 -> accessToken -> chatStream SSE
用法:
  python probe_upstream.py                 # 默认游客登录 + 非流式探针
  python probe_upstream.py --stream        # 测 SSEx 流式
  python probe_upstream.py --models        # 拉 chat-setting/option 模型列表
"""
import sys, json, time, uuid, base64, hmac, hashlib, argparse
import urllib.request, urllib.error

BASE = "https://chat-ai.begamob.com/"
SECRET_KEY = "stteam-ikameglobal-chatapiopenai"     # BuildConfig.SECRET_KEY
BUNDLE_ID = "com.chat.chatai.chatbot.aichatbot"      # BuildConfig.APPLICATION_ID
VERSION = "1.5.5"                                    # BuildConfig.VERSION_NAME

UA_OKHTTP = "okhttp/4.12.0"

def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def make_jwt(secret=SECRET_KEY, extra_sec=3600):
    """还原 ApiKeyFactoryImpl.generateToken(): HS256 自签 JWT.
    注意: claims iat=毫秒, exp=秒; exp 与 jjwt expiration(Date) 冗余.
    """
    now_ms = int(time.time() * 1000)
    iat = now_ms
    exp_s = int((now_ms + extra_sec * 1000) / 1000)
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iat": iat,
        "exp": exp_s,
        "bundleId": BUNDLE_ID,
        "os": "Android",
        "versionApp": VERSION,
        "timezone": "Asia/Shanghai",
    }
    h = b64url(json.dumps(header, separators=(",", ":")).encode())
    p = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{b64url(sig)}"

def user_header(device_id, user_id=""):
    # OpenAiUtils.getUserHeader()
    return f"bundleId:{BUNDLE_ID}/versionApp:{VERSION}/OS:Android/osVersion:34/userId:{user_id}/deviceId:{device_id}"

def meta_headers(device_id):
    # AppMetadataInterceptor
    return {
        "x-adjust-id": "",
        "x-app-version": VERSION,
        "x-app-bundle-id": BUNDLE_ID,
        "x-os": "android",
    }

def req(method, path, body=None, headers=None, stream=False, timeout=60):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("User-Agent", UA_OKHTTP)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
        if stream:
            return resp  # caller reads iteratively
        raw = resp.read().decode("utf-8", "replace")
        return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

def login_guest(device_id):
    """POST api/v1/auth/login  {deviceId, type:'guest'}"""
    h = meta_headers(device_id)
    st, raw = req("POST", "api/v1/auth/login",
                  {"deviceId": device_id, "type": "guest"}, h)
    print(f"[login] HTTP {st}")
    print(f"[login] body: {raw[:600]}")
    if 200 <= st < 300:
        try:
            j = json.loads(raw)
            d = j.get("data") or {}
            return d.get("accessToken"), d.get("refreshToken"), d.get("user")
        except Exception as e:
            print("parse err", e)
    return None, None, None

def get_models(token, device_id):
    h = meta_headers(device_id)
    h["Authorization"] = "Bearer " + token
    h["User-Authorization"] = token
    h["user-header"] = user_header(device_id)
    h["language-code"] = "en"
    st, raw = req("GET", "api/v1/chat-setting/option", None, h)
    print(f"[models] HTTP {st}")
    print(f"[models] body: {raw[:1500]}")
    return st, raw

def build_chat_body(prompt, model="gpt-4o-mini"):
    return {
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": prompt}]}
        ],
        "model": model,
        "response_length": "medium",
        "response_tone": "default",
        "topic_type": "general",
        "image_and_analytic": False,
        "tools": [],
        "is_image_vip": False,
    }

def probe_chat_stream(device_id, prompt, model, vendor="v3"):
    """streamChatV3: POST api/v3/chat/stream  走 StreamChatAuthInterceptor(自签JWT)"""
    token = make_jwt()
    h = meta_headers(device_id)
    h["Authorization"] = "Bearer " + token
    h["user-header"] = user_header(device_id)
    path = "api/v3/chat/stream" if vendor == "v3" else "api/v2/chatStream"
    print(f"[stream:{vendor}] POST {path}  model={model}")
    try:
        resp = req("POST", path, build_chat_body(prompt, model), h, stream=True)
    except urllib.error.HTTPError as e:
        print(f"[stream] HTTPError {e.code}: {e.read().decode('utf-8','replace')[:500]}")
        return
    print(f"[stream] HTTP {resp.status}  content-type={resp.headers.get('Content-Type')}")
    chunks = []
    t0 = time.time()
    first = None
    n = 0
    for line in resp:
        s = line.decode("utf-8", "replace").rstrip("\n")
        if first is None:
            first = time.time() - t0
        n += 1
        if s.strip():
            chunks.append(s)
        if n <= 8 or "finish" in s.lower():
            print("  |", s[:200])
    print(f"[stream] lines={n} chunks={len(chunks)} TTFT={first}s total={time.time()-t0:.2f}s")

def probe_chat_nonstream(device_id, prompt, model, vendor="v3"):
    """非流式探针：读完整 SSE 聚合"""
    token = make_jwt()
    h = meta_headers(device_id)
    h["Authorization"] = "Bearer " + token
    h["user-header"] = user_header(device_id)
    path = "api/v3/chat/stream" if vendor == "v3" else "api/v2/chatStream"
    print(f"[chat:{vendor}] POST {path}  model={model}")
    try:
        resp = req("POST", path, build_chat_body(prompt, model), h, stream=True)
    except urllib.error.HTTPError as e:
        print(f"[chat] HTTPError {e.code}: {e.read().decode('utf-8','replace')[:500]}")
        return
    print(f"[chat] HTTP {resp.status} ct={resp.headers.get('Content-Type')}")
    full = []
    for line in resp:
        s = line.decode("utf-8", "replace").rstrip("\n")
        full.append(s)
    txt = "\n".join(full)
    print(f"[chat] raw ({len(txt)} bytes):")
    print(txt[:1200])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", action="store_true")
    ap.add_argument("--models", action="store_true")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--vendor", default="v3", choices=["v2", "v3"])
    ap.add_argument("--prompt", default="用一句话说你是谁")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    device_id = args.device or str(uuid.uuid4())
    print(f"deviceId = {device_id}")
    print("=" * 60)

    if args.models:
        tok, rt, user = login_guest(device_id)
        if tok:
            get_models(tok, device_id)
        return

    if args.stream:
        probe_chat_stream(device_id, args.prompt, args.model, args.vendor)
        return

    probe_chat_nonstream(device_id, args.prompt, args.model, args.vendor)

if __name__ == "__main__":
    main()
