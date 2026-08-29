# -*- coding: UTF-8 -*-
"""kb agent 端到端: sidecar run -> 轮询 -> 校验结果与事件。"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
QUERY = sys.argv[1] if len(sys.argv) > 1 else (
    "请作为鸿蒙知识库助手完成三件事："
    "1) kb_search 查 K3 Pico-ITX 的芯片与 AI 算力规格并引用来源文件；"
    "2) device_probe(topic=\"thermal\") 看本机温度；"
    "3) ltm_search 查询关于用户的历史记忆；"
    "最后用中文输出结构化回答：硬件规格（官方，注明来源文件）+ 本机实时温度 + 记忆命中情况。"
)


def req(method, path, body=None, timeout=30):
    r = urllib.request.Request(BASE + path, method=method,
                               data=json.dumps(body).encode() if body else None,
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


t = req("POST", "/api/run", {"agent": "kb", "query": QUERY})
tid = t["id"]
print("task:", tid)
t0 = time.time()
while time.time() - t0 < 600:
    time.sleep(5)
    s = req("GET", "/api/task/" + tid)
    if s["state"] != "running":
        break
print("state:", s["state"], "in %.0fs" % (time.time() - t0))
kinds = {}
for e in s.get("events", []):
    kinds[e.get("type")] = kinds.get(e.get("type"), 0) + 1
print("events:", kinds)
for e in s.get("events", []):
    if e.get("type") in ("tool_start", "tool_error"):
        print("  ", e.get("type"), e.get("tool"), (e.get("args") or e.get("detail") or "")[:80])
print("---- result ----")
print((s.get("result") or s.get("error") or "")[:1500])
