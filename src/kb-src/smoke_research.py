# -*- coding: utf-8 -*-
import asyncio, sys
sys.path.insert(0, '/data/agents')
import deepresearch_agent
async def main():
    r = await deepresearch_agent.arun("K3 pico 开发板的硬件规格和扩展接口有哪些？",
        record=lambda ev: print(ev.get('type'), str(ev.get('node',''))[:20], str(ev.get('head',''))[:70]),
        timeout=600)
    print('REPORT-LEN:', len(r))
    print('REPORT-HEAD:', r[:600].replace('\n', ' | '))
asyncio.run(main())
