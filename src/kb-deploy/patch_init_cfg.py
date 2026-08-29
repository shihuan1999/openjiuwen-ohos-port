# -*- coding: UTF-8 -*-
"""向 /vendor/etc/init.pico.cfg 的 services 追加 agentboot 服务(JSON 安全编辑)。"""
import json

CFG = "/vendor/etc/init.pico.cfg"
with open(CFG, "r", encoding="utf-8") as f:
    data = json.load(f)

names = [s.get("name") for s in data.get("services", [])]
if "agentboot" in names:
    print("already present")
else:
    data["services"].append({
        "name": "agentboot",
        "path": ["/data/agents/boot_start.sh"],
        "once": 1,
        "uid": "root",
        "gid": ["root"],
    })
    out = json.dumps(data, ensure_ascii=False, indent=4)
    with open(CFG + ".new", "w", encoding="utf-8") as f:
        f.write(out)
    # 回写前再验证一次可解析
    json.loads(open(CFG + ".new", encoding="utf-8").read())
    import shutil
    shutil.move(CFG + ".new", CFG)
    print("patched, services:", [s.get("name") for s in data["services"]])
