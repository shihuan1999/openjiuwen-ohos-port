# -*- coding: UTF-8 -*-
"""openjiuwen sidecar v3: Agent Store + Knowledge Base + cloud sync over HTTP.

v3 adds (v2 agent-run contract kept as-is for the ArkTS UI):
  GET  /api/store                 -> agent store catalog (19 org components)
  POST /api/store/toggle          -> enable/disable an installed agent
  GET  /api/kb/...                -> docs/notes/search/categories/tags/history/stats
  POST /api/sync/now              -> device<->cloud sync (LWW)
  GET  /api/sync/status           -> sync status + logs

All agent runs are dispatched through the agent-dx SDK executor contract
(store_engine.ModuleAgentExecutor), one at a time in a worker thread.
"""
import asyncio
import os
import sys
import threading
import time
import traceback
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/data/agents")
sys.path.insert(0, "/data/agents/memory")

# LLM goes through the PC relay (USB rport tunnel) by default; direct URL
# still wins if the operator exports API_BASE before launching the sidecar.
os.environ.setdefault("API_BASE", "http://127.0.0.1:16000/v1")
os.environ.setdefault("API_KEY", "sk-YOUR_API_KEY")
os.environ.setdefault("MODEL_PROVIDER", "openai")
os.environ.setdefault("MODEL_NAME", "glm-5.2")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

from aiohttp import web

import kb_store
import store_engine
import sync_client

RUNNABLE = ["kb", "diag", "perf", "research", "career"]
AGENTS = {}          # lazy: kind -> module
TASKS = {}
RUNNING = {"kind": None, "id": None}
LOCK = threading.Lock()


def get_module(kind):
    if kind not in AGENTS:
        name = store_engine.LOCAL_MODULES[kind]
        AGENTS[kind] = __import__(name)
    return AGENTS[kind]


def _worker(task):
    async def execute():
        mod = get_module(task["agent"])
        record = lambda ev: mod.EVENTS.append(ev)  # noqa: E731
        out = await store_engine.dispatch(mod, task["agent"], task["query"], record)
        task["result"] = str(out)

    try:
        asyncio.run(execute())
        task["state"] = "done"
    except Exception as e:
        task["error"] = "".join(traceback.format_exception_only(type(e), e))[-800:]
        traceback.print_exc()
        task["state"] = "error"
    finally:
        task["t1"] = time.time()
        with LOCK:
            if RUNNING["id"] == task["id"]:
                RUNNING["kind"], RUNNING["id"] = None, None


# ---------------- agent task endpoints (v2 contract) ----------------

async def h_health(request):
    recent = {}
    for t in TASKS.values():
        prev = recent.get(t["agent"])
        if prev is None or TASKS[prev]["t0"] <= t["t0"]:
            recent[t["agent"]] = t["id"]
    cat = store_engine.catalog()
    return web.json_response({
        "ok": True,
        "agents": [k for k in RUNNABLE if store_engine.is_enabled(k)],
        "model": os.getenv("MODEL_NAME"),
        "llm_base": os.getenv("API_BASE"),
        "tasks": len(TASKS),
        "running": RUNNING["id"],
        "running_kind": RUNNING["kind"],
        "recent": recent,
        "store": cat["stats"],
    })


