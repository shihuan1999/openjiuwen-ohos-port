[1][18:37:59] Not support std mode
"""OHOS performance-probe agent v2 (kernel-aware).

Tools follow OHOS DFX best practice: SmartPerf(SP_daemon) / hidumper first,
/proc & sysfs sampling as portable fallback, kernel-level views (PSI, slab,
interrupts, dmesg) for deep dives, plus a guarded expert shell.
Every subprocess call and every LLM reasoning step is streamed to EVENTS
for the UI (cmd / thought events).

Runs on K3 pico under /data/python312. Usage: python3 perf_probe_agent.py [model] [query]
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

EVENTS = []


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


def sp_cpu() -> str:
    """One-shot device CPU report via SmartPerf SP_daemon: per-core frequency and usage, total usage, load."""
    out = _run("SP_daemon -N 1 -c", timeout=30)
    if "TotalcpuUsage" not in out:
        return "SP_daemon returned no data: " + _cap(out, 10, 0)
    return _cap(out, 40, 20)


def cpu_top_processes(n: int = 5) -> str:
    """Sample /proc twice (~1s), return overall CPU usage% plus top-N processes by CPU%. No external tool needed."""
    n = max(3, min(int(n), 15))
    clk = os.sysconf("SC_CLK_TCK") or 100

    def snap():
        with open("/proc/stat") as f:
            for ln in f:
                if ln.startswith("cpu "):
                    v = [int(x) for x in ln.split()[1:]]
                    idle = v[3] + (v[4] if len(v) > 4 else 0)
                    break
            else:
                raise RuntimeError("no cpu line")
        procs = {}
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                data = open("/proc/%s/stat" % pid, "rb").read().decode("utf-8", "replace")
                rp = data.rfind(")")
                fields = data[rp + 2:].split()
                procs[pid] = int(fields[11]) + int(fields[12])
            except Exception:
                continue
        return sum(v) - idle, procs

    busy0, p0 = snap()
    t0 = time.monotonic()
    time.sleep(1.0)
    busy1, p1 = snap()
    dt = max(time.monotonic() - t0, 0.2)
    total_pct = 100.0 * (busy1 - busy0) / (dt * clk)
    rows = []
    for pid, t1 in p1.items():
        d = t1 - p0.get(pid, 0)
        if d <= 0:
            continue
        try:
            name = open("/proc/%s/cmdline" % pid, "rb").read().decode(
                "utf-8", "replace").split("\x00")[0]
        except Exception:
            name = "?"
        rows.append((100.0 * d / (dt * clk), pid, name or "(kthread)"))
    rows.sort(reverse=True)
    loadavg = open("/proc/loadavg").read().strip()
    out = ["overall_cpu_usage=%.1f%% (sampled %.1fs)" % (total_pct, dt),
           "loadavg='%s'" % loadavg, "top %d processes by cpu:" % n]
    out += ["  %5.1f%%  pid=%s  %s" % r for r in rows[:n]]
    return "\n".join(out)


def mem_overview() -> str:
    """Whole-device memory water level from /proc/meminfo (Total/Available/Cached/Swap/Slab)."""
    keys = ("MemTotal", "MemFree", "MemAvailable", "Cached", "SwapCached",
            "SwapTotal", "SwapFree", "Slab", "VmallocUsed")
    out = []
    with open("/proc/meminfo") as f:
        for ln in f:
            if ln.split(":")[0] in keys:
                out.append(ln.strip())
    return _cap("\n".join(out), 15, 0)


def app_mem(target: str) -> str:
    """Per-process memory via 'hidumper --mem <pid>' with full PSS breakdown. target: pid or process/bundle-name substring."""
    t = _clean(target)
    if not t:
        return "ERROR: need a pid or process-name substring"
    pid = t if t.isdigit() else None
    head = ""
    if pid is None:
        hits = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                name = open("/proc/%s/cmdline" % entry, "rb").read().decode(
                    "utf-8", "replace").replace("\x00", " ")
            except Exception:
                continue
            if t.lower() in name.lower():
                hits.append((entry, name.strip()))
        if not hits:
            return "ERROR: no process matching '%s'" % t
        pid, pname = hits[0]
        others = ", ".join("%s(%s)" % h for h in hits[1:5])
        head = "matched pid=%s name=%s%s\n" % (pid, pname,
                                               ("; also: " + others) if others else "")
    return _cap(head + _run("hidumper --mem %s" % pid, timeout=30), 55, 25)


def device_sensors() -> str:
    """GPU frequency+load and board temperatures. SmartPerf first; falls back to /sys/class/thermal zones when battery nodes are absent."""
    gpu = _run("SP_daemon -N 1 -g", timeout=30)
    thermal = _run("SP_daemon -N 1 -t", timeout=30)
    has_temp = any(("temp" in ln.lower() or "batt" in ln.lower())
                   for ln in thermal.splitlines())
    if not has_temp:
        zones = []
        base = "/sys/class/thermal"
        if os.path.isdir(base):
            for z in sorted(os.listdir(base)):
                if not z.startswith("thermal_zone"):
                    continue
                try:
                    ztype = open(base + "/" + z + "/type").read().strip()
                    temp = int(open(base + "/" + z + "/temp").read().strip())
                    zones.append("%s: %s = %.1f C" % (z, ztype, temp / 1000.0))
                except Exception:
                    continue
        if zones:
            thermal = "(sysfs thermal fallback)\n" + "\n".join(zones)
    return _cap("[GPU]\n%s\n[Temperature]\n%s" % (gpu, thermal), 35, 15)


def fps_probe(package_name: str) -> str:
    """Measure an application refresh FPS via SP_daemon -f -PKG; only meaningful while the app renders on screen."""
    pkg = _clean(package_name)
    if not pkg:
        return "ERROR: need a foreground bundleName (find one via bundle_info or bm dump -a)"
    return _cap(_run("SP_daemon -f -PKG %s" % shlex.quote(pkg), timeout=30), 30, 10)


    out = ["[PSI]"]
    for k in ("cpu", "memory", "io"):
        try:
            out.append(k + ": " + open("/proc/pressure/" + k).read().strip().replace("\n", " / "))
        except Exception as e:
            out.append("%s: unavailable (%s)" % (k, e))
    keys = ("allocstall", "pgscan_kswapd", "pgsteal_kswapd", "pgscan_direct",
            "pgsteal_direct", "compact_stall")
    out.append("[vmstat reclaim counters]")
    with open("/proc/vmstat") as f:
        for ln in f:
            if any(ln.startswith(k) for k in keys):
                out.append(ln.strip())
    with open("/proc/meminfo") as f:
        for ln in f:
            if ln.startswith(("MemAvailable", "SwapFree")):
                out.append(ln.strip())
    return "\n".join(out)


def slab_interrupts(top_n: int = 8) -> str:
    """Kernel slab caches by memory footprint and busiest interrupt lines — spots kernel-memory hogs and IRQ storms."""
    top_n = max(3, min(int(top_n), 20))
    slabs = []
    try:
        with open("/proc/slabinfo") as f:
            f.readline()
            f.readline()
            for ln in f:
                parts = ln.split()
                if len(parts) < 6:
                    continue
                try:
                    slabs.append((int(parts[2]) * int(parts[4]) // 1024,
                                  parts[0], int(parts[2])))
                except ValueError:
                    continue
    except Exception:
        pass
    lines = ["[slab top%d by KB]" % top_n]
    if slabs:
        slabs.sort(reverse=True)
        lines += ["%6dKB  %-24s x%d" % s for s in slabs[:top_n]]
    ints = []
    try:
        with open("/proc/interrupts") as f:
            f.readline()
            for ln in f:
                parts = ln.split()
                if len(parts) < 3:
                    continue
                try:
                    total = sum(int(x) for x in parts[1:-1])
                    ints.append((total, ln.rstrip()))
                except ValueError:
                    continue
    except Exception:
        pass
    ints.sort(key=lambda x: -x[0])
    lines.append("[interrupts top%d]" % top_n)
    lines += [t[1][:110] for t in ints[:top_n]]
    return "\n".join(lines)


def klog_tail(n: int = 80, keyword: str = "") -> str:
    """Kernel ring buffer tail (driver errors, OOM killer, thermal throttling, DRM issues). Optional keyword filter."""
    dump = _run("dmesg", timeout=25)
    lines = dump.splitlines()
    kw = _clean(keyword).lower()
    if kw:
        lines = [ln for ln in lines if kw in ln.lower()]
    n = max(20, min(int(n), 400))
    got = lines[-n:]
    return _cap("\n".join(got) if got else "(no matching kernel messages)", 50, 30)


PERF_SHELL_BLOCKED = ("rm -rf /", "mkfs", "dd if=/dev/", "reboot", "halt", "poweroff",
                      "shutdown", "fastboot", "flashd", "fdisk", "wipe ", "mkswap",
                      ":(){", "chmod 000")


def perf_shell(cmd: str) -> str:
    """EXPERT escape hatch: run ONE guarded shell command (25s timeout) for deep kernel/user-space performance triage (e.g. cat /proc/sched_debug, cat /sys/kernel/debug/..., SP_daemon variants). Destructive commands blocked. Prefer dedicated tools first."""
    c = str(cmd).strip()
    if not c:
        return "ERROR: empty command"
    low = c.lower()
    for b in PERF_SHELL_BLOCKED:
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


PERF_TOOLS = [
    make_tool(
        "sp_cpu",
        "One-shot whole-device CPU status from SmartPerf SP_daemon: per-core frequency/usage, total usage, load.",
        lambda: sp_cpu(), {}, []),
    make_tool(
        "cpu_top_processes",
        "Sample /proc for ~1s: overall CPU usage% and top-N processes by CPU%.",
        cpu_top_processes,
        {"n": {"type": "integer"}},
        []),
    make_tool(
        "mem_overview",
        "Whole-device memory water level (MemTotal/MemAvailable/Cached/Swap/Slab).",
        lambda: mem_overview(), {}, []),
    make_tool(
        "app_mem",
        "Per-process memory via hidumper --mem with full PSS breakdown. target: pid or name substring.",
        app_mem,
        {"target": {"type": "string"}},
        ["target"]),
    make_tool(
        "mem_pressure",
        "Memory pressure: PSI saturation percentages + kswapd/direct-reclaim counters + MemAvailable. Detects silent thrashing.",
        lambda: mem_pressure(), {}, []),
    make_tool(
        "slab_interrupts",
        "Kernel slab caches by KB and busiest IRQ lines. Spots kernel-memory hogs and interrupt storms.",
        slab_interrupts,
        {"top_n": {"type": "integer"}},
        []),
    make_tool(
        "device_sensors",
        "GPU frequency+load and board temperatures (SP_daemon, sysfs thermal fallback).",
        lambda: device_sensors(), {}, []),
    make_tool(
        "fps_probe",
        "Measure app refresh FPS via SP_daemon -f -PKG; needs the foreground bundleName.",
        fps_probe,
        {"package_name": {"type": "string"}},
        ["package_name"]),
    make_tool(
        "klog_tail",
        "Kernel ring buffer tail for driver/OOM/thermal issues. Optional keyword filter.",
        klog_tail,
        {"n": {"type": "integer"}, "keyword": {"type": "string"}},
        []),
    make_tool(
        "perf_shell",
        "EXPERT escape hatch for deep kernel/user-space performance triage: ONE guarded shell command (25s timeout). Examples: cat /proc/sched_debug; cat /proc/buddyinfo; cat /sys/kernel/debug/wakeup_sources. Destructive commands blocked.",
        perf_shell,
        {"cmd": {"type": "string"}},
        ["cmd"]),
]

DEFAULT_QUERY = (
    "请对这台 OpenHarmony 设备做一次深入的性能体检："
    "1) sp_cpu 看整机 CPU 频率与占用；2) cpu_top_processes(n=5) 找最耗 CPU 进程；"
    "3) mem_overview 与 mem_pressure 评估内存水位与回收压力；"
    "4) device_sensors 看 GPU 与温度；5) 若发现异常，用 klog_tail 或 perf_shell 深入内核定位；"
    "6) 中文输出体检报告：指标、Top 进程、异常项（含内核证据）、优化建议。"
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
    for t in PERF_TOOLS:
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
    agent = ReActAgent(card=AgentCard(id="ohos_perf_probe_agent", name="ohos_perf_probe_agent",
                                      description="OpenHarmony performance probe agent v2"))
    _configure(agent)
    await _register_thought_hook(agent)
    return agent


AGENT = _configure(ReActAgent(card=AgentCard(
    id="ohos_perf_probe_agent", name="ohos_perf_probe_agent",
    description="OpenHarmony performance probe agent")))

for _t in PERF_TOOLS:
    Runner.resource_mgr.add_tool(_t)


async def main():
    query = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_QUERY
    result = await Runner.run_agent(agent=AGENT, inputs={"query": query})
    out = result.get("output")
    print("\n===== PerfProbeAgent final result =====")
    print(getattr(out, "result", out))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())


def mem_pressure() -> str:
    """Memory pressure view: PSI (/proc/pressure/*) + kswapd/direct-reclaim counters from /proc/vmstat + MemAvailable. Detects silent thrashing."""
    out = ["[PSI]"]
    for k in ("cpu", "memory", "io"):
        try:
            out.append(k + ": " + open("/proc/pressure/" + k).read().strip().replace("\n", " / "))
        except Exception as e:
            out.append("%s: unavailable (%s)" % (k, e))
    keys = ("allocstall", "pgscan_kswapd", "pgsteal_kswapd", "pgscan_direct",
            "pgsteal_direct", "compact_stall")
    out.append("[vmstat reclaim counters]")
    with open("/proc/vmstat") as f:
        for ln in f:
            if any(ln.startswith(k) for k in keys):
                out.append(ln.strip())
    with open("/proc/meminfo") as f:
        for ln in f:
            if ln.startswith(("MemAvailable", "SwapFree")):
                out.append(ln.strip())
    return "\n".join(out)