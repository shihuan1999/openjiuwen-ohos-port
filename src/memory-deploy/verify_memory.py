# -*- coding: UTF-8 -*-
"""记忆闭环复现验证: 新用户写入 -> 立即/延迟检索,确认检索时序特性。"""
import json
import time
import urllib.request


def post(path, payload, timeout=180):
    req = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def search(q, uid, th=0.0):
    r = post("/search_memory/", {"query": q, "user_id": uid, "scope_id": "default", "num": 5, "threshold": th})
    return r.get("results", [])


UID = "kb-user2"
print("1) add_messages (LLM 抽取)...")
t0 = time.time()
r = post("/add_messages/", {
    "messages": [
        {"role": "user", "content": "我们团队的鸿蒙应用包名前缀是 com.example，设备 IP 网段是 10.0.90.x。"},
        {"role": "assistant", "content": "记住了：包名前缀 com.example，设备网段 10.0.90.x。"},
    ],
    "user_id": UID, "scope_id": "default",
})
print("   add ok in %.1fs" % (time.time() - t0))

for delay in [0, 5, 15, 30]:
    if delay:
        time.sleep(delay)
    hits = search("设备 IP 网段", UID)
    print("2) search after +%2ds -> %d hits %s" % (
        delay, len(hits), ("| top: %.3f %s" % (hits[0]["score"], hits[0]["content"][:40])) if hits else ""))
    if hits:
        break

hits = search("应用包名", UID)
print("3) search[应用包名] -> %d hits" % len(hits))
for h in hits[:3]:
    print("   %.3f %s" % (h["score"], h["content"][:50]))
