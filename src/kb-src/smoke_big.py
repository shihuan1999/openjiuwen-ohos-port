# -*- coding: utf-8 -*-
import asyncio
from openai import AsyncOpenAI
KEY = "sk-YOUR_API_KEY"
BIG = "You are a report intent parser. " + ("blah context line. " * 1500)
TOOLS = [{"type": "function", "function": {"name": "emit_report_intent",
  "description": "emit the parsed intent",
  "parameters": {"type": "object", "properties": {"research_query": {"type": "string"},
   "language": {"type": "string"}}, "required": ["research_query", "language"]}}}]
async def t():
    c = AsyncOpenAI(base_url="http://127.0.0.1:16000/v1", api_key=KEY, max_retries=0)
    try:
        s = await c.chat.completions.create(model="glm-5.2",
            messages=[{"role": "system", "content": BIG}, {"role": "user", "content": "parse: K3 board specs"}],
            tools=TOOLS, stream=True, timeout=120)
        n = 0
        async for ch in s:
            n += 1
        print("stream chunks:", n)
    except Exception as e:
        print("BIG stream ERR:", repr(e)[:300])
asyncio.run(t())
