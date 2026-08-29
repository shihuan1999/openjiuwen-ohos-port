# -*- coding: UTF-8 -*-
"""DeepResearch agent: openJiuwen-DeepSearch workflow over the LOCAL knowledge
base (custom local search engine, no external web-search API needed).

Sidecar contract (new-style): module exposes
  DEFAULT_QUERY / EVENTS / async arun(query, record) -> str(final markdown report)
"""
import asyncio
import copy
import json
import os
import time
import uuid

os.environ.setdefault("API_BASE", "http://127.0.0.1:16000/v1")
os.environ.setdefault("API_KEY", "sk-YOUR_API_KEY")
os.environ.setdefault("MODEL_PROVIDER", "openai")
os.environ.setdefault("MODEL_NAME", "glm-5.2")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

EVENTS = []
DEFAULT_QUERY = "SpacemiT K3 芯片在 OpenHarmony 生态中的定位与技术优势是什么？请基于本地知识库给出带引用的研究简报。"

LOCAL_SEARCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb_local_search.py")


def _record(ev):
    EVENTS.append(ev)


def _llm_role():
    # one FRESH bytearray per role: create_llm_obj's finally-block zero_secret
    # wipes the bytearray IN PLACE, and pydantic would otherwise share one
    # instance across all four llm_config entries (we saw Bearer \x00\x00...)
    return {
        "model_name": os.getenv("MODEL_NAME", "glm-5.2"),
        "model_type": "openai",
        "base_url": os.getenv("API_BASE", "http://127.0.0.1:16000/v1"),
        "api_key": bytearray(os.getenv("API_KEY", "").encode()),
        # upstream llm_model_factory never forwards model_name into
        # ModelRequestConfig; hyper_parameters ARE setattr'ed there, so we
        # piggyback the model name through it (zero-intrusion workaround)
        "hyper_parameters": {"model_name": os.getenv("MODEL_NAME", "glm-5.2")},
        "timeout": 300,
        "max_tries": 3,
    }


def build_config():
    return {
        "execute_mode": "general",
        "execution_method": "parallel",
        "workflow_human_in_the_loop": False,
        "outline_interaction_enabled": False,
        "outline_interaction_max_rounds": 1,
        "outliner_max_section_num": 3,
        "source_tracer_research_trace_source_switch": True,
        "source_tracer_generated_citation_switch": True,
        "source_tracer_infer_switch": False,
        "info_collector_search_method": "local",
        "info_collector_webpage_enrich_enable": False,
        "search_mode": "research",
        "llm_config": {"general": _llm_role(), "plan_understanding": _llm_role(),
                       "info_collecting": _llm_role(), "writing_checking": _llm_role()},
        "local_search_engine_config": {
            "search_engine_name": "custom",
            "search_mode": "mix",
            "max_local_search_results": 5,
            "recall_threshold": 0.3,
        },
        "custom_local_search_config": {
            "custom_local_search_file": LOCAL_SEARCH_FILE,
            "custom_local_search_func": "KBLocalSearch",
        },
        "search_workflow_per_question_params": {
            "max_workers": 2, "time_limit": 900, "actions_explored_limit": 40,
            "retry_count_on_empty_action_space": 2,
        },
    }


async def arun(query, record=_record, timeout=1200):
    record({"type": "cmd", "cmd": "deepsearch workflow start", "rc": 0, "head": query[:120]})
    from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import parse_endnode_content

    t0 = time.time()
    cfg = build_config()
    agent = AgentFactory().create_agent(copy.deepcopy(cfg))
    report_parts = []
    node_seen = {}

    async def _drive():
        async for chunk in agent.run(message=query, conversation_id=uuid.uuid4().hex,
                                     report_template="", interrupt_feedback="",
                                     agent_config=cfg):
            try:
                data = json.loads(chunk) if isinstance(chunk, str) else chunk
            except Exception:
                data = {"raw": str(chunk)[:120]}
            node = data.get("node") or data.get("id") or data.get("source") or "step"
            node_seen[node] = node_seen.get(node, 0) + 1
            if node_seen[node] <= 2:  # only first two events per node keep the feed tight
                head = str(data.get("content") or data.get("message") or "")[:110].replace("\n", " ")
                record({"type": "node", "node": str(node), "head": head})
            rep = parse_endnode_content(data)
            if rep:
                if isinstance(rep, dict):
                    rep = rep.get("response_content") or rep.get("content") \
                        or json.dumps(rep, ensure_ascii=False)
                report_parts.append(rep if isinstance(rep, str) else str(rep))

    try:
        await asyncio.wait_for(_drive(), timeout=timeout)
    except asyncio.TimeoutError:
        record({"type": "tool_error", "tool": "deepsearch", "detail": "timeout %ss" % timeout})
    report = "\n\n".join(report_parts).strip()
    if not report:
        report = "(deepsearch 未产出终稿报告，请缩小问题范围后重试；事件流见上方节点记录)"
    record({"type": "done", "head": "deepsearch finished in %.0fs" % (time.time() - t0)})
    return report
