# -*- coding: UTF-8 -*-
"""变体B: 纯 sqlite 读 + FileMemoryIndex 初始化(不 search)。"""
import os
import sqlite3
import sys

os.chdir("/data/agents/memory")
sys.path.insert(0, "/data/agents/memory")

c = sqlite3.connect("/data/agents/memory/file_memory_data/memory.db")
print("B rows:", c.execute(
    "SELECT count(*) FROM chunks WHERE user_id='kb-test' AND scope_id='default' "
    "AND embedding IS NOT NULL").fetchone()[0])
c.close()

from local_hash_embedding import LocalHashEmbedding
from jiuwen_memory.foundation.store.index.file_index.file_memory_index import FileMemoryIndex


class AsyncEmb(LocalHashEmbedding):
    async def embed_query(self, text, **kw):
        return super().embed_query(text)

    async def embed_documents(self, texts, **kw):
        return super().embed_documents(list(texts))


import asyncio


async def m():
    idx = FileMemoryIndex(root_dir="/data/agents/memory/file_memory_data",
                          embedding_model=AsyncEmb())
    print("B init ok")


asyncio.run(m())
