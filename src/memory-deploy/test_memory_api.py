# -*- coding: UTF-8 -*-
"""memory_server 端到端冒烟测试: add_messages(LLM 抽取) -> search_memory(混合检索)."""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path, payload, timeout=180):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


print("health:", urllib.request.urlopen(BASE + "/health", timeout=10).read().decode())

t0 = time.time()
r1 = post("/add_messages/", {
    "messages": [
        {"role": "user", "content": "我在 K3 Pico-ITX 开发板上做 OpenHarmony 移植，编译服务器是 10.0.50.17。"},
        {"role": "assistant", "content": "好的，已了解你的开发环境：SpacemiT K3 Pico-ITX 板 + OHOS 6.1，编译机 snode7。"},
    ],
    "user_id": "kb-test",
    "scope_id": "default",
})
print("add_messages (%.1fs):" % (time.time() - t0), json.dumps(r1, ensure_ascii=False)[:300])

time.sleep(2)
for q in ["开发板 型号", "编译服务器", "OpenHarmony 移植"]:
    r = post("/search_memory/", {"query": q, "user_id": "kb-test", "num": 5})
    hits = r.get("results", [])
    print("search[%s] -> %d hits:" % (q, len(hits)))
    for h in hits[:3]:
        print("   %.3f [%s] %s" % (h.get("score", 0), h.get("type"), (h.get("content") or "")[:80]))
