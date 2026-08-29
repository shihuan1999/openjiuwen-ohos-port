[1][18:37:59] Not support std mode
# -*- coding: UTF-8 -*-
"""OHOS 鸿蒙知识库 agent（HarmonyOS Knowledge Base Agent）.

基于 openjiuwen ReActAgent，三类能力：
  1. kb_search/kb_docs —— 本地知识库混合检索（哈希向量余弦 + CJK bigram 关键词，
     语料 = K3 Pico-ITX 官方规格 + SpacemiT K3 芯片/生态 + 本机实时硬件快照 + OHOS 移植实录）
  2. device_probe —— 设备实时硬件探测（cpu/mem/thermal/storage/net/drm/audio）
  3. ltm_search/remember —— 经 memory_server(:8000, jiuwen_memory) 的长期记忆
     （跨会话记住用户环境与偏好；这正是 agent-memory plugin 的 REST 接入形态）

Runs on K3 pico under /data/python312. CLI: python3 kb_agent.py [model] [query]
"""
import asyncio
import glob
import json
import math
import os
import sys
import time
import urllib.request

os.environ.setdefault("API_BASE", "https://api.rvcompute.com:60000/v1")
os.environ.setdefault("API_KEY", "sk-YOUR_API_KEY")
os.environ.setdefault("MODEL_PROVIDER", "openai")
os.environ.setdefault("MODEL_NAME", sys.argv[1] if len(sys.argv) > 1 else "glm-5.2")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

KB_DIR = "/data/agents/kb_data"
MEMORY_BASE = "http://127.0.0.1:8000"
LTM_USER = "kb-agent"

sys.path.insert(0, "/data/agents/memory")
from local_hash_embedding import LocalHashEmbedding  # noqa: E402

EVENTS = []  # consumed by the HTTP sidecar
_EMB = LocalHashEmbedding()

# ---------------- 知识库索引（导入期构建） ----------------

CHUNKS = []  # {"file","head","text","vec"}


def _bigrams(t):
    return {t[i:i + 2] for i in range(len(t) - 1)} | {t[i:i + 3] for i in range(len(t) - 2)}


def _build_index():
    for fp in sorted(glob.glob(os.path.join(KB_DIR, "*.md"))):
        try:
            data = open(fp, "rb").read().decode("utf-8", "replace")
        except OSError:
            continue
        fname = os.path.basename(fp)
        title = ""
        cur_head = ""
        buf = []
        for line in data.splitlines():
            if line.startswith("# ") and not title:
                title = line[2:].strip()
            elif line.startswith("## "):
                _flush(fname, title, cur_head, buf)
                cur_head = line[3:].strip()
                buf = []
            else:
                buf.append(line)
        _flush(fname, title, cur_head, buf)


def _flush(fname, title, head, buf):
    text = "\n".join(buf).strip()
    if not text:
        return
    for piece in _split_len(text, 1400):
        ctx = (title + " > " + head).strip(" >")
        CHUCK_TEXT = piece
        CHUNKS.append({
            "file": fname, "head": ctx or fname, "text": CHUCK_TEXT,
            "vec": _EMB.embed_query(ctx + "\n" + piece[:400]),
            "bg": _bigrams(piece),
        })


def _split_len(text, n):
    paras, out, cur = text.split("\n\n"), [], ""
    for p in paras:
        if len(cur) + len(p) > n and cur:
            out.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p).strip()
    if cur.strip():
        out.append(cur)
    return out


_build_index()

# ---------------- 工具实现 ----------------


def _record(ev):
    EVENTS.append(ev)


def _clean(v) -> str:
    s = str(v if v is not None else "").strip()
    return "" if s.lower() in ("none", "null", "undefined") else s


def _run(cmd, timeout=15):
    import subprocess
    t0 = time.time()
    try:
        p = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, timeout=timeout)
        out = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace").strip()
        rc = p.returncode
    except Exception as e:
        out, rc = "ERROR: %s" % e, 124
    _record({"type": "cmd", "cmd": cmd[:220], "rc": rc,
             "ms": int((time.time() - t0) * 1000), "head": out[:150].replace("\n", " | ")})
    return out or "(no output)"


def kb_docs() -> str:
    """List knowledge-base source files with chunk counts (call this first to see what is known)."""
    per = {}
    for c in CHUNKS:
        per[c["file"]] = per.get(c["file"], 0) + 1
    out = ["knowledge base: %d chunks in %d files" % (len(CHUNKS), len(per))]
    for f, n in sorted(per.items()):
        out.append("- %s (%d chunks)" % (f, n))
    return "\n".join(out)


