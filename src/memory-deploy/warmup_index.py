# -*- coding: UTF-8 -*-
"""memory_server 检索 warm-up(已验证配方,3/3 有效).

怪癖: memory_server 启动后 REST search_memory 恒空(写入正常),直到本脚本的三步
序列在外部进程跑一遍后立即恢复: ①裸 sqlite 读 memory.db ②FileMemoryIndex 初始化
③带真实命中的 search。缺一不可(二分实测: 仅①/仅①②/②③ 均无效)。根因未定
(疑 WAL/懒同步跨进程状态),影响仅"启动后到 warm-up 前"的检索,不丢数据。
boot 流程在 server /health 就绪后自动执行本脚本。
"""
import asyncio
import os
import sqlite3
import sys

os.chdir("/data/agents/memory")
sys.path.insert(0, "/data/agents/memory")

from local_hash_embedding import LocalHashEmbedding
from jiuwen_memory.foundation.store.index.file_index.file_memory_index import FileMemoryIndex


class AsyncEmb(LocalHashEmbedding):
    async def embed_query(self, text, **kw):
        return super().embed_query(text)

    async def embed_documents(self, texts, **kw):
        return super().embed_documents(list(texts))


def step1_raw_read():
    c = sqlite3.connect("/data/agents/memory/file_memory_data/memory.db")
    n = c.execute(
        "SELECT count(*) FROM chunks WHERE user_id='kb-test' AND scope_id='default' "
        "AND embedding IS NOT NULL").fetchone()[0]
    c.close()
    return n


async def main():
    n = step1_raw_read()
    idx = FileMemoryIndex(root_dir="/data/agents/memory/file_memory_data",
                          embedding_model=AsyncEmb())
    hits = await idx.search(user_id="kb-test", scope_id="default",
                            query="编译服务器 OpenHarmony K3", top_k=3)
    print("warmup: raw_rows=%d hits=%d" % (n, len(hits)))


asyncio.run(main())
