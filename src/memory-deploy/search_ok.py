# -*- coding: UTF-8 -*-
"""检查 memory_server REST 检索是否真实可用(命中数>0 输出 OK)。ASCII-only 便于 shell。"""
import json
import urllib.request

req = urllib.request.Request(
    "http://127.0.0.1:8000/search_memory/",
    data=json.dumps({"query": "OpenHarmony", "user_id": "kb-test",
                     "scope_id": "default", "num": 3, "threshold": 0.0}).encode(),
    headers={"Content-Type": "application/json"})
try:
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    n = len(r.get("results", []))
    print("OK" if n > 0 else "EMPTY", n)
except Exception as e:
    print("FAIL", e)
