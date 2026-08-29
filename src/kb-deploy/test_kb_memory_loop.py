# -*- coding: UTF-8 -*-
"""记忆闭环验证: kb agent 写入事实 -> 新任务检索回忆。"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def req(method, path, body=None, timeout=30):
    r = urllib.request.Request(BASE + path, method=method,
                               data=json.dumps(body).encode() if body else None,
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def run_wait(q, timeout=420):
    t = req("POST", "/api/run", {"agent": "kb", "query": q})
    tid = t["id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(5)
        s = req("GET", "/api/task/" + tid)
        if s["state"] != "running":
            return s
    return {"state": "timeout"}


WRITE_QUERY = (
    "请先用 remember 工具记住这条重要事实："
    "「用户团队的鸿蒙知识库 agent 部署在 K3 pico 板上，代号 AgentHub-KB，2026-08-27 上线」。"
    "然后用一句中文确认已保存。"
)

RECALL_QUERY = (
    "请用 ltm_search 查询「AgentHub-KB 部署 哪块板子」，"
    "把命中的长期记忆内容原样引用出来，并用一句中文总结。"
)

print("== 第 1 轮: 写入记忆 ==")
s1 = run_wait(WRITE_QUERY)
print("state:", s1["state"])
print("result:", (s1.get("result") or s1.get("error") or "")[:300])
mem_ev = [e for e in s1.get("events", []) if e.get("type") == "memory_write"]
print("memory_write events:", len(mem_ev))

print("\n== 第 2 轮: 跨会话回忆 ==")
s2 = run_wait(RECALL_QUERY)
print("state:", s2["state"])
print("result:", (s2.get("result") or s2.get("error") or "")[:400])
