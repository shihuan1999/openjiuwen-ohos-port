# -*- coding: UTF-8 -*-
"""本地 SQLite + numpy 向量存储 —— BaseVectorStore 的设备端实现。

为何存在:chromadb 需 chroma-hnswlib(C++/onnx),milvus/es/gauss 都要外部服务,
边缘设备跑不动;而语义记忆(SemanticStore)检索路径必须有 vector_store。
本实现每个 collection 一张表,id 为主键,整包字段存 JSON,向量存 float32 BLOB,
检索用 numpy 矩阵余弦(设备已移植 numpy 1.26.4),千级 chunk 规模毫秒级返回。

配合 VECTOR_STORE_TYPE=local_sqlite + 启动器里的 ms.create_vector_store 补丁使用,
零侵入上游代码。
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
from typing import Any, Dict, List, Optional

import numpy as np

from jiuwen_memory.foundation.store.base_vector_store import (
    BaseVectorStore,
    CollectionSchema,
    VectorSearchResult,
)
from jiuwen_memory.foundation.store.filter_dsl import FilterCondition, FilterGroup, FilterOperator


def _safe_name(name: str) -> str:
    return "c_" + re.sub(r"[^0-9A-Za-z_]", "_", name)


class SqliteVectorStore(BaseVectorStore):
    def __init__(self, db_path: str = "/data/agents/memory/data/vecstore.db"):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS collections ("
            "name TEXT PRIMARY KEY, schema TEXT, metadata TEXT)"
        )
        self._conn.commit()

    # ---------- 内部工具 ----------

    def _vec_to_blob(self, vec: List[float]) -> bytes:
        return np.asarray(vec, dtype=np.float32).tobytes()

    def _blob_to_vec(self, blob: bytes) -> np.ndarray:
        return np.frombuffer(blob, dtype=np.float32)

    def _ensure_table(self, collection_name: str):
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_safe_name(collection_name)} ("
            "id TEXT PRIMARY KEY, doc TEXT NOT NULL, embedding BLOB NOT NULL)"
        )
        self._conn.commit()

    @staticmethod
    def _match(doc: Dict[str, Any], group: Optional[FilterGroup]) -> bool:
        if group is None:
            return True
        results = []
        for cond in group.conditions:
            if isinstance(cond, FilterCondition):
                v = doc.get(cond.field)
                ok = (v == cond.value) if cond.op == FilterOperator.EQ else (v != cond.value)
                results.append(ok)
            else:  # FilterGroup 递归
                results.append(SqliteVectorStore._match(doc, cond))
        if not results:
            return True
        logic = getattr(group.logic, "value", str(group.logic)).lower()
        return all(results) if logic == "and" else any(results)

    # ---------- BaseVectorStore 接口 ----------

    async def create_collection(self, collection_name, schema, **kwargs) -> None:
        s = schema.to_dict() if isinstance(schema, CollectionSchema) else schema
        self._conn.execute(
            "INSERT OR REPLACE INTO collections(name, schema, metadata) VALUES (?,?,?)",
            (collection_name, json.dumps(s, ensure_ascii=False, default=str), "{}"),
        )
        self._ensure_table(collection_name)

    async def delete_collection(self, collection_name, **kwargs) -> None:
        self._conn.execute(f"DROP TABLE IF EXISTS {_safe_name(collection_name)}")
        self._conn.execute("DELETE FROM collections WHERE name=?", (collection_name,))
        self._conn.commit()

    async def collection_exists(self, collection_name, **kwargs) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (_safe_name(collection_name),),
        ).fetchone()
        return row is not None

    async def get_schema(self, collection_name, **kwargs) -> Optional[CollectionSchema]:
        row = self._conn.execute(
            "SELECT schema FROM collections WHERE name=?", (collection_name,)
        ).fetchone()
        if not row:
            return None
        return CollectionSchema.from_dict(json.loads(row[0]))

    async def add_docs(self, collection_name, docs: List[Dict[str, Any]], **kwargs) -> None:
        self._ensure_table(collection_name)
        for d in docs:
            doc = {k: v for k, v in d.items() if k != "embedding"}
            self._conn.execute(
                f"INSERT OR REPLACE INTO {_safe_name(collection_name)}(id, doc, embedding) VALUES (?,?,?)",
                (str(d.get("id")), json.dumps(doc, ensure_ascii=False, default=str),
                 self._vec_to_blob(d["embedding"])),
            )
        self._conn.commit()

    async def search(
        self,
        collection_name,
        query_vector,
        vector_field,
        top_k: int = 5,
        filters: Optional[FilterGroup] = None,
        **kwargs,
    ) -> List[VectorSearchResult]:
        if not await self.collection_exists(collection_name):
            return []
        rows = self._conn.execute(
            f"SELECT id, doc, embedding FROM {_safe_name(collection_name)}"
        ).fetchall()
        if not rows:
            return []
        mat = np.vstack([self._blob_to_vec(r[2]) for r in rows])
        norms = np.linalg.norm(mat, axis=1)
        norms[norms == 0] = 1.0
        q = np.asarray(query_vector, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scores = (mat / norms[:, None]) @ q
        order = np.argsort(-scores)[:top_k]
        out: List[VectorSearchResult] = []
        for i in order:
            doc = json.loads(rows[i][1])
            if not self._match(doc, filters):
                continue
            out.append(VectorSearchResult(score=float(scores[i]), fields=doc))
        return out

    async def list_docs(
        self, collection_name, filters=None, limit: int = 100, offset: int = 0, **kwargs
    ) -> List[Dict[str, Any]]:
        if not await self.collection_exists(collection_name):
            return []
        rows = self._conn.execute(
            f"SELECT doc FROM {_safe_name(collection_name)} LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        docs = [json.loads(r[0]) for r in rows]
        return [d for d in docs if self._match(d, filters)]

    async def update_doc_fields(self, collection_name, doc_id, fields: Dict[str, Any], **kwargs) -> None:
        row = self._conn.execute(
            f"SELECT doc FROM {_safe_name(collection_name)} WHERE id=?", (str(doc_id),)
        ).fetchone()
        if not row:
            return
        doc = json.loads(row[0])
        doc.update(fields)
        self._conn.execute(
            f"UPDATE {_safe_name(collection_name)} SET doc=? WHERE id=?",
            (json.dumps(doc, ensure_ascii=False, default=str), str(doc_id)),
        )
        self._conn.commit()

    async def delete_docs_by_ids(self, collection_name, ids: List[str], **kwargs) -> None:
        self._conn.executemany(
            f"DELETE FROM {_safe_name(collection_name)} WHERE id=?", [(str(i),) for i in ids]
        )
        self._conn.commit()

    async def delete_docs_by_filters(self, collection_name, filters: Dict[str, Any], **kwargs) -> None:
        docs = await self.list_docs(collection_name, limit=1000000)
        ids = [d.get("id") for d in docs
               if all(str(d.get(k)) == str(v) for k, v in filters.items())]
        await self.delete_docs_by_ids(collection_name, ids)

    async def list_collection_names(self) -> List[str]:
        return [r[0] for r in self._conn.execute("SELECT name FROM collections").fetchall()]

    async def update_schema(self, collection_name, operations, **kwargs) -> None:
        pass  # 设备端无跨版本迁移需求

    async def update_collection_metadata(self, collection_name, metadata: Dict[str, Any], **kwargs) -> None:
        self._conn.execute(
            "UPDATE collections SET metadata=? WHERE name=?",
            (json.dumps(metadata, ensure_ascii=False, default=str), collection_name),
        )
        self._conn.commit()

    async def get_collection_metadata(self, collection_name, **kwargs) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT metadata FROM collections WHERE name=?", (collection_name,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else {}
