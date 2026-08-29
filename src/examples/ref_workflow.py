"""PC reference run: agent-core quickstart WorkflowAgent against rvcompute glm-5.2."""
import os
import sys
import asyncio
import json

os.environ.setdefault("API_BASE", "https://api.rvcompute.com:60000/v1")
os.environ.setdefault("API_KEY", "sk-YOUR_API_KEY")
os.environ.setdefault("MODEL_PROVIDER", "openai")
os.environ.setdefault("MODEL_NAME", sys.argv[1] if len(sys.argv) > 1 else "glm-5.2")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.workflow import Workflow, WorkflowCard
from openjiuwen.core.workflow import Start, End, LLMComponent, LLMCompConfig, generate_workflow_key

model_client_config = ModelClientConfig(
    client_provider=os.getenv("MODEL_PROVIDER"),
    api_key=os.getenv("API_KEY"),
    api_base=os.getenv("API_BASE"),
    verify_ssl=os.getenv("LLM_SSL_VERIFY").lower() == "true",
)
model_config = ModelRequestConfig(model=os.getenv("MODEL_NAME"))

workflow_card = WorkflowCard(
    id="generate_text_workflow", name="generate_text", version="1.0",
    description="Generate text based on user input",
    input_params={"type": "object",
                  "properties": {"query": {"type": "string", "description": "User input"}},
                  "required": ["query"]},
)

flow = Workflow(card=workflow_card)
start = Start()
end = End({"responseTemplate": "Workflow output text: {{output}}"})
llm_config = LLMCompConfig(
    model_client_config=model_client_config,
    model_config=model_config,
    template_content=[
        {"role": "system",
         "content": "You are an AI assistant that can help me complete tasks.\nNote: Please do not reason, just output the result directly!"},
        {"role": "user", "content": "{{query}}"}],
    response_format={"type": "json"},
    output_config={"type": "object", "description": "LLM output schema",
                   "properties": {"output": {"type": "string", "description": "LLM output"}},
                   "required": ["output"]},
)
llm = LLMComponent(llm_config)
flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
flow.add_workflow_comp("llm", llm, inputs_schema={"query": "${start.query}"})
flow.set_end_comp("end", end, inputs_schema={"output": "${llm.output}"})
flow.add_connection("start", "llm")
flow.add_connection("llm", "end")

Runner.resource_mgr.add_workflow(
    WorkflowCard(id=generate_workflow_key(flow.card.id, flow.card.version)),
    lambda: flow,
)

agent_config = WorkflowAgentConfig(id="hello_agent", version="0.1.1", description="First Agent")
workflow_agent = WorkflowAgent(agent_config)
workflow_agent.add_workflows([flow])


async def main():
    invoke_result = await Runner.run_agent(
        workflow_agent, {"query": "Hello, please generate a joke, no more than 20 characters"})
    output_result = invoke_result.get("output").result
    print(f"WorkflowAgent output result >>> {output_result.get('response')}")


asyncio.run(main())