def kb_search(query: str, top_k: int = 5) -> str:
    """Hybrid-search the local HarmonyOS/K3-pico knowledge base (official board specs, SpacemiT K3 chip & ecosystem, live device hardware snapshot, OHOS porting notes). Returns ranked excerpts with source."""
    q = _clean(query)
    if not q:
        return "ERROR: empty query"
    qv = _EMB.embed_query(q)
    qbg = _bigrams(q)
    scored = []
    for i, c in enumerate(CHUNKS):
        cos = sum(a * b for a, b in zip(qv, c["vec"]))
        overlap = len(qbg & c["bg"]) / max(1, len(qbg))
        scored.append((0.65 * cos + 0.35 * overlap, i))
    scored.sort(reverse=True)
    n = max(1, min(int(top_k) if top_k else 5, 10))
    out = []
    for s, i in scored[:n]:
        c = CHUNKS[i]
        body = c["text"] if len(c["text"]) <= 700 else c["text"][:700] + " ..."
        out.append("[%.3f] %s :: %s\n%s" % (s, c["file"], c["head"], body))
    return "\n\n".join(out) if out else "(no chunk)"


_PROBES = {
    "cpu": "grep -m1 isa /proc/cpuinfo; echo cores=$(nproc); cat /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq 2>/dev/null",
    "mem": "head -3 /proc/meminfo",
    "thermal": "for z in /sys/class/thermal/thermal_zone*; do echo $(cat $z/type 2>/dev/null)=$(( $(cat $z/temp 2>/dev/null) / 1000 ))C; done",
    "storage": "df -h /data; cat /proc/partitions | tail -n +3 | head -6",
    "net": "ifconfig eth0 2>/dev/null | head -3; head -4 /proc/net/route",
    "drm": "ls /sys/class/drm 2>/dev/null | grep -v version; param get const.product.software.version",
    "audio": "cat /proc/asound/cards 2>/dev/null",
}


def device_probe(topic: str = "cpu") -> str:
    """Probe LIVE hardware status on this K3 pico board. topic: cpu|mem|thermal|storage|net|drm|audio|all (no battery on this board)."""
    t = _clean(topic).lower() or "cpu"
    if t == "all":
        return "\n\n".join("[%s]\n%s" % (k, _run(v)) for k, v in _PROBES.items())
    if t not in _PROBES:
        return "ERROR: unknown topic %r, use cpu|mem|thermal|storage|net|drm|audio|all" % t
    return _run(_PROBES[t])


