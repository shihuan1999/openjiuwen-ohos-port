[1][18:37:59] Not support std mode
"""OHOS app-diagnosis agent v2 (kernel-aware).

ReActAgent + LocalFunction wrapping OHOS DFX commands per community best practice:
FaultLogger -> HiLog -> HiDumper/bm/aa/param -> HiSysEvent history -> dmesg,
plus a guarded expert shell (diag_shell) for deep kernel-level triage.
Every subprocess call is recorded into EVENTS (type=cmd) so the UI can show
exact execution steps; LLM reasoning is captured via an AFTER_MODEL_CALL
callback (type=thought).

Runs on K3 pico (10.0.91.108) under /data/python312. Usage: python3 app_diag_agent.py [model] [query]
"""
import asyncio
import json
import os
import shlex
import subprocess
import sys
import time

os.environ.setdefault("API_BASE", "https://api.rvcompute.com:60000/v1")
os.environ.setdefault("API_KEY", "sk-YOUR_API_KEY")
os.environ.setdefault("MODEL_PROVIDER", "openai")
os.environ.setdefault("MODEL_NAME", sys.argv[1] if len(sys.argv) > 1 else "glm-5.2")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

FAULTLOG_DIR = "/data/log/faultlog"
FAULTLOG_SUBDIRS = ("faultlogger", "freeze")

EVENTS = []  # consumed by the HTTP sidecar


def _record(ev):
    EVENTS.append(ev)


def _clean(v) -> str:
    """Normalize LLM-supplied scalars: None/null/undefined become empty string."""
    s = str(v if v is not None else "").strip()
    return "" if s.lower() in ("none", "null", "undefined") else s



def _run(cmd, timeout=20, shell=False):
    t0 = time.time()
    argv = ["/bin/sh", "-c", cmd] if shell else cmd.split()
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout)
        out = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace").strip()
        rc = p.returncode
    except FileNotFoundError:
        out, rc = "ERROR: command not found: " + cmd, 127
    except subprocess.TimeoutExpired:
        out, rc = "ERROR: timed out after %ss: %s" % (timeout, cmd), 124
    ms = int((time.time() - t0) * 1000)
    _record({"type": "cmd", "cmd": cmd[:220], "rc": rc, "ms": ms,
             "head": out[:150].replace("\n", " | ")})
    return out or "(no output)"


def _cap(text, head=60, tail=45):
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text
    return "\n".join(lines[:head] +
                     ["...[omitted %d lines]..." % (len(lines) - head - tail)] +
                     lines[-tail:])


def hilog_recent(keyword: str = "", max_lines: int = 80) -> str:
    """Search recent system logs (hilog buffer dumped once). keyword: case-insensitive substring such as a tag(C01800/SAMGR), process or error text; empty means latest lines."""
    dump = _run("hilog -x", timeout=30)
    if str(keyword).strip():
        kw = str(keyword).lower()
        picked = [ln for ln in dump.splitlines() if kw in ln.lower()]
    else:
        picked = dump.splitlines()
    n = max(20, min(int(max_lines), 300))
    got = picked[-n:]
    return _cap("\n".join(got) if got else "(no lines matching %r)" % keyword, 40, 40)


def list_faultlogs() -> str:
    """List FaultLogger fault logs (cppcrash/jscrash/appfreeze/sysfreeze) newest first with age in days."""
    rows = []
    for sub in FAULTLOG_SUBDIRS:
        d = os.path.join(FAULTLOG_DIR, sub)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            fp = os.path.join(d, name)
            try:
                st = os.stat(fp)
                rows.append((st.st_mtime, sub, name, st.st_size))
            except OSError:
                continue
    rows.sort(reverse=True)
    now = time.time()
    out = ["total=%d fault logs" % len(rows)]
    for mt, sub, name, size in rows[:15]:
        out.append("[%s] %s (%dB, %.1f days ago)" % (sub, name, size, (now - mt) / 86400))
    return "\n".join(out)


