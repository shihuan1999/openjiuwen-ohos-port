# -*- coding: UTF-8 -*-
"""Agent Store engine for the openJiuwen device sidecar.

- CATALOG: full openJiuwen-ai org component directory (19 repos) with
  on-device status: installed / cloud / na
- execution layer implements the agent-dx SDK (yuanrong.agentruntime)
  AgentExecutor contract — every sidecar run is dispatched through a dx
  executor and returns Complete(...)
- enable/disable + run stats persisted to /data/agents/store/state.json
"""
import asyncio
import json
import os
import threading
import time

from yuanrong.agentruntime import AgentExecutor, Complete, InputRequired
from yuanrong.agentruntime.context import RequestContext, SessionContext

STORE_DIR = "/data/agents/store"
STATE_PATH = os.path.join(STORE_DIR, "state.json")

# id -> module name on device (absent = installed-as-infrastructure, not runnable)
LOCAL_MODULES = {
    "kb": "kb_agent",
    "diag": "app_diag_agent",
    "perf": "perf_probe_agent",
    "research": "deepresearch_agent",
    "career": "career_agent",
}

CATALOG = [
    {"id": "agent-core", "name": "agent-core", "repo": "openJiuwen-ai/agent-core",
     "category": "核心框架", "status": "installed",
     "desc": "Agent 开发/运行/优化/演进全栈 SDK（本机 openjiuwen 0.1.17）",
     "deps": "已随设备 Python 3.12 闭包部署", "capabilities": ["ReActAgent", "WorkflowAgent", "Runner"]},
    {"id": "agent-memory", "name": "agent-memory", "repo": "openJiuwen-ai/agent-memory",
     "category": "记忆", "status": "installed",
     "desc": "Agent 长期记忆：抽取/存储/检索/迁移（memory_server :8000）",
     "deps": "本地哈希 embedding + SQLite 向量库（自研适配）", "capabilities": ["add_messages", "search_memory"]},
    {"id": "memory-plugins", "name": "agent-memory-plugin", "repo": "openJiuwen-ai/agent-memory",
     "category": "记忆", "status": "installed",
     "desc": "OpenClaw/code_agent/hermes 三形态记忆插件（REST 接入，落地为 kb agent 的 ltm 工具）",
     "deps": "复用 memory_server", "capabilities": ["ltm_search", "remember"]},
    {"id": "deepsearch", "name": "DeepSearch", "repo": "openJiuwen-ai/deepsearch",
     "category": "知识检索", "status": "installed",
     "desc": "知识增强深度研究引擎：查询规划/多agent协同/片段级引用溯源/报告生成（dev 分支，本地知识库模式）",
     "deps": "纯 py 依赖离线装 + pypdfium2/pandas stub + 自定义本地检索引擎",
     "capabilities": ["本地融合检索", "大纲规划", "溯源引用", "研究报告"]},
    {"id": "career", "name": "CareerSim-BDCI26", "repo": "openJiuwen-ai/CareerSim-BDCI26",
     "category": "仿真", "status": "installed",
     "desc": "CCF BDCI 2026 职场生存挑战：career-emulator 纯 py 引擎 + LLM 直接驱动 GameEngine",
     "deps": "career-emulator(pure wheel) + msgpack 纯 py fallback",
     "capabilities": ["new_game", "observe", "take_action"]},
    {"id": "dx-sdk", "name": "agent-dx SDK", "repo": "openJiuwen-ai/agent-dx",
     "category": "运行时", "status": "installed",
     "desc": "Agent 分布式执行器 Python SDK（FaaS 式 AgentExecutor/EventLog/SessionContext）——本商店的执行层契约",
     "deps": "零三方依赖（纯标准库）", "capabilities": ["AgentExecutor", "Complete", "RequestContext"]},
    {"id": "kb", "name": "KB Agent", "repo": "device-local",
     "category": "知识检索", "status": "installed",
     "desc": "鸿蒙知识库 agent：kb_search 混合检索 + device_probe 实时探测 + 长期记忆",
     "deps": "设备原生", "capabilities": ["kb_search", "kb_docs", "device_probe", "ltm_search", "remember"]},
    {"id": "diag", "name": "App Diagnoser", "repo": "device-local",
     "category": "诊断", "status": "installed",
     "desc": "openJiuwen 应用诊断 agent（HAP/系统日志/崩溃分析）", "deps": "设备原生",
     "capabilities": ["hilog 分析", "崩溃栈解读"]},
    {"id": "perf", "name": "Perf Probe", "repo": "device-local",
     "category": "诊断", "status": "installed",
     "desc": "设备性能体检 agent（CPU/内存/IO/温度压力探测）", "deps": "设备原生",
     "capabilities": ["SP_daemon", "实时指标"]},
    {"id": "jiuwenswarm", "name": "jiuwenswarm", "repo": "openJiuwen-ai/jiuwenswarm",
     "category": "核心框架", "status": "cloud",
     "desc": "指尖智能体：多渠道接入(Telegram/Discord/Slack/飞书/企微)的个人 AI 助理",
     "deps": "chromadb/faiss/playwright/sqlite-vec 等重型依赖，不满足零额外依赖约束",
     "capabilities": ["多渠道消息", "个人助理"]},
    {"id": "skillhub", "name": "skillhub", "repo": "openJiuwen-ai/skillhub",
     "category": "生态", "status": "cloud",
     "desc": "技能托管与分发平台（marketplace + skill-runner），其 skill 清单格式为本商店目录设计参考",
     "deps": "mysql/redis/faiss/k8s，服务端组件", "capabilities": ["skill 市场", "分发"]},
    {"id": "agent-runtime", "name": "agent-runtime", "repo": "openJiuwen-ai/agent-runtime",
     "category": "运行时", "status": "cloud",
     "desc": "Agent 运行时与部署管理平台（dev→production）",
     "deps": "k8s/mysql/redis/rabbitmq，云端部署组件", "capabilities": ["部署编排"]},
    {"id": "agent-studio", "name": "agent-studio", "repo": "openJiuwen-ai/agent-studio",
     "category": "平台", "status": "na",
     "desc": "零代码/低代码可视化开发与编排平台（Java）",
     "deps": "JVM 运行时，设备不适用", "capabilities": ["可视化编排"]},
    {"id": "agent-core-java", "name": "agent-core-java", "repo": "openJiuwen-ai/agent-core-java",
     "category": "核心框架", "status": "na",
     "desc": "agent-core 的 Java 版 SDK", "deps": "JVM 运行时", "capabilities": ["Java SDK"]},
    {"id": "agent-runtime-java", "name": "agent-runtime-java", "repo": "openJiuwen-ai/agent-runtime-java",
     "category": "运行时", "status": "na",
     "desc": "分布式 Agent 运行时 Java 实现（Agent Server）", "deps": "JVM 运行时", "capabilities": ["Agent Server"]},
    {"id": "jiuwensymbiosis", "name": "jiuwensymbiosis", "repo": "openJiuwen-ai/jiuwensymbiosis",
     "category": "具身智能", "status": "cloud",
     "desc": "物理 AI 助理框架（机械臂/相机/语音，基于 agent-core）",
     "deps": "scipy/opencv/torch/pyrealsense2，需机器人本体", "capabilities": ["ROS2", "臂 IK"]},
    {"id": "agent-protocol", "name": "agent-protocol", "repo": "openJiuwen-ai/agent-protocol",
     "category": "协议", "status": "cloud",
     "desc": "Agent 通信协议实现（MCP/A2A 的 C++ SDK）",
     "deps": "C++ 交叉编译 + 协议对端，规划中", "capabilities": ["MCP", "A2A"]},
    {"id": "agent-tools", "name": "agent-tools", "repo": "openJiuwen-ai/agent-tools",
     "category": "算子", "status": "na",
     "desc": "infer_router(KV Cache 感知路由)/vllm-affinity：vLLM/SGLang GPU 集群服务端组件",
     "deps": "GPU 推理集群，边缘设备不适用", "capabilities": ["推理路由"]},
    {"id": "relay", "name": "relay", "repo": "openJiuwen-ai/relay",
     "category": "平台", "status": "cloud",
     "desc": "多 agent 协作平台（开发/代码评审/任务管理，TypeScript）",
     "deps": "Node.js 运行时", "capabilities": ["协作平台"]},
]

