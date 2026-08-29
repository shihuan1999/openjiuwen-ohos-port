# -*- coding: utf-8 -*-
import asyncio, os
os.environ.setdefault("API_BASE", "http://127.0.0.1:16000/v1")
from openjiuwen_deepsearch.config.config import LLMConfig
from openjiuwen_deepsearch.llm.llm_wrapper import create_llm_obj

cfg = LLMConfig.model_validate({
    "model_name": "glm-5.2", "model_type": "openai",
    "base_url": "http://127.0.0.1:16000/v1",
    "api_key": bytearray(b"sk-YOUR_API_KEY"),
    "timeout": 120, "max_tries": 1,
})
llm = create_llm_obj(cfg)
print("llm obj:", type(llm).__name__, [a for a in dir(llm) if not a.startswith('_')][:12])

async def main():
    msgs = [{"role": "user", "content": "say ok"}]
    for attr in ("ainvoke", "invoke", "astream"):
        if hasattr(llm, attr):
            print("trying", attr)
            try:
                r = getattr(llm, attr)(msgs) if attr != "ainvoke" else await llm.ainvoke(msgs)
                if hasattr(r, "__aiter__"):
                    out = ""
                    async for ch in r:
                        out += str(ch)
                    print(attr, "->", out[:120])
                else:
                    print(attr, "->", str(r)[:200])
                return
            except Exception as e:
                print(attr, "ERR:", repr(e)[:250])
asyncio.run(main())
