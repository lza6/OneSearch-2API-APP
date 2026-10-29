#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大规模模型枚举 - 找出后端所有可用模型名"""
import json, uuid, sys, time
sys.path.insert(0, '.')
import probe_upstream as P

CANDIDATES = [
    # OpenAI
    'gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'gpt-4.1-mini', 'gpt-4.1-nano',
    'gpt-5', 'gpt-5-mini', 'gpt-5-nano', 'gpt-5.1', 'gpt-5.1-mini',
    'gpt-3.5-turbo', 'gpt-4-turbo',
    'o1', 'o1-mini', 'o1-preview', 'o3', 'o3-mini', 'o3-pro', 'o4-mini',
    # Anthropic
    'claude-3-5-sonnet', 'claude-3-5-sonnet-latest', 'claude-3-5-haiku',
    'claude-3-7-sonnet', 'claude-3-7-sonnet-latest',
    'claude-sonnet-4', 'claude-sonnet-4-20250514', 'claude-sonnet-4.5', 'claude-sonnet-4-5',
    'claude-opus-4', 'claude-opus-4-1', 'claude-opus-4.1', 'claude-opus-4-5', 'claude-opus-4-6', 'claude-opus-4.6',
    'claude-haiku-4.5', 'claude-haiku-4-5',
    # Google
    'gemini-2.0-flash', 'gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.5-flash-lite',
    'gemini-3-flash', 'gemini-3-pro', 'gemini-3-flash-preview', 'gemini-3-pro-preview',
    'gemini-3.0-flash', 'gemini-2.5-flash-preview',
    # xAI
    'grok-3', 'grok-3-mini', 'grok-4', 'grok-4-fast', 'grok-4.1-fast', 'grok-2',
    # Meta
    'llama-4-maverick', 'llama-4-scout', 'llama-3.3-70b', 'llama-3.1-70b', 'llama-3.1-405b',
    # DeepSeek / others
    'deepseek-chat', 'deepseek-r1', 'deepseek-v3', 'deepseek-reasoner',
    'qwen', 'qwen-max', 'qwen2.5', 'mistral-large', 'kimi',
    # UI stringIds (exact)
    'Claude Opus 4.6', 'claude-sonnet-4.5', 'Llama 4 Maverick', 'Grok-4.1 Fast',
]

def probe(dev, model):
    tok = P.make_jwt()
    h = P.meta_headers(dev)
    h['Authorization'] = 'Bearer ' + tok
    h['user-header'] = P.user_header(dev)
    body = P.build_chat_body('hi', model)
    body['max_tokens'] = 64
    try:
        resp = P.req('POST', 'api/v3/chat/stream', body, h, stream=True, timeout=40)
    except Exception as e:
        return ('EXC', str(e)[:50])
    txt = ''; err = ''
    try:
        for line in resp:
            s = line.decode('utf-8', 'replace').strip()
            if not s.startswith('data:'):
                continue
            try:
                j = json.loads(s[5:]); d = j.get('data') or {}
                if d.get('contentText'):
                    txt += d['contentText']
                if j.get('error'):
                    err = 'ERR' + str(j['error'].get('statusCode'))
                if j.get('statusCode') and j['statusCode'] != 200 and not d.get('contentText'):
                    err = 'code' + str(j['statusCode'])
            except Exception:
                pass
    except Exception as e:
        return ('READ_EXC', str(e)[:50])
    if err:
        return (err, '')
    return ('OK', txt[:20].replace('\n', ' '))

dev = str(uuid.uuid4())
lines = [f'deviceId={dev}']
ok_models = []
for m in CANDIDATES:
    st, txt = probe(dev, m)
    if st == 'OK':
        ok_models.append(m)
        lines.append(f'{m:28} OK   {txt}')
    else:
        lines.append(f'{m:28} {st}')
    time.sleep(0.15)
lines.append('\n=== AVAILABLE (' + str(len(ok_models)) + ') ===')
lines.append(', '.join(ok_models))
open('model_enumerate.txt', 'w', encoding='utf-8').write('\n'.join(lines))
print(f'available {len(ok_models)}/{len(CANDIDATES)}')
print('\n'.join(ok_models))