_LOCK = threading.RLock()
_STATE = None


def _load_state():
    global _STATE
    with _LOCK:
        if _STATE is None:
            try:
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    _STATE = json.load(f)
            except Exception:
                _STATE = {"enabled": {}, "runs": {}, "last_run": {}}
        return _STATE


def _save_state():
    with _LOCK:
        os.makedirs(STORE_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(_load_state(), f, ensure_ascii=False, indent=1)


def is_enabled(agent_id):
    st = _load_state()
    if agent_id in st["enabled"]:
        return bool(st["enabled"][agent_id])
    return True  # default enabled


def set_enabled(agent_id, flag):
    _load_state()["enabled"][agent_id] = bool(flag)
    _save_state()
    return is_enabled(agent_id)


def note_run(agent_id):
    st = _load_state()
    st["runs"][agent_id] = st["runs"].get(agent_id, 0) + 1
    st["last_run"][agent_id] = time.time()
    _save_state()


class ModuleAgentExecutor(AgentExecutor):
    """agent-dx executor wrapping one on-device agent module.

    legacy modules: build_agent() + Runner (diag/perf/kb)
    new modules:    async arun(query, record) (research/career)
    """

    def __init__(self, agent_id, module):
        self.agent_id = agent_id
        self.module = module

    async def init(self, session_context) -> None:
        if not hasattr(self.module, "DEFAULT_QUERY"):
            raise RuntimeError("agent module %s lacks DEFAULT_QUERY" % self.agent_id)

    async def execute(self, request_context) -> Complete:
        msg = request_context.input.message  # {"query": str, "record": callable}
        query, record = msg.get("query"), msg.get("record")
        mod = self.module
        note_run(self.agent_id)
        if hasattr(mod, "arun"):
            rec = record or (lambda ev: mod.EVENTS.append(ev))
            return Complete(await mod.arun(query, rec))
        from openjiuwen.core.runner.runner import Runner
        agent = await mod.build_agent()
        result = await Runner.run_agent(agent=agent, inputs={"query": query})
        out = result.get("output")
        return Complete(str(getattr(out, "result", out)))


class _NullOutput:
    pass


def dispatch(mod, agent_id, query, record):
    """Run one agent through the agent-dx execution contract (async)."""
    exe = ModuleAgentExecutor(agent_id, mod)
    ctx = SessionContext("store-%s" % agent_id, None)
    rc = RequestContext(ctx, turn_id="%d" % int(time.time() * 1000),
                        message={"query": query, "record": record},
                        output=_NullOutput())

    async def _go():
        await exe.init(ctx)
        res = await exe.execute(rc)
        if isinstance(res, Complete):
            return res.value
        if isinstance(res, InputRequired):
            return "[input required] %s" % res.value
        return str(res)

    return _go()


def catalog(extra_cloud=None):
    st = _load_state()
    items = []
    for c in CATALOG:
        it = dict(c)
        lid = c["id"]
        it["local_module"] = LOCAL_MODULES.get(lid)
        it["runnable"] = bool(it["local_module"]) and it["status"] == "installed"
        it["enabled"] = is_enabled(lid) if it["runnable"] else False
        it["run_count"] = st["runs"].get(lid, 0)
        it["last_run_at"] = st["last_run"].get(lid, 0)
        items.append(it)
    if extra_cloud:
        for c in extra_cloud:
            if not any(i["id"] == c.get("id") for i in items):
                items.append(c)
    stats = {
        "installed": sum(1 for i in items if i["status"] == "installed"),
        "cloud": sum(1 for i in items if i["status"] == "cloud"),
        "na": sum(1 for i in items if i["status"] == "na"),
        "total": len(items),
    }
    return {"items": items, "stats": stats}
