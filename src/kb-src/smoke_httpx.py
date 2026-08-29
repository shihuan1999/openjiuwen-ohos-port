# -*- coding: utf-8 -*-
import httpx, asyncio, json
H = {"Authorization": "Bearer sk-YOUR_API_KEY"}
B = {"model": "glm-5.2", "messages": [{"role": "user", "content": "say ok"}], "max_tokens": 8}
def sync_test():
    try:
        r = httpx.post("http://127.0.0.1:16000/v1/chat/completions", json=B, headers=H, timeout=30)
        print("sync:", r.status_code, r.text[:80])
    except Exception as e:
        print("sync ERR:", repr(e)[:200])
async def async_test():
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post("http://127.0.0.1:16000/v1/chat/completions", json=B, headers=H, timeout=30)
            print("async:", r.status_code, r.text[:80])
    except Exception as e:
        print("async ERR:", repr(e)[:200])
sync_test(); asyncio.run(async_test())
