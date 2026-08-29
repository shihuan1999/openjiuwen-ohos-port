# -*- coding: utf-8 -*-
import asyncio, os
os.environ.setdefault("LLM_SSL_VERIFY", "false")
from openjiuwen_deepsearch.config.config import LLMConfig
from openjiuwen_deepsearch.llm.llm_wrapper import create_llm_obj

cfg = LLMConfig.model_validate({
    "model_name": "glm-5.2", "model_type": "openai",
    "base_url": "http://127.0.0.1:16000/v1",
    "api_key": bytearray(b"sk-YOUR_API_KEY"),
    "timeout": 120, "max_tries": 1,
})
d = create_llm_obj(cfg)
m = d["model"]
print("model obj:", type(m).__name__)
attrs = [a for a in dir(m) if a in ("ainvoke", "invoke", "astream", "_ainvoke", "generate", "agenerate")]
print("methods:", attrs)

async def main():
    msgs = [{"role": "user", "content": "say ok"}]
    try:
        r = await m.ainvoke(msgs) if hasattr(m, "ainvoke") else None
        print("ainvoke ->", str(r)[:200])
    except Exception as e:
        print("ainvoke ERR:", repr(e)[:300])
asyncio.run(main())
