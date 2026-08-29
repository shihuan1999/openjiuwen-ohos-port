# -*- coding: UTF-8 -*-
import json
import urllib.request


def post(p, d):
    r = urllib.request.Request(
        "http://127.0.0.1:8000" + p,
        data=json.dumps(d).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(r, timeout=60).read())


for th in [0.0, 0.1, 0.2, 0.3]:
    r = post("/search_memory/", {"query": "编译服务器 IP 是多少", "user_id": "kb-test", "num": 8, "threshold": th})
    hits = r.get("results", [])
    print("th=%s hits=%d" % (th, len(hits)))
    for h in hits[:5]:
        print("  %.4f [%s] %s" % (h.get("score", 0), h.get("type"), (h.get("content") or "")[:60]))
