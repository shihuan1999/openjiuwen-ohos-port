# -*- coding: utf-8 -*-
import asyncio, os
os.environ.setdefault("LLM_SSL_VERIFY", "false")
from openjiuwen_deepsearch.config.config import LLMConfig
from openjiuwen_deepsearch.llm.llm_wrapper import create_llm_obj
from openjiuwen_deepsearch.utils.common_utils import llm_utils

cfg = LLMConfig.model_validate({
    "model_name": "glm-5.2", "model_type": "openai",
    "base_url": "http://127.0.0.1:16000/v1",
    "api_key": bytearray(b"sk-YOUR_API_KEY"),
    "hyper_parameters": {"model_name": "glm-5.2"},
    "timeout": 120, "max_tries": 1,
})
d = create_llm_obj(cfg)

async def main():
    try:
        r = await llm_utils.llm_astream(llm=d, messages=[{"role": "user", "content": "say ok"}],
                                        model_name="glm-5.2", agent_name="smoke")
        print("astream ->", str(r)[:250])
    except Exception as e:
        print("astream ERR:", repr(e)[:400])
asyncio.run(main())
