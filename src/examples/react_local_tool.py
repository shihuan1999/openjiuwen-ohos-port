"""Device demo: ReActAgent + LocalFunction local tools on OHOS riscv64.

Two local tools are registered (no network needed for the tools themselves):
  - device_status: reads /proc for real SoC/board info
  - calculator:    deterministic arithmetic (a <op> b)

The LLM (rvcompute glm-5.2 via openai provider) drives the ReAct loop and must
call both tools to answer the final query.
"""
import asyncio
import os
import platform
import sys

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


def device_status(part: str = "all") -> str:
    """Return real device status from /proc. part: cpu | mem | all"""
    lines = [f"python={platform.python_version()} arch={platform.machine()}"]
    if part in ("cpu", "all"):
        with open("/proc/cpuinfo") as f:
            for ln in f:
                if ln.startswith(("processor", "isa", "mmu", "hart")):
                    lines.append(ln.strip())
    if part in ("mem", "all"):
        with open("/proc/meminfo") as f:
            lines += [ln.strip() for ln in f if ln.startswith(("MemTotal", "MemFree"))]
    with open("/proc/uptime") as f:
        lines.append("uptime_s=" + f.read().split()[0])
    return "; ".join(lines)


def calculator(a: float, op: str, b: float) -> float:
    """Arithmetic: op is one of + - * / %"""
    ops = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
           "/": lambda: a / b, "%": lambda: a % b}
    if op not in ops:
        raise ValueError(f"unsupported op: {op}")
    return ops[op]()


status_tool = LocalFunction(
    card=ToolCard(
        id="device_status", name="device_status",
        description="Query local device (SoC CPU info, memory, uptime) status. part: cpu|mem|all",
        input_params={"type": "object",
                      "properties": {"part": {"type": "string", "enum": ["cpu", "mem", "all"],
                                              "description": "which part of status, default all"}},
                      "required": []}),
    func=device_status)

calc_tool = LocalFunction(
    card=ToolCard(
        id="calculator", name="calculator",
        description="Compute arithmetic expression with two operands, op in + - * / %",
        input_params={"type": "object",
                      "properties": {"a": {"type": "number"}, "op": {"type": "string"},
                                       "b": {"type": "number"}},
                      "required": ["a", "op", "b"]}),
    func=calculator)

model_client_config = ModelClientConfig(
    client_provider=os.getenv("MODEL_PROVIDER"),
    api_key=os.getenv("API_KEY"),
    api_base=os.getenv("API_BASE"),
    verify_ssl=os.getenv("LLM_SSL_VERIFY").lower() == "true",
)

agent_card = AgentCard(id="device_react_agent", name="device_react_agent",
                       description="ReAct agent on device with local tools")
react_agent = ReActAgent(card=agent_card).configure(
    ReActAgentConfig(
        model_name=os.getenv("MODEL_NAME"),
        model_client_config=model_client_config,
        model_config_obj=ModelRequestConfig(model=os.getenv("MODEL_NAME")),
        max_iterations=8,
    ))

Runner.resource_mgr.add_tool(status_tool)
Runner.resource_mgr.add_tool(calc_tool)
react_agent.ability_manager.add(status_tool.card)
react_agent.ability_manager.add(calc_tool.card)


async def main():
    result = await Runner.run_agent(
        agent=react_agent,
        inputs={"query": ("请先调用 calculator 算出 128*64 的结果，"
                          "再调用 device_status(part=\"cpu\") 查询本机 CPU 信息，"
                          "最后用中文汇总两步结果。")},
    )
    out = result.get("output")
    print("\n===== ReActAgent final result =====")
    print(getattr(out, "result", out))


asyncio.run(main())