def read_faultlog(filename: str, max_lines: int = 100) -> str:
    """Read one fault log by filename (head+tail) to analyze crash reason. filename comes from list_faultlogs."""
    name = os.path.basename(str(filename))
    for sub in FAULTLOG_SUBDIRS:
        fp = os.path.join(FAULTLOG_DIR, sub, name)
        if os.path.isfile(fp):
            data = open(fp, "rb").read().decode("utf-8", "replace")
            lines = data.splitlines()
            n = max(30, min(int(max_lines), 400))
            head_n = min(n // 2 + 10, len(lines))
            body = "\n".join(lines[:head_n])
            if len(lines) > head_n:
                body += "\n...\n" + "\n".join(lines[-(n - head_n):])
            return _cap("== %s (%d lines) ==\n%s" % (fp, len(lines), body), 80, 60)
    return "ERROR: %s not found under %s/{faultlogger,freeze}" % (name, FAULTLOG_DIR)


def bundle_info(bundle_name: str = "") -> str:
    """Query installed application info via bm(bundle manager). Empty name lists all bundle names."""
    bn = _clean(bundle_name)
    if bn:
        return _cap(_run("bm dump -n %s" % shlex.quote(bn), timeout=25), 70, 25)
    return _cap(_run("bm dump -a", timeout=25), 60, 0)


def ability_dump() -> str:
    """Dump running UIAbility/ServiceExtension instances and lifecycle state."""
    return _cap(_run("aa dump -a", timeout=25), 50, 30)


def get_param(key: str) -> str:
    """Read one OHOS system parameter, e.g. const.product.software.version."""
    k = _clean(key)
    if not k:
        return "ERROR: need a parameter key"
    return _cap(_run("param get %s" % shlex.quote(k)), 30, 0)


def dmesg_tail(n: int = 80, keyword: str = "") -> str:
    """Kernel ring buffer (dmesg) tail, optionally filtered by case-insensitive keyword. Best for driver/IRQ/OOM issues."""
    dump = _run("dmesg", timeout=25)
    lines = dump.splitlines()
    kw = _clean(keyword).lower()
    if kw:
        lines = [ln for ln in lines if kw in ln.lower()]
    n = max(20, min(int(n), 400))
    got = lines[-n:]
    return _cap("\n".join(got) if got else "(no matching kernel messages)", 50, 30)


def hisysevent_recent(etype: str = "FAULT", domain: str = "", event_name: str = "", max_lines: int = 60) -> str:
    """Query HiSysEvent history. etype: FAULT|STATISTIC|SECURITY|BEHAVIOR; or filter by domain(+event_name). Shows system-level faults like SERVICE_BLOCK, CPP_CRASH, POWER."""
    et = _clean(etype).upper()
    if et not in ("FAULT", "STATISTIC", "SECURITY", "BEHAVIOR"):
        et = "FAULT"
    m = max(20, min(int(max_lines), 200))
    dom, evn = _clean(domain), _clean(event_name)
    if dom:
        cmd = "hisysevent -l -o %s -m %d" % (shlex.quote(dom), m)
        if evn:
            cmd = "hisysevent -l -o %s -n %s -m %d" % (shlex.quote(dom), shlex.quote(evn), m)
    else:
        cmd = "hisysevent -l -g %s -m %d" % (et, m)
    out = _run(cmd, timeout=30)
    return _cap(out, m // 2 + 5, 20)


DIAG_SHELL_BLOCKED = ("rm -rf /", "mkfs", "dd if=/dev/", "reboot", "halt", "poweroff",
                      "shutdown", "fastboot", "flashd", "fdisk", "wipe ", "mkswap",
                      ":(){", "chmod 000", "mknode /dev")


def diag_shell(cmd: str) -> str:
    """EXPERT escape hatch: run ONE arbitrary read-only-ish shell command with 25s timeout for deep kernel/user-space triage (e.g. cat /proc/buddyinfo, cat /sys/kernel/debug/..., service_control, ps -T -p PID). Destructive commands are blocked. Prefer dedicated tools first."""
    c = str(cmd).strip()
    if not c:
        return "ERROR: empty command"
    low = c.lower()
    for b in DIAG_SHELL_BLOCKED:
        if b in low:
            _record({"type": "cmd", "cmd": "[BLOCKED] " + c[:200], "rc": -1, "ms": 0, "head": ""})
            return "BLOCKED: '%s' is not allowed on this device" % b
    return _cap(_run(c, timeout=25, shell=True), 55, 35)


def make_tool(tid, desc, func, props, req):
    import functools

    @functools.wraps(func)
    def wrapped(*a, **k):
        t0 = time.time()
        try:
            args_s = json.dumps(k, ensure_ascii=False, default=str)[:300]
        except Exception:
            args_s = str(k)[:300]
        EVENTS.append({"type": "tool_start", "tool": tid, "args": args_s})
        try:
            r = func(*a, **k)
        except Exception as e:
            EVENTS.append({"type": "tool_error", "tool": tid,
                           "detail": str(e)[:300], "ms": int((time.time() - t0) * 1000)})
            raise
        EVENTS.append({"type": "tool_done", "tool": tid,
                       "preview": str(r)[:400], "ms": int((time.time() - t0) * 1000)})
        return r

    return LocalFunction(
        card=ToolCard(id=tid, name=tid, description=desc,
                      input_params={"type": "object",
                                    "properties": props, "required": req}),
        func=wrapped)


DIAG_TOOLS = [
    make_tool(
        "list_faultlogs",
        "List FaultLogger fault logs (cppcrash/jscrash/appfreeze/sysfreeze) newest first, with age in days. Use before read_faultlog.",
        lambda: list_faultlogs(), {}, []),
    make_tool(
        "read_faultlog",
        "Read one fault log file (head+tail) to analyze crash reason. filename comes from list_faultlogs.",
        read_faultlog,
        {"filename": {"type": "string"}, "max_lines": {"type": "integer"}},
        ["filename"]),
    make_tool(
        "hilog_recent",
        "Search recent system logs (hilog buffer dumped once). keyword: case-insensitive substring such as a tag(C01800/SAMGR), process or error text; empty means latest lines.",
        hilog_recent,
        {"keyword": {"type": "string"}, "max_lines": {"type": "integer"}},
        []),
    make_tool(
        "hisysevent_recent",
        "Query HiSysEvent history records. Prefer etype FAULT for system faults; or filter by domain like POWERMGR/RENDER_SERVICE and event_name.",
        hisysevent_recent,
        {"etype": {"type": "string"}, "domain": {"type": "string"},
         "event_name": {"type": "string"}, "max_lines": {"type": "integer"}},
        []),
    make_tool(
        "dmesg_tail",
        "Kernel ring buffer tail (driver errors, IRQ storms, OOM killer, DRM/VSync issues). Optional keyword filter.",
        dmesg_tail,
        {"n": {"type": "integer"}, "keyword": {"type": "string"}},
        []),
    make_tool(
        "bundle_info",
        "Query installed application info via bm. Empty name lists all bundle names.",
        bundle_info,
        {"bundle_name": {"type": "string"}},
        []),
    make_tool(
        "ability_dump",
        "Dump running UIAbility/ServiceExtension instances and their lifecycle state.",
        lambda: ability_dump(), {}, []),
    make_tool(
        "get_param",
        "Read one OHOS system parameter, e.g. const.product.software.version.",
        get_param,
        {"key": {"type": "string"}},
        ["key"]),
    make_tool(
        "diag_shell",
        "EXPERT escape hatch for deep kernel/user-space triage: run ONE guarded shell command (25s timeout). Examples: cat /proc/buddyinfo; cat /proc/interrupts; ps -T -p <pid>; cat /sys/kernel/debug/dri/0/summary. Destructive commands blocked. Prefer dedicated tools first.",
        diag_shell,
        {"cmd": {"type": "string"}},
        ["cmd"]),
]

DEFAULT_QUERY = (
    "请对这台 OpenHarmony 设备做一次深入的应用健康诊断："
    "1) list_faultlogs 查看故障日志；若 7 天内有日志，read_faultlog 读最新一条并总结故障类型与原因；"
    "2) hisysevent_recent(etype=\"FAULT\") 看系统级故障事件；"
    "3) get_param(key=\"const.product.software.version\") 获取系统版本；"
    "4) 若怀疑内核问题（如 DRM/VSync/OOM），用 dmesg_tail 或 diag_shell 深入验证；"
    "5) 用中文输出结构化诊断报告：版本、问题清单及证据、根因分析、建议。"
)

model_client_config = ModelClientConfig(
    client_provider=os.getenv("MODEL_PROVIDER"),
    api_key=os.getenv("API_KEY"),
    api_base=os.getenv("API_BASE"),
    verify_ssl=os.getenv("LLM_SSL_VERIFY").lower() == "true")


def _configure(agent):
    agent.configure(ReActAgentConfig(
        model_name=os.getenv("MODEL_NAME"),
        model_client_config=model_client_config,
        model_config_obj=ModelRequestConfig(model=os.getenv("MODEL_NAME")),
        max_iterations=12))
    for t in DIAG_TOOLS:
        agent.ability_manager.add(t.card)
    return agent


async def _register_thought_hook(agent):
    counter = {"n": 0}

    async def _on_model_call(ctx):
        try:
            resp = getattr(ctx.inputs, "response", None)
            if resp is None:
                return
            content = getattr(resp, "content", "") or ""
            if isinstance(content, list):
                content = "".join(str(x) for x in content)
            calls = []
            for tc in (getattr(resp, "tool_calls", None) or []):
                nm = getattr(tc, "name", None)
                if nm is None and isinstance(tc, dict):
                    nm = tc.get("name", "")
                if nm:
                    calls.append(str(nm))
            text = str(content).strip()
            if not text and not calls:
                return
            counter["n"] += 1
            EVENTS.append({"type": "thought", "iter": counter["n"],
                           "text": text[:1200], "plan": ", ".join(calls)[:200]})
        except Exception as e:
            EVENTS.append({"type": "thought_error", "detail": repr(e)[:150]})

    await agent.register_callback(AgentCallbackEvent.AFTER_MODEL_CALL, _on_model_call)
    return agent


async def build_agent():
    """Fresh instrumented agent per run (used by the HTTP sidecar worker thread)."""
    agent = ReActAgent(card=AgentCard(id="ohos_app_diag_agent", name="ohos_app_diag_agent",
                                      description="OpenHarmony app diagnosis agent v2"))
    _configure(agent)
    await _register_thought_hook(agent)
    return agent


AGENT = _configure(ReActAgent(card=AgentCard(
    id="ohos_app_diag_agent", name="ohos_app_diag_agent",
    description="OpenHarmony app diagnosis agent")))

for _t in DIAG_TOOLS:
    Runner.resource_mgr.add_tool(_t)


async def main():
    query = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_QUERY
    result = await Runner.run_agent(agent=AGENT, inputs={"query": query})
    out = result.get("output")
    print("\n===== AppDiagAgent final result =====")
    print(getattr(out, "result", out))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
