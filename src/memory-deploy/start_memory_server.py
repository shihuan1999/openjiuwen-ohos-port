# -*- coding: UTF-8 -*-
"""设备端 memory_server 启动器。

rvcompute 网关无 embedding 模型，这里把 memory_server 里的 APIEmbedding
替换为本地哈希 embedding(离线、确定性)，其余装配(store_factory/.env/接口)
全部复用上游代码，零侵入。

用法: cd /data/agents/memory && . /data/python312/env.sh && setsid python3.12 start_memory_server.py &
"""

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)                       # .env 在本目录(cwd 加载路径)
sys.path.insert(0, BASE)             # local_hash_embedding 所在

import jiuwen_memory.server.memory_server as ms
from jiuwen_memory.retrieval.embedding.api_embedding import APIEmbedding
from local_hash_embedding import LocalHashEmbedding


class LocalAPIEmbedding(APIEmbedding):
    """离线替身：保留 isinstance 兼容，跳过父类的网络初始化。"""

    def __init__(self, config=None, **kwargs):
        self.config = config
        self.model_name = (getattr(config, "model_name", "") or "") or "local-hash-384"
        self._inner = LocalHashEmbedding()

    async def embed_query(self, text: str, **kwargs):
        return self._inner.embed_query(text)

    async def embed_documents(self, texts, batch_size=None, **kwargs):
        # 参数名与基类对齐: semantic_store 以 texts=/batch_size= 关键字调用
        return self._inner.embed_documents(list(texts))


ms.APIEmbedding = LocalAPIEmbedding

# 设备端向量存储: SQLite+numpy 余弦(见 sqlite_vector_store.py)。
# .env 里 VECTOR_STORE_TYPE=local_sqlite 仅作开关,实际装配走这里的补丁。
from sqlite_vector_store import SqliteVectorStore

ms.create_vector_store = lambda: SqliteVectorStore(
    os.path.join(BASE, "data", "vecstore.db")
)

if __name__ == "__main__":
    ms.main()
