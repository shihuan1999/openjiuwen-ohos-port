# -*- coding: UTF-8 -*-
"""CareerSim agent: LLM plays the CCF BDCI'26 career survival game
(career-emulator GameEngine) directly on device — no JiuwenSwarm/ws needed.

Sidecar contract (new-style): DEFAULT_QUERY / EVENTS / async arun(query, record).
"""
import asyncio
import json
import os
import re
import time
import urllib.request

os.environ.setdefault("API_BASE", "http://127.0.0.1:16000/v1")
os.environ.setdefault("API_KEY", "sk-YOUR_API_KEY")
os.environ.setdefault("MODEL_NAME", "glm-5.2")
# event dataset ships inside the career_emulator_bdci26 wheel (no git clone)
os.environ.setdefault("CAREER_EMULATOR_DATASET_SOURCE", "distribution")

EVENTS = []
DEFAULT_QUERY = "开始一局职场生存挑战：平衡健康/尊严/技能/人脉/产出/财富，目标是48个月后成功晋升。"
DB_PATH = "/data/agents/kbapp/career.sqlite3"
MAX_TURNS = 80

SYSTEM_PROMPT = (
    "你在玩一个职场生存模拟游戏，扮演一名业务研发组新员工，游戏时长48个月。"
    "每次给你当前状态(JSON)、当前事件与可选行动列表(JSON)。"
    "你要权衡健康、尊严、技能、人脉、产出与财富，做出最有利于长期生存与晋升的选择。"
    "只输出一个数字：你选择的行动编号(choice 字段)，不要输出任何其他内容。"
)


def _record(ev):
    EVENTS.append(ev)


def _llm(prompt, timeout=120):
    body = json.dumps({
        "model": os.getenv("MODEL_NAME", "glm-5.2"),
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
        "temperature": 0.3, "max_tokens": 16,
    }).encode()
    req = urllib.request.Request(
        os.getenv("API_BASE").rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + os.getenv("API_KEY", "")})
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        data = json.loads(r.read())
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")


def _obj(o):
    """best-effort dict-ify a pydantic/dataclass response object."""
    if isinstance(o, dict):
        return o
    if hasattr(o, "model_dump"):
        return o.model_dump()
    if hasattr(o, "__dict__"):
        return {k: v for k, v in vars(o).items()}
    return str(o)


def _choice_id(c):
    for k in ("choice", "id", "index", "number", "no"):
        if isinstance(c, dict) and k in c:
            return c[k]
    return None


async def arun(query, record=_record, max_turns=MAX_TURNS):
    from career_emulator.game import GameEngine

    t0 = time.time()
    engine = GameEngine(db_path=__import__("pathlib").Path(DB_PATH))
    ng = await engine.new_game()
    session = _obj(ng).get("session_id") or ""
    if not session:
        return "new_game failed: %s" % _obj(ng).get("error", ng)
    record({"type": "cmd", "cmd": "career new_game", "rc": 0, "head": "session " + session})

    turns, last_state, ending = 0, {}, ""
    while turns < max_turns:
        turns += 1
        obs = _obj(await engine.observe(session))
        state = obs.get("current_state") or {}
        choices = obs.get("choices") or []
        last_state = state
        if obs.get("warning"):
            record({"type": "node", "node": "warning", "head": str(obs["warning"])[:110]})
        if state.get("game_over") or state.get("ended") or (not choices and turns > 3):
            ending = json.dumps(state.get("ending_score") or state, ensure_ascii=False)[:1200]
            record({"type": "node", "node": "game_over", "head": "at turn %d" % turns})
            break
        if not choices:
            break
        prompt = ("当前状态：%s\n\n当前事件：%s\n\n可选行动：%s\n\n请输出行动编号。"
                  % (json.dumps(state, ensure_ascii=False, default=str)[:1500],
                     json.dumps(_obj(obs.get("current_event") or {}), ensure_ascii=False, default=str)[:600],
                     json.dumps(choices, ensure_ascii=False, default=str)[:2200]))
        try:
            ans = await asyncio.to_thread(_llm, prompt)
        except Exception as e:
            return "LLM error at turn %d: %s" % (turns, e)
        m = re.search(r"\d+", ans or "")
        pick = None
        if m:
            want = int(m.group())
            for c in choices:
                if _choice_id(c) == want:
                    pick = want
                    break
            if pick is None and 0 < want <= len(choices):
                pick = _choice_id(choices[want - 1])
        if pick is None:
            pick = _choice_id(choices[0])
        ev_head = str((_obj(obs.get("current_event") or {}).get("title")
                       or _obj(obs.get("current_event") or {}).get("description") or ""))[:80]
        record({"type": "node", "node": "Q%d" % turns,
                "head": "%s → choice %s" % (ev_head or "action", pick)})
        await engine.take_action(session, int(pick), "")

    obs = _obj(await engine.observe(session))
    last_state = obs.get("current_state") or last_state
    if not ending:
        ending = json.dumps(last_state, ensure_ascii=False, default=str)[:1200]
    keys = ["level", "month", "health", "dignity", "skill", "network", "output", "wealth", "rank"]
    summary = " | ".join("%s=%s" % (k, last_state.get(k)) for k in keys if k in last_state)
    report = ("# 职场模拟战报\n\n- 会话: %s\n- 回合数: %d\n- 用时: %.0fs\n- 终局状态: %s\n\n"
              "## 终局数据\n```json\n%s\n```\n" % (session, turns, time.time() - t0,
                                                    summary or "(见下方 JSON)", ending))
    record({"type": "done", "head": "career finished in %.0fs, %d turns" % (time.time() - t0, turns)})
    return report
