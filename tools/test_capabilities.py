#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""能力探测: web_search / image / 多轮 / 上下文 / 错误路径"""
import json, uuid, sys, time
sys.path.insert(0, '.')
import probe_upstream as P

dev = str(uuid.uuid4())
out = []
def log(s):
    out.append(s); print(s)

def do(model, messages, tools=None, vip=False, maxtok=512, label=''):
    tok = P.make_jwt()
    h = P.meta_headers(dev)
    h['Authorization'] = 'Bearer ' + tok
    h['user-header'] = P.user_header(dev)
    body = {
        'max_tokens': maxtok, 'messages': messages, 'model': model,
        'response_length': 'medium', 'response_tone': 'default', 'topic_type': 'general',
        'image_and_analytic': False, 'tools': tools or [], 'is_image_vip': vip,
    }
    t0 = time.time()
    try:
        resp = P.req('POST', 'api/v3/chat/stream', body, h, stream=True, timeout=90)
    except Exception as e:
        return {'err': 'EXC ' + str(e)[:80]}
    st = resp.status; txt = ''; webs = []; finish = ''; err = ''
    for line in resp:
        s = line.decode('utf-8', 'replace').strip()
        if not s.startswith('data:'):
            continue
        try:
            j = json.loads(s[5:]); d = j.get('data') or {}
            if d.get('contentText'):
                txt += d['contentText']
            if d.get('webs'):
                webs = d['webs']
            if d.get('finishReason'):
                finish = d['finishReason']
            if j.get('error'):
                err = 'ERR' + str(j['error'].get('statusCode'))
            if j.get('statusCode') and j['statusCode'] != 200 and not d.get('contentText'):
                err = 'code' + str(j['statusCode'])
        except Exception:
            pass
    return {'st': st, 'txt': txt, 'webs': webs, 'finish': finish, 'err': err,
            't': round(time.time() - t0, 2)}

# 1. web_search
log("### 1. web_search (联网搜索)")
r = do('gpt-4o-mini', [{'role': 'user', 'content': [{'type': 'text', 'text': '今天有什么重要的科技新闻?简短'}]}], tools=['web_search'])
log(f"  err={r.get('err')} finish={r.get('finish')} t={r.get('t')}")
log(f"  reply: {(r.get('txt') or '')[:120]}")
log(f"  webs: {len(r.get('webs') or [])} sources")
for w in (r.get('webs') or [])[:3]:
    log(f"    - {w.get('title','')[:40]} | {w.get('uri','')[:50]}")

# 2. image input
log("\n### 2. 多模态读图 (image_url)")
img_msg = [{'role': 'user', 'content': [
    {'type': 'text', 'text': '这张图是什么颜色？只回答颜色'},
    {'type': 'image_url', 'image_url': {'url': 'https://raw.githubusercontent.com/github/explore/main/topics/python/python.png', 'detail': 'auto'}}
]}]
r = do('gpt-4o-mini', img_msg)
log(f"  err={r.get('err')} finish={r.get('finish')} t={r.get('t')}")
log(f"  reply: {(r.get('txt') or '')[:120]}")

# 3. multi-turn
log("\n### 3. 多轮对话记忆")
r = do('gpt-4o-mini', [
    {'role': 'user', 'content': [{'type': 'text', 'text': '我叫小明'}]},
    {'role': 'assistant', 'content': [{'type': 'text', 'text': '你好小明'}]},
    {'role': 'user', 'content': [{'type': 'text', 'text': '我叫什么?'}]},
])
log(f"  reply: {(r.get('txt') or '')[:120]}")

# 4. context limit (approx with repeated)
log("\n### 4. 长上下文 (重复文本约 6万字)")
big = '这是一段测试文本。' * 8000
r = do('gpt-4o-mini', [{'role': 'user', 'content': [{'type': 'text', 'text': big + '\n\n以上文本重复了多少次大约?一句话'}]}], maxtok=200, label='ctx')
log(f"  err={r.get('err')} finish={r.get('finish')} t={r.get('t')}")
log(f"  reply: {(r.get('txt') or '')[:100]}")

# 5. error path - invalid model
log("\n### 5. 错误路径 (无效模型)")
r = do('non-existent-model-xyz', [{'role': 'user', 'content': [{'type': 'text', 'text': 'hi'}]}])
log(f"  err={r.get('err')} reply: {(r.get('txt') or '')[:60]}")

open('capability_probe.txt', 'w', encoding='utf-8').write('\n'.join(out))
log("\nsaved capability_probe.txt")