async def h_run(request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    kind = str(body.get("agent", "")).strip()
    if kind not in RUNNABLE or kind not in store_engine.LOCAL_MODULES:
        return web.json_response(
            {"error": "agent must be one of %s" % RUNNABLE}, status=400)
    if not store_engine.is_enabled(kind):
        return web.json_response({"error": "agent %s is disabled in store" % kind}, status=409)
    mod = get_module(kind)
    query = str(body.get("query", "") or "").strip() or mod.DEFAULT_QUERY
    with LOCK:
        if RUNNING["id"] is not None:
            return web.json_response(
                {"error": "another task is running (%s)" % RUNNING["kind"],
                 "id": RUNNING["id"]}, status=409)
        tid = uuid.uuid4().hex[:12]
        TASKS[tid] = {"id": tid, "agent": kind, "query": query, "state": "running",
                      "events": [], "ev_cursor": len(mod.EVENTS),
                      "result": "", "error": "", "t0": time.time(), "t1": 0.0}
        RUNNING["kind"], RUNNING["id"] = kind, tid
    threading.Thread(target=_worker, args=(TASKS[tid],), daemon=True).start()
    return web.json_response({"id": tid})


async def h_task(request):
    tid = request.match_info["id"]
    task = TASKS.get(tid)
    if task is None:
        return web.json_response({"error": "unknown id"}, status=404)
    if task["agent"] in AGENTS:
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


# ---------------- agent store endpoints ----------------

async def h_store(request):
    extra = None
    try:
        r = sync_client._req("/cloud/api/store/catalog", timeout=6)
        extra = r.get("items")
    except Exception:
        pass
    return web.json_response(store_engine.catalog(extra_cloud=extra))


async def h_store_toggle(request):
    body = await request.json()
    aid = str(body.get("id", "")).strip()
    if not any(c["id"] == aid for c in store_engine.CATALOG):
        return web.json_response({"error": "unknown agent id"}, status=404)
    flag = bool(body.get("enabled", True))
    store_engine.set_enabled(aid, flag)
    return web.json_response({"id": aid, "enabled": store_engine.is_enabled(aid)})


# ---------------- knowledge base endpoints ----------------

async def h_kb_docs(request):
    q = request.query
    docs = kb_store.list_docs(
        category=q.get("category"), tag=q.get("tag"),
        favorite=q.get("favorite"), q=q.get("q"),
        limit=q.get("limit", 200))
    return web.json_response({"docs": docs})


async def h_kb_doc_new(request):
    body = await request.json()
    title = str(body.get("title", "")).strip()
    if not title:
        return web.json_response({"error": "title required"}, status=400)
    doc = kb_store.create_doc(
        title=title, content=str(body.get("content", "")),
        category=str(body.get("category", "")), tags=body.get("tags") or [],
        summary=str(body.get("summary", "")))
    sync_client.log("new doc: %s" % title)
    return web.json_response({"doc": doc})


async def h_kb_doc(request):
    did = request.match_info["id"]
    doc = kb_store.get_doc(did)
    if not doc:
        return web.json_response({"error": "not found"}, status=404)
    if request.method == "GET":
        kb_store.add_history(did)
        doc["notes"] = kb_store.list_notes(doc_id=did)
        return web.json_response({"doc": doc})
    if request.method == "DELETE":
        kb_store.delete_doc(did)
        sync_client.log("delete doc: %s" % did)
        return web.json_response({"ok": True})
    body = await request.json()
    doc = kb_store.update_doc(
        did, title=body.get("title"), content=body.get("content"),
        category=body.get("category"), tags=body.get("tags"),
        summary=body.get("summary"))
    sync_client.log("update doc: %s" % did)
    return web.json_response({"doc": doc})


async def h_kb_favorite(request):
    did = request.match_info["id"]
    fav = kb_store.toggle_favorite(did)
    return web.json_response({"id": did, "favorite": fav})


async def h_kb_search(request):
    q = request.query
    hits = kb_store.search(q.get("q", ""), mode=q.get("mode", "mix"),
                           top_k=q.get("top_k", 8))
    return web.json_response({"hits": hits})


async def h_kb_notes(request):
    q = request.query
    return web.json_response({"notes": kb_store.list_notes(
        doc_id=q.get("doc_id"), q=q.get("q"))})


async def h_kb_note_new(request):
    body = await request.json()
    if not body.get("doc_id"):
        return web.json_response({"error": "doc_id required"}, status=400)
    note = kb_store.add_note(body["doc_id"], body.get("quote", ""),
                             body.get("note", ""), body.get("tags"))
    sync_client.log("new note on %s" % body["doc_id"])
    return web.json_response({"note": note})


async def h_kb_note_del(request):
    kb_store.delete_note(request.match_info["id"])
    return web.json_response({"ok": True})


async def h_kb_meta(request):
    return web.json_response({
        "categories": kb_store.categories(),
        "tags": kb_store.tags if hasattr(kb_store, "tags") else kb_store.all_tags(),
        "history": kb_store.list_history(),
        "stats": kb_store.stats(),
    })


# ---------------- sync endpoints ----------------

async def h_sync_now(request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    direction = body.get("direction", "both")
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(None, sync_client.sync_now, direction)
    return web.json_response(res)


async def h_sync_status(request):
    return web.json_response(sync_client.status())


async def h_sync_url(request):
    body = await request.json()
    url = str(body.get("url", "")).strip().rstrip("/")
    if not url.startswith("http"):
        return web.json_response({"error": "url must start with http"}, status=400)
    sync_client.set_cloud_url(url)
    return web.json_response({"cloud_url": url})


async def h_index(request):
    html = ("<h2>openjiuwen sidecar v3 — Agent Store + KB + Sync</h2>"
            "<p>agents: kb / diag / perf / research(deepsearch) / career — model %s via %s</p>"
            "<p>endpoints: /api/store /api/kb/* /api/sync/* /api/run /api/task</p>"
            % (os.getenv("MODEL_NAME"), os.getenv("API_BASE")))
    return web.Response(text=html, content_type="text/html")


def main():
    kb_store.import_corpus()
    app = web.Application()
    r = app.router
    r.add_get("/", h_index)
    r.add_get("/api/health", h_health)
    r.add_post("/api/run", h_run)
    r.add_get("/api/task/{id}", h_task)
    r.add_get("/api/store", h_store)
    r.add_post("/api/store/toggle", h_store_toggle)
    r.add_get("/api/kb/docs", h_kb_docs)
    r.add_post("/api/kb/docs", h_kb_doc_new)
    r.add_get("/api/kb/doc/{id}", h_kb_doc)
    r.add_route("PUT", "/api/kb/doc/{id}", h_kb_doc)
    r.add_route("DELETE", "/api/kb/doc/{id}", h_kb_doc)
    r.add_post("/api/kb/doc/{id}/favorite", h_kb_favorite)
    r.add_get("/api/kb/search", h_kb_search)
    r.add_get("/api/kb/notes", h_kb_notes)
    r.add_post("/api/kb/notes", h_kb_note_new)
    r.add_route("DELETE", "/api/kb/note/{id}", h_kb_note_del)
    r.add_get("/api/kb/meta", h_kb_meta)
    r.add_post("/api/sync/now", h_sync_now)
    r.add_get("/api/sync/status", h_sync_status)
    r.add_post("/api/sync/cloud-url", h_sync_url)
    print("sidecar v3 listening on 0.0.0.0:8765", flush=True)
    web.run_app(app, host="0.0.0.0", port=8765, print=None)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
