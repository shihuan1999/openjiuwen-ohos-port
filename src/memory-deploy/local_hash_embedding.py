# -*- coding: UTF-8 -*-
"""本地哈希 embedding —— 离线、确定性、纯 Python。

rvcompute 网关无 embedding 模型(95 个全是 chat 模型)，设备上也跑不动
onnxruntime/tokenizers，故用双哈希 n-gram 词袋向量替代真实语义向量：

- 中文按 1-gram + 2-gram 切分，拉丁/数字按整词切分；
- 每个 token 经 FNV-1a + 两个质数扰动映射到 dim 维并带符号累加；
- L2 归一化后与 sqlite-vec/纯 Python 余弦路径完全兼容。

词法相似度对本机规模(知识库+记忆,千级 chunk)足够；FTS5 关键词召回
与向量召回加权混合(0.3/0.7)，中文 bigram 保证召回复盖。
"""

from __future__ import annotations

import math
from typing import List

_DIM = 384
_FNV_PRIME = 0x01000193


def _fnv1a(data: str, seed: int = 0x811C9DC5) -> int:
    h = seed
    for ch in data:
        h ^= ord(ch)
        h = (h * _FNV_PRIME) & 0xFFFFFFFF
    return h


def _tokens(text: str) -> List[str]:
    toks: List[str] = []
    buf: List[str] = []
    def flush():
        if buf:
            w = "".join(buf).lower()
            if w:
                toks.append(w)
            buf.clear()
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            flush()
            toks.append(ch)
        elif ch.isalnum():
            buf.append(ch)
        else:
            flush()
    flush()
    # 中文 2-gram
    grams = []
    prev_cjk = None
    for t in toks:
        if len(t) == 1 and 0x4E00 <= ord(t) <= 0x9FFF:
            if prev_cjk:
                grams.append(prev_cjk + t)
            prev_cjk = t
        else:
            prev_cjk = None
    return toks + grams


class LocalHashEmbedding:
    """APIEmbedding 的离线替身：接口对齐 embed_documents / embed_query。"""

    def __init__(self, dim: int = _DIM, model_name: str = "local-hash-384"):
        self.dim = dim
        self.model_name = model_name

    def _embed_one(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        if not text:
            return vec
        for tok in _tokens(text):
            h1 = _fnv1a(tok)
            h2 = _fnv1a(tok, seed=0x9E3779B9)
            idx = h1 % self.dim
            sign = 1.0 if (h2 & 1) == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts: List[str], **kwargs) -> List[List[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str, **kwargs) -> List[float]:
        return self._embed_one(text)
