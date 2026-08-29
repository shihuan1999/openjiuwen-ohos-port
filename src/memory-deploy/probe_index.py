# -*- coding: UTF-8 -*-
"""直接驱动 FileMemoryIndex 定位检索空结果问题。"""
import asyncio
import os
import sys

os.chdir("/data/agents/memory")  # 日志路径 ./logs/ 相对 CWD
sys.path.insert(0, "/data/agents/memory")

from local_hash_embedding import LocalHashEmbedding
from jiuwen_memory.foundation.store.index.file_index.file_memory_index import FileMemoryIndex


class AsyncEmb(LocalHashEmbedding):
    async def embed_query(self, text, **kw):
        return super().embed_query(text)

    async def embed_documents(self, texts, **kw):
        return super().embed_documents(list(texts))


async def main():
    idx = FileMemoryIndex(
        root_dir="/data/agents/memory/file_memory_data",
        embedding_model=AsyncEmb(),
    )
    import sqlite3
    c = sqlite3.connect("/data/agents/memory/file_memory_data/memory.db")
    rows = c.execute(
        "SELECT mem_id, user_id, scope_id, type, substr(text,1,40), "
        "length(embedding), blacklisted FROM chunks"
    ).fetchall()
    print("chunks rows:")
    for r in rows:
        print("  ", r)
    print("files rows:", c.execute("SELECT path, hash FROM files").fetchall())

    for q in ["编译服务器 IP 是多少", "开发板型号", "OpenHarmony"]:
        res = await idx.search(user_id="kb-test", scope_id="default", query=q, top_k=5)
        print("search[%s] -> %d" % (q, len(res)))
        for doc, score in res:
            print("   %.4f %s" % (score, (doc.text or "")[:60]))


asyncio.run(main())
