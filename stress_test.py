#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OneSearch-2API 压测脚本 (第一轮)
================================
验证: 延迟 / 并发 / 流式TTFT / 错误路径 / 稳定性
用法: python stress_test.py --base http://127.0.0.1:8920
"""
import time, json, argparse, statistics, concurrent.futures as cf
import requests

def one(base, model, prompt, stream=False, timeout=120):
    t0 = time.time()
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": stream}
    try:
        if stream:
            r = requests.post(base + "/v1/chat/completions", json=body, stream=True, timeout=timeout)
            ttft = None; n = 0; txt = ""
            for line in r.iter_lines():
                if not line:
                    continue
                s = line.decode("utf-8")
                if not s.startswith("data:"):
                    continue
                p = s[5:].strip()
                if p == "[DONE]":
                    break
                if ttft is None:
                    ttft = time.time() - t0
                n += 1
                try:
                    j = json.loads(p)
                    dt = j["choices"][0]["delta"].get("content")
                    if dt:
                        txt += dt
                except Exception:
                    pass
            return {"ok": True, "st": r.status_code, "ttft": ttft, "chunks": n,
                    "t": time.time() - t0, "len": len(txt)}
        else:
            r = requests.post(base + "/v1/chat/completions", json=body, timeout=timeout)
            d = r.json()
            ok = r.status_code == 200 and "choices" in d
            txt = d["choices"][0]["message"]["content"] if ok else ""
            return {"ok": ok, "st": r.status_code, "t": time.time() - t0, "len": len(txt)}
    except Exception as e:
        return {"ok": False, "st": "EXC", "t": time.time() - t0, "err": str(e)[:80]}

def pct(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    k = int(len(s) * p / 100)
    return s[min(k, len(s) - 1)]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8920")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("=" * 60)
    print("OneSearch-2API 压测 R1")
    print("=" * 60)

    # 0. health
    t0 = time.time()
    try:
        h = requests.get(base + "/health", timeout=10).json()
        print(f"[0] health OK  {round(time.time()-t0,3)}s  models={h.get('models')} upstream={h.get('upstream')}")
    except Exception as e:
        print(f"[0] health FAIL {e}"); return

    # 1. baseline latency (single, non-stream)
    print("\n[1] 基线延迟 (非流式 x5)")
    lats = []
    for i in range(5):
        r = one(base, "gpt-4o-mini", f"用一句话说第{i}句话")
        if r["ok"]:
            lats.append(r["t"])
            print(f"  #{i+1} {round(r['t'],2)}s len={r['len']}")
        else:
            print(f"  #{i+1} FAIL {r.get('st')} {r.get('err','')}")
    if lats:
        print(f"  median={round(statistics.median(lats),2)}s min={round(min(lats),2)}s max={round(max(lats),2)}s")

    # 2. streaming TTFT
    print("\n[2] 流式 TTFT (x3)")
    for i in range(3):
        r = one(base, "gpt-4o-mini", "写一小段话讲讲AI", stream=True)
        if r["ok"]:
            print(f"  #{i+1} ttft={round(r['ttft'],2)}s total={round(r['t'],2)}s chunks={r['chunks']}")
        else:
            print(f"  #{i+1} FAIL {r.get('st')}")

    # 3. model coverage (each once)
    print("\n[3] 模型覆盖 (各一次)")
    models = ["gpt-4o-mini", "gpt-5-mini", "claude-sonnet-4.5", "Claude Opus 4.6",
              "gemini-2.5-flash", "gemini-3-pro", "Grok-4.1 Fast", "deepseek-chat", "no_id"]
    for m in models:
        r = one(base, m, "回一个字：好")
        status = "OK" if r["ok"] else "FAIL"
        print(f"  {m:22} {status:4} {round(r['t'],2)}s len={r.get('len',0)}")

    # 4. concurrency ladder
    print("\n[4] 并发阶梯")
    for conc in [5, 10, 20]:
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=conc) as ex:
            futs = [ex.submit(one, base, "gpt-4o-mini", f"并发测试{i}") for i in range(conc)]
            results = [f.result() for f in futs]
        ok = sum(1 for r in results if r["ok"])
        wall = time.time() - t0
        plats = [r["t"] for r in results if r["ok"]]
        codes = {}
        for r in results:
            codes[r["st"]] = codes.get(r["st"], 0) + 1
        print(f"  x{conc:2}  ok={ok}/{conc}  wall={round(wall,2)}s  p50={round(pct(plats,50),2)}s  codes={codes}")

    # 5. sustained fire (30 quick)
    print("\n[5] 持续连发 x30 (单线程)")
    ok = 0; fails = 0; ft = []
    for i in range(30):
        r = one(base, "gpt-4o-mini", "回好")
        if r["ok"]:
            ok += 1; ft.append(r["t"])
        else:
            fails += 1
            if r.get("st") in (429, 502, 503):
                print(f"  #{i} rate/err {r['st']}")
    print(f"  ok={ok}/30 fails={fails} p50={round(pct(ft,50),2)}s")

    # 6. error paths
    print("\n[6] 错误路径")
    # invalid model -> upstream fallback?
    r = one(base, "nonexistent-xyz", "hi")
    print(f"  invalid-model: ok={r['ok']} st={r['st']} (上游对非法模型可能兜底200)")
    # bad body
    try:
        rr = requests.post(base + "/v1/chat/completions", json={"model": "gpt-4o-mini"}, timeout=20)
        print(f"  missing-messages: st={rr.status_code} (应422)")
    except Exception as e:
        print(f"  missing-messages EXC {e}")

    print("\n" + "=" * 60)
    print("压测完成")

if __name__ == "__main__":
    main()
