# -*- coding: UTF-8 -*-
"""Device-side cloud sync client (端云协同).

Cloud = PC-side cloud_kb_server (:9800). Transport preference:
  1. http://127.0.0.1:9800  via `hdc rport tcp:9800 tcp:9800` USB reverse tunnel
     (works even when the bench LAN isolates the device)
  2. direct LAN URL if configured through meta table / env KB_CLOUD_URL

Protocol (LWW by updated_at):
  GET  /cloud/api/pull?since=<ts>          -> {docs, notes, server_time}
  POST /cloud/api/push {device_id, docs, notes} -> {applied, skipped, conflicts, server_time}
"""
import json
import os
import ssl
import threading
import time
import urllib.request

import kb_store

DEFAULT_CLOUD = os.getenv("KB_CLOUD_URL", "http://127.0.0.1:9800")
_LOG = []
_LOCK = threading.RLock()
_LAST = {"ok": None, "at": 0, "detail": "", "pushed": 0, "pulled": 0, "conflicts": 0}


def _cloud_url():
    return kb_store.get_meta("cloud_url", DEFAULT_CLOUD)


def set_cloud_url(url):
    kb_store.set_meta("cloud_url", url.rstrip("/"))


def log(line):
    with _LOCK:
        _LOG.insert(0, {"at": time.time(), "line": line})
        del _LOG[80:]


def _req(path, payload=None, timeout=30):
    url = _cloud_url() + path
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read())


def _do_sync(direction="both"):
    t0 = time.time()
    since = float(kb_store.get_meta("last_pull_at", 0))
    pulled = pushed = conflicts = 0
    detail = []
    if direction in ("both", "pull"):
        r = _req("/cloud/api/pull?since=%.3f" % since)
        pdocs, pnotes = r.get("docs", []), r.get("notes", [])
        ap, sk, cf = kb_store.apply_remote(pdocs, pnotes)
        pulled, conflicts = ap, cf
        kb_store.set_meta("last_pull_at", r.get("server_time", time.time()))
        detail.append("pull %d docs/%d notes" % (len(pdocs), len(pnotes)))
    if direction in ("both", "push"):
        psince = float(kb_store.get_meta("last_push_at", 0))
        docs, notes = kb_store.changes_since(psince)
        if docs or notes:
            r = _req("/cloud/api/push", {
                "device_id": kb_store.DEVICE_ID,
                "docs": docs, "notes": notes,
            }, timeout=60)
            pushed = r.get("applied", 0)
            conflicts += r.get("conflicts", 0)
            kb_store.set_meta("last_push_at", r.get("server_time", time.time()))
            detail.append("push %d docs/%d notes" % (len(docs), len(notes)))
        else:
            detail.append("push 0 (clean)")
    ok = True
    kb_store.set_meta("last_sync_at", time.time())
    line = "sync %s: %s in %.1fs" % (direction, "; ".join(detail), time.time() - t0)
    log(line)
    with _LOCK:
        _LAST.update({"ok": ok, "at": time.time(), "detail": line,
                      "pushed": pushed, "pulled": pulled, "conflicts": conflicts})
    return {"ok": ok, "detail": line, "pushed": pushed, "pulled": pulled,
            "conflicts": conflicts, "elapsed": round(time.time() - t0, 1)}


def sync_now(direction="both"):
    """Blocking sync; called from a worker thread by the sidecar."""
    try:
        return _do_sync(direction)
    except Exception as e:
        line = "sync error: %s" % e
        log(line)
        with _LOCK:
            _LAST.update({"ok": False, "at": time.time(), "detail": line})
        return {"ok": False, "detail": line}


def status():
    with _LOCK:
        st = dict(_LAST)
    st["cloud_url"] = _cloud_url()
    st["pending"] = kb_store.pending_sync_count()
    st["last_sync_at"] = kb_store.get_meta("last_sync_at")
    st["device_id"] = kb_store.DEVICE_ID
    st["logs"] = list(_LOG[:30])
    return st
