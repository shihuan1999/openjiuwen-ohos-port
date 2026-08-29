# -*- coding: UTF-8 -*-
"""Custom local-search engine for openJiuwen-DeepSearch on device.

deepsearch loads this file via CustomLocalSearchConfig
(custom_local_search_file + custom_local_search_func="KBLocalSearch");
the class is instantiated with LocalSearchEngineConfig.model_dump() kwargs
and must expose `async aresults(query) -> List[Dict]` where each dict looks
like a web-search hit: {url, title, content, snippet, score}.

Retrieval = kb_store docs (SQLite + hash-embedding hybrid) merged with the
raw /data/agents/kb_data corpus chunks indexed by kb_agent.
"""
import os
import sys

sys.path.insert(0, "/data/agents")
sys.path.insert(0, "/data/agents/memory")

import kb_store  # noqa: E402


class KBLocalSearch:
    """Adapter plugged into deepsearch's local_search_mapping["custom"]."""

    def __init__(self, search_engine_name="custom", search_api_key=None,
                 search_url="", search_datasets=None, max_local_search_results=5,
                 recall_threshold=0.5, search_mode="mix", knowledge_base_type="internal",
                 source="KooSearch", extension=None, knowledge_base_configs=None, **kw):
        self.top_k = int(max_local_search_results or 5)
        self.mode = search_mode if search_mode in ("doc", "keyword", "mix") else "mix"
        self.mode_map = {"doc": "semantic", "keyword": "keyword", "mix": "mix"}
        self._corpus = None

    async def aopen(self):
        pass

    async def aclose(self):
        pass

    def _load_corpus(self):
        """Lazily reuse kb_agent's in-memory corpus index (kb_data chunks)."""
        if self._corpus is None:
            self._corpus = []
            try:
                import kb_agent  # heavy import (builds index at module import)
                for c in kb_agent.CHUNKS:
                    self._corpus.append(c)
            except Exception:
                pass
        return self._corpus

    async def aresults(self, query):
        out = []
        for hit in kb_store.search(query, mode=self.mode_map[self.mode],
                                   top_k=self.top_k):
            out.append({
                "url": "kb://doc/%s" % hit["doc_id"],
                "title": "%s · %s" % (hit["title"], hit["head"]) if hit["head"] else hit["title"],
                "content": hit["snippet"],
                "snippet": hit["snippet"][:200],
                "score": hit["score"],
            })
        for c in self._load_corpus()[:]:
            if len(out) >= self.top_k:
                break
            if query.strip() and query.strip().lower() in c["text"].lower():
                out.append({
                    "url": "kb://corpus/%s" % c["file"],
                    "title": c["head"],
                    "content": c["text"][:600],
                    "snippet": c["text"][:200],
                    "score": 0.35,
                })
        return out[: self.top_k]
