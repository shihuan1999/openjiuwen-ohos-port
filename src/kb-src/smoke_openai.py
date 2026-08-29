# -*- coding: utf-8 -*-
import asyncio, os
from openai import AsyncOpenAI
async def t():
    c = AsyncOpenAI(base_url="http://127.0.0.1:16000/v1",
                    api_key="sk-YOUR_API_KEY")
    try:
        r = await c.chat.completions.create(model="glm-5.2",
            messages=[{"role": "user", "content": "say ok"}], max_tokens=8)
        print("nonstream:", r.choices[0].message.content)
    except Exception as e:
        print("nonstream ERR:", repr(e)[:300])
    try:
        s = await c.chat.completions.create(model="glm-5.2",
            messages=[{"role": "user", "content": "say ok"}], max_tokens=8, stream=True)
        out = ""
        async for ch in s:
            out += (ch.choices[0].delta.content or "") if ch.choices else ""
        print("stream:", out)
    except Exception as e:
        print("stream ERR:", repr(e)[:300])
asyncio.run(t())
