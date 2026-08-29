# -*- coding: utf-8 -*-
import asyncio, os, inspect
os.environ.setdefault("LLM_SSL_VERIFY", "false")
from openjiuwen_deepsearch.config.config import LLMConfig
from openjiuwen_deepsearch.llm.llm_wrapper import create_llm_obj

cfg = LLMConfig.model_validate({
    "model_name": "glm-5.2", "model_type": "openai",
    "base_url": "http://127.0.0.1:16000/v1",
    "api_key": bytearray(b"sk-YOUR_API_KEY"),
    "timeout": 120, "max_tries": 1,
})
m = create_llm_obj(cfg)["model"]

async def main():
    msgs = [{"role": "user", "content": "say ok"}]
    try:
        r = m.invoke(msgs)
        if inspect.isawaitable(r):
            r = await r
        print("invoke ->", str(r)[:250])
    except Exception as e:
        print("invoke ERR:", repr(e)[:400])
asyncio.run(main())
