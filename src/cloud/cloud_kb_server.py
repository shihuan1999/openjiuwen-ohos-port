# -*- coding: UTF-8 -*-
"""Cloud-side knowledge base server (PC, :9800) — the 云端 of 端云协同.

Stdlib-only (http.server + sqlite3). Endpoints:
  GET  /cloud/api/health
  GET  /cloud/api/pull?since=<ts>            -> docs/notes changed since (LWW source of truth)
  POST /cloud/api/push {device_id, docs, notes} -> apply device changes (LWW)
  GET  /cloud/api/docs                       -> cloud copy doc list
  GET  /cloud/api/store/catalog              -> extra cloud-only catalog entries (merged into device store)
"""
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb_cloud.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id TEXT PRIMARY KEY, title TEXT, category TEXT DEFAULT '', tags TEXT DEFAULT '[]',
  content TEXT DEFAULT '', summary TEXT DEFAULT '', source TEXT DEFAULT 'cloud',
  origin TEXT DEFAULT 'device', favorite INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0,
  rev INTEGER DEFAULT 1, created_at REAL, updated_at REAL,
  last_device TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS notes(
  id TEXT PRIMARY KEY, doc_id TEXT, quote TEXT DEFAULT '', note TEXT DEFAULT '',
  tags TEXT DEFAULT '[]', deleted INTEGER DEFAULT 0, created_at REAL, updated_at REAL);
"""

_LOCK = threading.RLock()
_CONN = None


def conn():
    global _CONN
    if _CONN is None:
        _CONN = sqlite3.connect(DB, check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.executescript(SCHEMA)
        _CONN.commit()
    return _CONN


def doc_dict(r):
    return {k: r[k] for k in r.keys()}


def apply_push(payload):
    applied = skipped = conflicts = 0
    device = payload.get("device_id", "unknown")
    with _LOCK:
        c = conn()
        for d in payload.get("docs", []):
            row = c.execute("SELECT * FROM docs WHERE id=?", (d.get("id"),)).fetchone()
            if row and row["updated_at"] >= d.get("updated_at", 0):
                skipped += 1
                continue
            if row:
                conflicts += 1
            c.execute(
                "INSERT OR REPLACE INTO docs(id,title,category,tags,content,summary,"
                "source,origin,favorite,deleted,rev,created_at,updated_at,last_device)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["id"], d.get("title", ""), d.get("category", ""),
                 json.dumps(d.get("tags", []), ensure_ascii=False),
                 d.get("content", ""), d.get("summary", ""),
                 d.get("source", "cloud"), d.get("origin", "device"),
                 int(d.get("favorite", 0)), int(d.get("deleted", 0)),
                 int(d.get("rev", 1)), d.get("created_at", time.time()),
                 d.get("updated_at", time.time()), device))
            applied += 1
        for n in payload.get("notes", []):
            row = c.execute("SELECT * FROM notes WHERE id=?", (n.get("id"),)).fetchone()
            if row and row["updated_at"] >= n.get("updated_at", 0):
                skipped += 1
                continue
            if row:
                conflicts += 1
            c.execute(
                "INSERT OR REPLACE INTO notes(id,doc_id,quote,note,tags,deleted,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (n["id"], n.get("doc_id", ""), n.get("quote", ""), n.get("note", ""),
                 json.dumps(n.get("tags", []), ensure_ascii=False),
                 int(n.get("deleted", 0)), n.get("created_at", time.time()),
                 n.get("updated_at", time.time())))
            applied += 1
        c.commit()
    return {"applied": applied, "skipped": skipped, "conflicts": conflicts,
            "server_time": time.time()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/cloud/api/health":
            return self._json({"ok": True, "server": "cloud-kb", "time": time.time()})
        if u.path == "/cloud/api/pull":
            since = float(q.get("since", ["0"])[0])
            with _LOCK:
                docs = [doc_dict(r) for r in conn().execute(
                    "SELECT * FROM docs WHERE updated_at>? ORDER BY updated_at", (since,))]
                notes = [doc_dict(r) for r in conn().execute(
                    "SELECT * FROM notes WHERE updated_at>? ORDER BY updated_at", (since,))]
            return self._json({"docs": docs, "notes": notes, "server_time": time.time()})
        if u.path == "/cloud/api/docs":
            with _LOCK:
                rows = conn().execute(
                    "SELECT id,title,category,summary,updated_at,deleted,favorite,origin "
                    "FROM docs ORDER BY updated_at DESC LIMIT 500").fetchall()
            return self._json({"docs": [doc_dict(r) for r in rows]})
        if u.path == "/cloud/api/store/catalog":
            return self._json({"items": [
                {"id": "sciencediscovery", "name": "sciencediscovery",
                 "repo": "openJiuwen-ai/sciencediscovery", "category": "科研",
                 "status": "cloud", "desc": "科研 all-in-one 工作站（TypeScript）",
                 "deps": "Node.js 运行时", "capabilities": ["文献", "实验"]},
                {"id": "community", "name": "community", "repo": "openJiuwen-ai/community",
                 "category": "社区", "status": "cloud",
                 "desc": "社区治理：章程/行为准则/CLA（文档仓）", "deps": "无",
                 "capabilities": ["治理文档"]},
            ]})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/cloud/api/push":
            ln = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(ln) or b"{}")
            except Exception:
                return self._json({"error": "bad json"}, 400)
            return self._json(apply_push(payload))
        return self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    conn()
    srv = ThreadingHTTPServer(("0.0.0.0", 9800), Handler)
    print("cloud kb server on 0.0.0.0:9800 (db: %s)" % DB, flush=True)
    srv.serve_forever()