def _post(path, payload, timeout=60):
    req = urllib.request.Request(MEMORY_BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ltm_search(query: str, num: int = 5) -> str:
    """Search long-term memory (cross-session facts about this user/project, stored in jiuwen_memory). Use it to recall user environment, preferences and past conclusions before answering personal-context questions."""
    q = _clean(query)
    if not q:
        return "ERROR: empty query"
    try:
        r = _post("/search_memory/", {"query": q, "user_id": LTM_USER,
                                      "scope_id": "default", "num": int(num) or 5,
                                      "threshold": 0.15})
        hits = r.get("results", [])
        if not hits:
            return "(no long-term memory matched; the user may be new here)"
        out = []
        for h in hits[:6]:
            out.append("[%.3f][%s] %s" % (h.get("score", 0), h.get("type"), (h.get("content") or "")[:160]))
        return "\n".join(out)
    except Exception as e:
        return "memory server unreachable/error: %s" % e


def remember(content: str) -> str:
    """Persist ONE important fact into long-term memory for future sessions (jiuwen_memory extraction). Example: remember('用户的编译服务器是 10.0.50.17 snode7')."""
    c = _clean(content)
    if not c:
        return "ERROR: empty content"
    try:
        r = _post("/add_messages/", {
            "messages": [
                {"role": "user", "content": "请记住这个事实：%s" % c},
                {"role": "assistant", "content": "已记住：%s" % c},
            ],
            "user_id": LTM_USER, "scope_id": "default",
        }, timeout=180)
        _record({"type": "memory_write", "head": c[:120]})
        return "memory add: %s" % r.get("status", r)
    except Exception as e:
        return "memory server unreachable/error: %s" % e


# ---------------- agent 装配 ----------------

def make_tool(tid, desc, func, props, req):
    import functools

    @functools.wraps(func)
    def wrapped(*a, **k):
        t0 = time.time()
        try:
            args_s = json.dumps(k, ensure_ascii=False, default=str)[:300]
        except Exception:
            args_s = str(k)[:300]
        EVENTS.append({"type": "tool_start", "tool": tid, "args": args_s})
        try:
            r = func(*a, **k)
        except Exception as e:
            EVENTS.append({"type": "tool_error", "tool": tid,
                           "detail": str(e)[:300], "ms": int((time.time() - t0) * 1000)})
            raise
        EVENTS.append({"type": "tool_done", "tool": tid,
                       "preview": str(r)[:400], "ms": int((time.time() - t0) * 1000)})
        return r

    return LocalFunction(
        card=ToolCard(id=tid, name=tid, description=desc,
                      input_params={"type": "object",
                                    "properties": props, "required": req}),
        func=wrapped)


KB_TOOLS = [
    make_tool("kb_docs", "List knowledge-base source files and chunk counts.", kb_docs, {}, []),
    make_tool("kb_search",
              "Search the local HarmonyOS/K3 knowledge base (official K3 Pico-ITX specs, SpacemiT K3 chip, device hardware snapshot, OHOS porting notes). Use FIRST for board/chip/OHOS questions.",
              kb_search, {"query": {"type": "string"}, "top_k": {"type": "integer"}}, ["query"]),
    make_tool("device_probe",
              "Probe LIVE hardware on this board. topic: cpu|mem|thermal|storage|net|drm|audio|all.",
              device_probe, {"topic": {"type": "string"}}, []),
    make_tool("ltm_search",
              "Search long-term memory for user/project facts learned in past sessions.",
              ltm_search, {"query": {"type": "string"}, "num": {"type": "integer"}}, ["query"]),
    make_tool("remember",
              "Persist an important user/project fact into long-term memory for future sessions.",
              remember, {"content": {"type": "string"}}, ["content"]),
]

DEFAULT_QUERY = (
    "请作为鸿蒙知识库助手完成三件事："
    "1) kb_search 查 K3 Pico-ITX 的芯片与 AI 算力规格并引用来源文件；"
    "2) device_probe(topic=\"all\") 采集本机实时状态（CPU/内存/温度等）；"
    "3) ltm_search 查询是否有关于我的历史记忆；"
    "最后用中文输出结构化回答：硬件规格（官方）+ 本机实时状态 + 记忆命中情况，"
    "若有值得记住的新信息（如本机实时 IP/温度基线）可调用 remember 保存。"
)

model_client_config = ModelClientConfig(
    client_provider=os.getenv("MODEL_PROVIDER"),
    api_key=os.getenv("API_KEY"),
    api_base=os.getenv("API_BASE"),
    verify_ssl=os.getenv("LLM_SSL_VERIFY").lower() == "true")


def _configure(agent):
    agent.configure(ReActAgentConfig(
        model_name=os.getenv("MODEL_NAME"),
        model_client_config=model_client_config,
        model_config_obj=ModelRequestConfig(model=os.getenv("MODEL_NAME")),
        max_iterations=10))
    for t in KB_TOOLS:
        agent.ability_manager.add(t.card)
    return agent


async def _register_thought_hook(agent):
    counter = {"n": 0}

    async def _on_model_call(ctx):
        try:
            resp = getattr(ctx.inputs, "response", None)
            if resp is None:
                return
            content = getattr(resp, "content", "") or ""
            if isinstance(content, list):
                content = "".join(str(x) for x in content)
            calls = []
            for tc in (getattr(resp, "tool_calls", None) or []):
                nm = getattr(tc, "name", None) or (tc.get("name", "") if isinstance(tc, dict) else "")
                if nm:
                    calls.append(str(nm))
            text = str(content).strip()
            if not text and not calls:
                return
            counter["n"] += 1
            EVENTS.append({"type": "thought", "iter": counter["n"],
                           "text": text[:1200], "plan": ", ".join(calls)[:200]})
        except Exception as e:
            EVENTS.append({"type": "thought_error", "detail": repr(e)[:150]})

    await agent.register_callback(AgentCallbackEvent.AFTER_MODEL_CALL, _on_model_call)
    return agent


async def build_agent():
    """Fresh instrumented agent per run (used by the HTTP sidecar worker thread)."""
    agent = ReActAgent(card=AgentCard(id="ohos_kb_agent", name="ohos_kb_agent",
                                      description="HarmonyOS/K3 knowledge-base agent with memory"))
    _configure(agent)
    await _register_thought_hook(agent)
    return agent


AGENT = _configure(ReActAgent(card=AgentCard(
    id="ohos_kb_agent", name="ohos_kb_agent",
    description="HarmonyOS knowledge base agent")))

for _t in KB_TOOLS:
    Runner.resource_mgr.add_tool(_t)


async def main():
    query = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_QUERY
    result = await Runner.run_agent(agent=AGENT, inputs={"query": query})
    out = result.get("output")
    print("\n===== KbAgent final result =====")
    print(getattr(out, "result", out))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
