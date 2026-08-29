# -*- coding: UTF-8 -*-
"""KB store: SQLite-backed knowledge base for the OHOS KB app (docs/notes/
favorites/tags/history + hybrid semantic search).

Design notes (device constraints):
- device sqlite3 has no FTS5 -> keyword path uses LIKE + CJK bigram overlap
- embeddings reuse /data/agents/memory/local_hash_embedding.py (384-dim hash),
  chunk vectors stored as float32 BLOB, cosine via numpy (on device)
- single-writer: one connection guarded by an RLock; handlers run in the
  aiohttp event loop, all calls here are short and non-blocking for KB scale
"""
import glob
import json
import os
import re
import sqlite3
import threading
import time
import uuid

import numpy as np

BASE = "/data/agents/kbapp"
DB_PATH = os.path.join(BASE, "kb.db")
CORPUS_DIR = "/data/agents/kb_data"
DEVICE_ID = "k3pico-001"

sys_path = "/data/agents/memory"
if sys_path not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path)
from local_hash_embedding import LocalHashEmbedding  # noqa: E402

_EMB = LocalHashEmbedding()
_LOCK = threading.RLock()
_CONN = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT DEFAULT '',
  tags TEXT DEFAULT '[]', content TEXT DEFAULT '', summary TEXT DEFAULT '',
  source TEXT DEFAULT 'local', origin TEXT DEFAULT 'device',
  favorite INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0,
  rev INTEGER DEFAULT 1, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS notes(
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, quote TEXT DEFAULT '',
  note TEXT DEFAULT '', tags TEXT DEFAULT '[]', deleted INTEGER DEFAULT 0,
  created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS history(
  doc_id TEXT PRIMARY KEY, at REAL);
CREATE TABLE IF NOT EXISTS chunks(
  doc_id TEXT, idx INTEGER, head TEXT, text TEXT, vec BLOB);
CREATE TABLE IF NOT EXISTS meta(
  k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_docs_upd ON docs(updated_at);
CREATE INDEX IF NOT EXISTS idx_notes_upd ON notes(updated_at);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
"""

# corpus file -> (category, tags)
_CORPUS_META = [
    ("spec", "产品", ["K3", "Pico-ITX", "规格"]),
    ("product", "产品", ["K3", "官方"]),
    ("chip", "芯片", ["SpacemiT", "X100TM"]),
    ("eco", "芯片", ["生态"]),
    ("forum", "指南", ["论坛", "入门"]),
    ("guide", "指南", ["上手"]),
    ("hw", "硬件", ["实测", "快照"]),
    ("snapshot", "硬件", ["实测"]),
    ("port", "移植", ["OHOS", "交叉编译"]),
    ("ohos", "移植", ["OpenHarmony"]),
]


def _conn():
    global _CONN
    if _CONN is None:
        os.makedirs(BASE, exist_ok=True)
        _CONN = sqlite3.connect(DB_PATH, check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.executescript(SCHEMA)
        _CONN.commit()
    return _CONN


def _now():
    return time.time()


def _uuid():
    return uuid.uuid4().hex[:12]


def _bigrams(t):
    return {t[i:i + 2] for i in range(len(t) - 1)} | {t[i:i + 3] for i in range(len(t) - 2)}


def _split_len(text, n=1400):
    paras, out, cur = text.split("\n\n"), [], ""
    for p in paras:
        if len(cur) + len(p) > n and cur:
            out.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p).strip()
    if cur.strip():
        out.append(cur)
    return out


def _reindex(doc_id, title, content):
    c = _conn()
    c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    rows = []
    head, buf = "", []
    pieces = []
    for line in content.splitlines():
        if line.startswith("## "):
            if buf:
                pieces.append((head, "\n".join(buf)))
            head, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if buf:
        pieces.append((head, "\n".join(buf)))
    if not pieces:
        pieces = [("", content)]
    for i, (head, text) in enumerate(pieces):
        text = text.strip()
        if not text:
            continue
        for j, piece in enumerate(_split_len(text)):
            ctx = (title + " > " + head).strip(" >")
            vec = np.asarray(_EMB.embed_query(ctx + "\n" + piece[:400]), dtype=np.float32)
            rows.append((doc_id, len(rows), ctx or title, piece, vec.tobytes()))
    c.executemany("INSERT INTO chunks(doc_id,idx,head,text,vec) VALUES(?,?,?,?,?)", rows)


def _auto_summary(content):
    text = re.sub(r"[#*`>\-\[\]]", "", content)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


# ---------------- corpus import ----------------

def import_corpus(force=False):
    with _LOCK:
        c = _conn()
        if not force and c.execute("SELECT COUNT(*) FROM docs WHERE origin='corpus'").fetchone()[0] > 0:
            return 0
        n = 0
        for fp in sorted(glob.glob(os.path.join(CORPUS_DIR, "*.md"))):
            fname = os.path.basename(fp)
            try:
                data = open(fp, "rb").read().decode("utf-8", "replace")
            except OSError:
                continue
            title = fname[:-3]
            for line in data.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            cat, tags = "语料", [fname[:-3]]
            low = fname.lower()
            for key, ccat, ctags in _CORPUS_META:
                if key in low:
                    cat, tags = ccat, ctags
                    break
            doc = {
                "title": title, "category": cat, "tags": tags,
                "content": data, "summary": _auto_summary(data),
                "source": "local", "origin": "corpus",
            }
            _upsert_doc(doc)
            n += 1
        c.commit()
        return n


# ---------------- docs CRUD ----------------

def _doc_row(d):
    return {
        "id": d["id"], "title": d["title"], "category": d["category"],
        "tags": json.loads(d["tags"] or "[]"), "content": d["content"],
        "summary": d["summary"], "source": d["source"], "origin": d["origin"],
        "favorite": d["favorite"], "deleted": d["deleted"], "rev": d["rev"],
        "created_at": d["created_at"], "updated_at": d["updated_at"],
    }


def _upsert_doc(doc):
    c = _conn()
    now = _now()
    doc.setdefault("id", _uuid())
    doc.setdefault("category", "")
    doc.setdefault("tags", [])
    doc.setdefault("content", "")
    doc.setdefault("summary", "")
    doc.setdefault("source", "local")
    doc.setdefault("origin", "device")
    row = c.execute("SELECT * FROM docs WHERE id=?", (doc["id"],)).fetchone()
    if row:
        c.execute(
            "UPDATE docs SET title=?,category=?,tags=?,content=?,summary=?,source=?,"
            "rev=rev+1,updated_at=? WHERE id=?",
            (doc["title"], doc["category"], json.dumps(doc["tags"], ensure_ascii=False),
             doc["content"], doc["summary"] or _auto_summary(doc["content"]),
             doc["source"], now, doc["id"]))
    else:
        c.execute(
            "INSERT INTO docs(id,title,category,tags,content,summary,source,origin,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (doc["id"], doc["title"], doc["category"],
             json.dumps(doc["tags"], ensure_ascii=False), doc["content"],
             doc["summary"] or _auto_summary(doc["content"]), doc["source"],
             doc["origin"], now, now))
    _reindex(doc["id"], doc["title"], doc["content"])
    return get_doc(doc["id"])


def create_doc(title, content="", category="", tags=None, summary="", source="local", origin="device"):
    with _LOCK:
        doc = _upsert_doc({
            "title": (title or "未命名文档").strip(), "content": content or "",
            "category": category or "", "tags": tags or [],
            "summary": summary, "source": source, "origin": origin,
        })
        _conn().commit()
        return doc


def update_doc(doc_id, **fields):
    with _LOCK:
        c = _conn()
        row = c.execute("SELECT * FROM docs WHERE id=? AND deleted=0", (doc_id,)).fetchone()
        if not row:
            return None
        doc = _doc_row(row)
        for k in ("title", "content", "category", "tags", "summary", "favorite", "source"):
            if k in fields and fields[k] is not None:
                doc[k] = fields[k]
        out = _upsert_doc(doc)
        c.commit()
        return out


def delete_doc(doc_id):
    with _LOCK:
        c = _conn()
        c.execute("UPDATE docs SET deleted=1,rev=rev+1,updated_at=? WHERE id=?", (_now(), doc_id))
        c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        c.commit()
        return True


def get_doc(doc_id):
    row = _conn().execute("SELECT * FROM docs WHERE id=? AND deleted=0", (doc_id,)).fetchone()
    return _doc_row(row) if row else None


def list_docs(category=None, tag=None, favorite=None, q=None, limit=200):
    sql, args = "SELECT * FROM docs WHERE deleted=0", []
    if category and category != "全部":
        sql += " AND category=?"
        args.append(category)
    if favorite:
        sql += " AND favorite=1"
    if q:
        sql += " AND (title LIKE ? OR summary LIKE ? OR content LIKE ?)"
        like = "%" + q + "%"
        args += [like, like, like]
    if tag:
        sql += " AND tags LIKE ?"
        args.append('%"' + tag + '"%')
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(int(limit))
    rows = _conn().execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = _doc_row(r)
        d.pop("content", None)
        out.append(d)
    return out


def toggle_favorite(doc_id):
    with _LOCK:
        c = _conn()
        c.execute("UPDATE docs SET favorite=1-favorite,updated_at=? WHERE id=?", (_now(), doc_id))
        c.commit()
        row = c.execute("SELECT favorite FROM docs WHERE id=?", (doc_id,)).fetchone()
        return bool(row["favorite"]) if row else None


def add_history(doc_id):
    with _LOCK:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO history(doc_id,at) VALUES(?,?)", (doc_id, _now()))
        c.commit()


def list_history(limit=20):
    rows = _conn().execute(
        "SELECT d.*, h.at AS viewed_at FROM history h JOIN docs d ON d.id=h.doc_id "
        "WHERE d.deleted=0 ORDER BY h.at DESC LIMIT ?", (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = _doc_row(r)
        d.pop("content", None)
        d["viewed_at"] = r["viewed_at"]
        out.append(d)
    return out


def categories():
    rows = _conn().execute(
        "SELECT category, COUNT(*) n FROM docs WHERE deleted=0 GROUP BY category ORDER BY n DESC").fetchall()
    return [{"category": r["category"] or "未分类", "count": r["n"]} for r in rows]


def all_tags():
    rows = _conn().execute("SELECT tags FROM docs WHERE deleted=0").fetchall()
    cnt = {}
    for r in rows:
        for t in json.loads(r["tags"] or "[]"):
            cnt[t] = cnt.get(t, 0) + 1
    return sorted(cnt.items(), key=lambda kv: -kv[1])


# ---------------- notes ----------------

def add_note(doc_id, quote="", note="", tags=None):
    with _LOCK:
        c = _conn()
        nid, now = _uuid(), _now()
        c.execute(
            "INSERT INTO notes(id,doc_id,quote,note,tags,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (nid, doc_id, quote or "", note or "", json.dumps(tags or [], ensure_ascii=False), now, now))
        c.commit()
        return get_note(nid)


def get_note(nid):
    r = _conn().execute("SELECT * FROM notes WHERE id=? AND deleted=0", (nid,)).fetchone()
    return _note_row(r) if r else None


def _note_row(r):
    return {"id": r["id"], "doc_id": r["doc_id"], "quote": r["quote"], "note": r["note"],
            "tags": json.loads(r["tags"] or "[]"), "created_at": r["created_at"],
            "updated_at": r["updated_at"], "doc_title": r["doc_title"] if "doc_title" in r.keys() else ""}


def list_notes(doc_id=None, q=None, limit=200):
    sql, args = ("SELECT n.*, d.title AS doc_title FROM notes n "
                 "LEFT JOIN docs d ON d.id=n.doc_id AND d.deleted=0 "
                 "WHERE n.deleted=0"), []
    if doc_id:
        sql += " AND n.doc_id=?"
        args.append(doc_id)
    if q:
        sql += " AND (n.quote LIKE ? OR n.note LIKE ?)"
        like = "%" + q + "%"
        args += [like, like]
    sql += " ORDER BY n.updated_at DESC LIMIT ?"
    args.append(int(limit))
    return [_note_row(r) for r in _conn().execute(sql, args).fetchall()]


def delete_note(nid):
    with _LOCK:
        c = _conn()
        c.execute("UPDATE notes SET deleted=1,updated_at=? WHERE id=?", (_now(), nid))
        c.commit()
        return True


# ---------------- search ----------------

def search(query, mode="mix", top_k=8):
    q = (query or "").strip()
    if not q:
        return []
    with _LOCK:
        rows = _conn().execute(
            "SELECT c.doc_id,c.head,c.text,c.vec,d.title,d.category FROM chunks c "
            "JOIN docs d ON d.id=c.doc_id AND d.deleted=0").fetchall()
    qv = np.asarray(_EMB.embed_query(q), dtype=np.float32)
    qbg = _bigrams(q)
    ql = q.lower()
    scored = []
    for r in rows:
        vec = np.frombuffer(r["vec"], dtype=np.float32)
        n = max(float(np.linalg.norm(qv) * np.linalg.norm(vec)), 1e-9)
        cos = float(np.dot(qv, vec) / n)
        bg = _bigrams(r["text"])
        overlap = len(qbg & bg) / max(1, len(qbg))
        kw = 1.0 if (ql in r["text"].lower() or ql in r["head"].lower()) else 0.0
        if mode == "semantic":
            s = cos
        elif mode == "keyword":
            s = 0.7 * overlap + 0.3 * kw
        else:
            s = 0.55 * cos + 0.30 * overlap + 0.15 * kw
        scored.append((s, r))
    scored.sort(key=lambda t: -t[0])
    out, seen = [], set()
    for s, r in scored[: int(top_k) * 3]:
        if r["doc_id"] in seen:
            continue
        seen.add(r["doc_id"])
        snippet = r["text"] if len(r["text"]) <= 400 else r["text"][:400] + "…"
        out.append({"doc_id": r["doc_id"], "title": r["title"], "head": r["head"],
                    "category": r["category"], "snippet": snippet, "score": round(s, 4)})
        if len(out) >= int(top_k):
            break
    return out


def stats():
    c = _conn()
    docs = c.execute("SELECT COUNT(*) FROM docs WHERE deleted=0").fetchone()[0]
    favs = c.execute("SELECT COUNT(*) FROM docs WHERE deleted=0 AND favorite=1").fetchone()[0]
    notes = c.execute("SELECT COUNT(*) FROM notes WHERE deleted=0").fetchone()[0]
    chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    cats = c.execute("SELECT COUNT(DISTINCT category) FROM docs WHERE deleted=0").fetchone()[0]
    last = get_meta("last_sync_at")
    return {"docs": docs, "favorites": favs, "notes": notes, "chunks": chunks,
            "categories": cats, "last_sync_at": last, "device_id": DEVICE_ID}


# ---------------- meta / sync support ----------------

def set_meta(k, v):
    with _LOCK:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))
        c.commit()


def get_meta(k, default=None):
    r = _conn().execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def changes_since(ts):
    docs = [_doc_row(r) for r in _conn().execute(
        "SELECT * FROM docs WHERE updated_at>? ORDER BY updated_at", (ts,)).fetchall()]
    notes = [_note_row(r) for r in _conn().execute(
        "SELECT * FROM notes WHERE updated_at>? ORDER BY updated_at", (ts,)).fetchall()]
    return docs, notes


def apply_remote(docs, notes):
    """LWW merge of cloud rows into local store. Returns (applied, skipped, conflicts)."""
    applied = skipped = conflicts = 0
    with _LOCK:
        c = _conn()
        for d in docs or []:
            row = c.execute("SELECT * FROM docs WHERE id=?", (d.get("id"),)).fetchone()
            if row and row["updated_at"] >= d.get("updated_at", 0):
                if row["updated_at"] > d.get("updated_at", 0):
                    conflicts += 1
                skipped += 1
                continue
            d.setdefault("origin", "cloud")
            d["source"] = "cloud"
            _upsert_doc(d)
            if row:
                conflicts += 1
            applied += 1
        for n in notes or []:
            row = c.execute("SELECT * FROM notes WHERE id=?", (n.get("id"),)).fetchone()
            if row and row["updated_at"] >= n.get("updated_at", 0):
                skipped += 1
                continue
            c.execute(
                "INSERT OR REPLACE INTO notes(id,doc_id,quote,note,tags,deleted,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (n["id"], n["doc_id"], n.get("quote", ""), n.get("note", ""),
                 json.dumps(n.get("tags", []), ensure_ascii=False), n.get("deleted", 0),
                 n.get("created_at", _now()), n.get("updated_at", _now())))
            applied += 1
        c.commit()
    return applied, skipped, conflicts


def pending_sync_count():
    since = float(get_meta("last_push_at", 0))
    docs, notes = changes_since(since)
    return len(docs) + len(notes)
