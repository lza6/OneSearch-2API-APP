#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OneSearch 模型矩阵 + 能力探测（输出存文件避免GBK乱码）"""
import json, uuid, sys, time, io
sys.path.insert(0, '.')
import probe_upstream as P

OUT = io.StringIO()
def log(*a):
    line = " ".join(str(x) for x in a)
    OUT.write(line + "\n")
    print(line)

def stream_once(dev, model, prompt='请只回复四个字：你好世界', vip=False, maxtok=1024, timeout=60):
    tok = P.make_jwt()
    h = P.meta_headers(dev)
    h['Authorization'] = 'Bearer ' + tok
    h['user-header'] = P.user_header(dev)
    body = P.build_chat_body(prompt, model)
    body['is_image_vip'] = vip
    body['max_tokens'] = maxtok
    t0 = time.time()
    try:
        resp = P.req('POST', 'api/v3/chat/stream', body, h, stream=True, timeout=timeout)
    except Exception as e:
        return {'st': 'EXC', 'txt': str(e)[:120], 'n': 0, 't': 0, 'fin': ''}
    st = resp.status; txt = ''; fin = ''; n = 0; first = None; codes = set()
    for line in resp:
        s = line.decode('utf-8', 'replace').strip()
        if not s.startswith('data:'):
            continue
        n += 1
        if first is None:
            first = time.time() - t0
        try:
            j = json.loads(s[5:].strip())
            d = j.get('data') or {}
            if d.get('contentText'):
                txt += d['contentText']
            if d.get('finishReason'):
                fin = d['finishReason']
            if j.get('statusCode'):
                codes.add(j['statusCode'])
        except Exception:
            pass
    return {'st': st, 'txt': txt, 'n': n, 't': round(time.time() - t0, 2),
            'fin': fin, 'ttft': round(first, 3) if first else None, 'codes': sorted(codes)}

dev = str(uuid.uuid4())
models = ['gpt-4o-mini', 'gpt-5-mini', 'gemini-2.5-flash', 'gemini-2.5-pro',
          'gemini-3-pro-preview', 'gemini-3-flash-preview', 'claude-sonnet-4.5',
          'Claude Opus 4.6', 'Grok-4.1 Fast', 'Llama 4 Maverick', 'no_id']

log(f"deviceId={dev}  test prompt=请只回复四个字：你好世界")
log(f"{'model':26} | st  | chk | ttft | t     | codes   | finish | reply")
log("-" * 110)
results = {}
for m in models:
    r = stream_once(dev, m)
    results[m] = r
    reply = (r['txt'] or '(EMPTY)').replace('\n', ' ')[:36]
    log(f"{m:26} | {str(r['st']):3} | {r['n']:3} | {str(r['ttft']):5} | {str(r['t']):5} | {str(r['codes']):8} | {r['fin']:6} | {reply}")
    time.sleep(0.3)

with open('model_matrix_result.txt', 'w', encoding='utf-8') as f:
    f.write(OUT.getvalue())
    f.write("\n\n=== FULL REPLIES ===\n")
    for m, r in results.items():
        f.write(f"\n[{m}] st={r['st']} finish={r['fin']} codes={r['codes']}\n{r['txt']}\n")
log("\nsaved model_matrix_result.txt")
