# -*- coding: utf-8 -*-
import asyncio, sys
sys.path.insert(0, '/data/agents')
import httpx
orig = httpx.AsyncClient.send
async def patched(self, request, **kw):
    print("HTTPX->", request.method, str(request.url)[:90], flush=True)
    try:
        r = await orig(self, request, **kw)
        print("HTTPX<-", r.status_code, flush=True)
        return r
    except Exception as e:
        print("HTTPX-ERR", type(e).__name__, repr(e)[:160], "URL:", str(request.url)[:90], flush=True)
        raise
httpx.AsyncClient.send = patched
import deepresearch_agent
async def main():
    r = await deepresearch_agent.arun("K3 pico board hardware specs",
        record=lambda ev: print('EV', ev.get('type'), str(ev.get('head',''))[:60], flush=True),
        timeout=300)
    print('REPORT-LEN:', len(r))
asyncio.run(main())
