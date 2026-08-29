# -*- coding: UTF-8 -*-
"""变体A: 仅纯 sqlite 读(不初始化 FileMemoryIndex)。"""
import sqlite3

c = sqlite3.connect("/data/agents/memory/file_memory_data/memory.db")
print("A rows:", c.execute(
    "SELECT count(*) FROM chunks WHERE user_id='kb-test' AND scope_id='default' "
    "AND embedding IS NOT NULL").fetchone()[0])
c.close()
