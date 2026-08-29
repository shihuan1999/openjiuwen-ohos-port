# -*- coding: UTF-8 -*-
"""对照: 原始 SQL 计数 vs FileMemoryIndex.search vs REST 三层结果。"""
import asyncio
import json
import os
import sqlite3
import sys
import urllib.request

os.chdir("/data/agents/memory")
sys.path.insert(0, "/data/agents/memory")

from local_hash_embedding import LocalHashEmbedding
from jiuwen_memory.foundation.store.index.file_index.file_memory_index import FileMemoryIndex


class AsyncEmb(LocalHashEmbedding):
    async def embed_query(self, text, **kw):
        return super().embed_query(text)

    async def embed_documents(self, texts, **kw):
        return super().embed_documents(list(texts))


async def main():
    c = sqlite3.connect("/data/agents/memory/file_memory_data/memory.db")
    total = c.execute("SELECT count(*) FROM chunks").fetchone()[0]
    frag = c.execute(
        "SELECT count(*) FROM chunks WHERE user_id='kb-test' AND scope_id='default' "
        "AND embedding IS NOT NULL AND type IN ('user_profile','episodic_memory','semantic_memory')"
    ).fetchone()[0]
    types = [r[0] for r in c.execute("SELECT DISTINCT type FROM chunks")]
    print("raw SQL: total=%d fragment_match=%d types=%s" % (total, frag, types))

    idx = FileMemoryIndex(root_dir="/data/agents/memory/file_memory_data",
                          embedding_model=AsyncEmb())
    res = await idx.search(user_id="kb-test", scope_id="default",
                           query="编译服务器 IP", top_k=3)
    print("index.search ->", len(res))
    for doc, s in res:
        print("  %.3f %s" % (s, (doc.text or "")[:40]))


asyncio.run(main())

req = urllib.request.Request(
    "http://127.0.0.1:8000/search_memory/",
    data=json.dumps({"query": "编译服务器 IP", "user_id": "kb-test",
                     "scope_id": "default", "num": 5, "threshold": 0.0}).encode(),
    headers={"Content-Type": "application/json"})
r = json.loads(urllib.request.urlopen(req, timeout=60).read())
print("REST ->", len(r.get("results", [])))
