# -*- coding: UTF-8 -*-
"""进程内复现服务器装配并直接调 search_user_mem,定位空结果。"""
import asyncio
import os
import sys

os.chdir("/data/agents/memory")
sys.path.insert(0, "/data/agents/memory")

import jiuwen_memory.server.memory_server as ms
from local_hash_embedding import LocalHashEmbedding
from sqlite_vector_store import SqliteVectorStore


class LocalAPIEmbedding(ms.APIEmbedding):
    def __init__(self, config=None, **kwargs):
        self.config = config
        self.model_name = (getattr(config, "model_name", "") or "") or "local-hash-384"
        self._inner = LocalHashEmbedding()

    async def embed_query(self, text, **kwargs):
        return self._inner.embed_query(text)

    async def embed_documents(self, texts, batch_size=None, **kwargs):
        return self._inner.embed_documents(list(texts))


ms.APIEmbedding = LocalAPIEmbedding
ms.create_vector_store = lambda: SqliteVectorStore("/data/agents/memory/data/vecstore.db")


async def main():
    await ms.startup_event()
    eng = ms.memory_engine
    print("index:", type(eng.memory_index).__name__)
    print("fragment_type:", getattr(eng, "fragment_type", None))
    sm = getattr(eng, "search_manager", None)
    print("managers:", list(sm.managers.keys()) if sm else None)
    try:
        res = await eng.search_user_mem(query="编译服务器 IP 是多少", num=5, user_id="kb-test", scope_id="default", threshold=0.0)
        print("inproc search_user_mem ->", len(res))
        for r in res:
            print("  %.4f [%s] %s" % (r.score, r.mem_info.type, (r.mem_info.content or "")[:50]))
    except Exception:
        import traceback
        traceback.print_exc()

    # 对照:打到运行中的服务器进程
    import json
    import urllib.request
    req = urllib.request.Request(
        "http://127.0.0.1:8000/search_memory/",
        data=json.dumps({"query": "编译服务器 IP 是多少", "user_id": "kb-test", "scope_id": "default", "num": 5, "threshold": 0.0}).encode(),
        headers={"Content-Type": "application/json"},
    )
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    print("REST search ->", len(r.get("results", [])), json.dumps(r, ensure_ascii=False)[:200])

    await ms.shutdown_event()


asyncio.run(main())
