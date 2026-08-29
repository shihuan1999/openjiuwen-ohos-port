# -*- coding: utf-8 -*-
import asyncio, sys
sys.path.insert(0, '/data/agents')
import career_agent
async def main():
    r = await career_agent.arun("quick smoke game", record=lambda ev: print(ev.get('type'), str(ev.get('head',''))[:60]), max_turns=4)
    print('RESULT-HEAD:', r[:300].replace('\n', ' | '))
asyncio.run(main())
