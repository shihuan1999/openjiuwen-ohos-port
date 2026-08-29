[1][18:37:59] Not support std mode
"""openjiuwen sidecar v2: expose on-device agents over HTTP for the ArkTS UI.

v2: agent runs execute in a worker thread with a FRESH instrumented agent
(build_agent() registers an AFTER_MODEL_CALL hook that streams LLM reasoning
into EVENTS), so blocking tool subprocesses never stall the HTTP loop.
Endpoints:
  GET  /api/health      -> liveness + per-agent recent task ids
  POST /api/run         {"agent": "diag"|"perf", "query": "..."} -> {"id"}
  GET  /api/task/<id>   -> {state, events(cmd/thought/tool_*), result, error}
"""
import asyncio
import os
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("API_BASE", "https://api.rvcompute.com:60000/v1")
os.environ.setdefault("API_KEY", "sk-YOUR_API_KEY")
os.environ.setdefault("MODEL_PROVIDER", "openai")
os.environ.setdefault("MODEL_NAME", "glm-5.2")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

from aiohttp import web

import app_diag_agent as diag_mod
import perf_probe_agent as perf_mod
from openjiuwen.core.runner.runner import Runner

import kb_agent as kb_mod
AGENTS = {"diag": diag_mod, "perf": perf_mod, "kb": kb_mod}
TASKS = {}
RUNNING = {"kind": None, "id": None}
LOCK = threading.Lock()


def _worker(task):
    async def execute():
        agent = await AGENTS[task["agent"]].build_agent()
        result = await Runner.run_agent(agent=agent, inputs={"query": task["query"]})
        out = result.get("output")
        task["result"] = str(getattr(out, "result", out))
        task["state"] = "done"

    try:
        asyncio.run(execute())
    except Exception as e:
        import traceback
        task["error"] = "".join(traceback.format_exception_only(type(e), e))[-800:]
        traceback.print_exc()
        task["state"] = "error"
    finally:
        task["t1"] = time.time()
        with LOCK:
            if RUNNING["id"] == task["id"]:
                RUNNING["kind"], RUNNING["id"] = None, None


async def h_health(request):
    recent = {}
    for t in TASKS.values():
        prev = recent.get(t["agent"])
        if prev is None or TASKS[prev]["t0"] <= t["t0"]:
            recent[t["agent"]] = t["id"]
    return web.json_response({
        "ok": True,
        "agents": sorted(AGENTS.keys()),
        "model": os.getenv("MODEL_NAME"),
        "tasks": len(TASKS),
        "running": RUNNING["id"],
        "running_kind": RUNNING["kind"],
        "recent": recent,
    })


async def h_run(request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    kind = str(body.get("agent", "")).strip()
    if kind not in AGENTS:
        return web.json_response(
            {"error": "agent must be one of %s" % sorted(AGENTS)}, status=400)
    query = str(body.get("query", "") or "").strip() or AGENTS[kind].DEFAULT_QUERY
    with LOCK:
        if RUNNING["id"] is not None:
            return web.json_response(
                {"error": "another task is running (%s)" % RUNNING["kind"],
                 "id": RUNNING["id"]}, status=409)
        tid = uuid.uuid4().hex[:12]
        TASKS[tid] = {"id": tid, "agent": kind, "query": query, "state": "running",
                      "events": [], "ev_cursor": len(AGENTS[kind].EVENTS),
                      "result": "", "error": "", "t0": time.time(), "t1": 0.0}
        RUNNING["kind"], RUNNING["id"] = kind, tid
    threading.Thread(target=_worker, args=(TASKS[tid],), daemon=True).start()
    return web.json_response({"id": tid})


async def h_task(request):
    tid = request.match_info["id"]
    task = TASKS.get(tid)
    if task is None:
        return web.json_response({"error": "unknown id"}, status=404)
    mod = AGENTS[task["agent"]]
    cur = task["ev_cursor"]
    fresh = mod.EVENTS[cur:]
    task["ev_cursor"] += len(fresh)
    task["events"].extend(fresh)
    return web.json_response({
        "id": tid, "agent": task["agent"], "state": task["state"],
        "query": task["query"], "events": task["events"][-300:],
        "result": task["result"], "error": task["error"],
        "elapsed": round((task["t1"] or time.time()) - task["t0"], 1),
    })


async def h_index(request):
    html = ("<h2>openjiuwen sidecar v2</h2>"
            "<p>agents: diag(app diagnosis) / perf(probe) — model glm-5.2</p>")
    return web.Response(text=html, content_type="text/html")


def main():
    app = web.Application()
    app.router.add_get("/", h_index)
    app.router.add_get("/api/health", h_health)
    app.router.add_post("/api/run", h_run)
    app.router.add_get("/api/task/{id}", h_task)
    print("sidecar v2 listening on 0.0.0.0:8765", flush=True)
    web.run_app(app, host="0.0.0.0", port=8765, print=None)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
