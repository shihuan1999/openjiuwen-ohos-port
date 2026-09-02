# openJiuwen 设备端 API 调用指南（供应用集成）

> 面向 OHOS riscv64 设备（K3 pico / OH 6.1）上的 **HAP 应用、Web 前端、脚本**。
> 所有接口均为本机 HTTP/WS，监听 127.0.0.1（部分 0.0.0.0），应用侧直接调用即可。
> 实证消费者：AgentHub HAP（五 Tab 商店/会话/知识库）、kbapp HAP、swarm_smoke.py。

## 服务总览

| 服务 | 地址 | 组件（brew 包） | 说明 |
|---|---|---|---|
| agents sidecar | `http://127.0.0.1:8765` | `openjiuwen-apps`（`openjiuwen-agents` 启动） | 应用诊断/性能/深度研究/职业/KB 五类 agent + 商店 v3 + 知识库 |
| AgentServer | `ws://127.0.0.1:18600` | `openjiuwen-swarm`（`jiwenswarm-agentserver`） | jiuwenswarm 多智能体，E2A 信封协议 |
| memory_server | `http://127.0.0.1:8000` | `openjiuwen-agent-memory`（`agent-memory-server`） | 长期记忆：本地哈希 embedding + SQLite 向量库 |
| 本地模型栈 | `http://127.0.0.1:<port>` | ohos-kb v1.1（qwen2.5-0.5B + bge） | 可选，离线推理/嵌入（见 ohos-kb 仓） |

依赖闭包/启动环境由 `openjiuwen` 基础包提供（`LD_PRELOAD=libriscvflush.so`、
`TMPDIR`、`SSL_CERT_FILE` 等，launcher 已内置）。

## 1. agents sidecar（HTTP :8765）

健康与任务：

```bash
curl http://127.0.0.1:8765/api/health
# 提交 agent 任务（agent: diag|perf|deepresearch|career|kb）
curl -X POST http://127.0.0.1:8765/api/run -H 'Content-Type: application/json' \
  -d '{"agent":"diag","input":"检查当前系统状态"}'
# {"task_id":"..."} → 轮询
curl http://127.0.0.1:8765/api/task/<task_id>
```

Agent Store（商店 v3）：

```bash
curl http://127.0.0.1:8765/api/store            # 列出商店 agent
curl -X POST http://127.0.0.1:8765/api/store/toggle -d '{"id":"deepresearch"}'
```

知识库（ohos-kb 后端）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/kb/docs` | 文档列表 |
| POST | `/api/kb/doc/new` | 新建文档 |
| PUT / DELETE | `/api/kb/doc/{id}` | 更新 / 删除 |
| POST | `/api/kb/favorite` | 收藏切换 |
| GET | `/api/kb/search?q=...` | 混合检索 |
| GET / POST | `/api/kb/notes`, `/api/kb/note/new`, DELETE `/api/kb/note/{id}` | 笔记 |
| GET | `/api/kb/meta` | 库元信息 |
| POST | `/api/kb/upload`（multipart） | 上传 md/html/docx/xlsx/pdf |
| GET | `/api/kb/uploads` | 已上传列表 |
| POST | `/api/kb/ask` | RAG 问答 |
| POST | `/api/kb/eval` | RAGAS 评测 |
| GET | `/api/kb/models` | 本地模型栈状态 |

云端同步：`POST /api/sync_now`、`GET /api/sync_status`、`/api/sync_url`。

### HAP（ArkTS）调用示例

```typescript
import { http } from '@kit.NetworkKit';

async function kbAsk(question: string): Promise<string> {
  const client = http.createHttp();
  const resp = await client.request('http://127.0.0.1:8765/api/kb/ask', {
    method: http.RequestMethod.POST,
    header: { 'Content-Type': 'application/json' },
    extraData: JSON.stringify({ question }),
    expectDataType: http.HttpDataType.STRING,
    connectTimeout: 60000, readTimeout: 300000,
  });
  client.destroy();
  return JSON.parse(resp.result as string);
}
```

注意：HAP 需声明 `ohos.permission.INTERNET`；本机回环调用不受 ACL 限制。
AgentHub HAP（本仓 `src/`）即以此模式工作。

## 2. AgentServer（WS :18600，E2A 信封）

```python
# 设备端（openjiuwen-swarm 自带 swarm_smoke.py 同款）
import asyncio
from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.gateway.routing.agent_client import WebSocketAgentServerClient

async def ask(query: str) -> str:
    client = WebSocketAgentServerClient()
    await client.connect("ws://127.0.0.1:18600")
    env = E2AEnvelope(request_id="req-app-1", method="chat.send",
                      channel="cli", is_stream=False, params={"query": query})
    resp = await client.send_request(env, timeout=300)
    await client.disconnect()
    return getattr(resp, "content", None) or getattr(resp, "result", str(resp))

asyncio.run(ask("你运行在什么硬件上？"))
```

信封字段：`request_id`（幂等键）、`method`（`chat.send` 等）、`channel`、
`is_stream`、`params`。需要真实 LLM：先在 `$HOME/.jiwenswarm/config/.env`
配置 `API_BASE/API_KEY/MODEL_NAME`（`jiwenswarm-init` 生成模板）。

非 Python 客户端：E2A 为 JSON 信封 over WebSocket，任意语言可实现
（`method=chat.send`，响应按 `request_id` 关联）。

## 3. memory_server（HTTP :8000）

设备端把上游 `APIEmbedding` 替换为本地哈希 embedding（离线、确定性），
接口复用上游 `jiuwen_memory.server.memory_server`（agent-memory 仓）。

```bash
# 健康检查与服务信息
curl http://127.0.0.1:8000/
# 记忆写入/检索（上游 memory_server 语义：/memory 及检索端点）
curl -X POST http://127.0.0.1:8000/memory -H 'Content-Type: application/json' \
  -d '{"role":"user","content":"项目A部署在K3 pico"}'
curl 'http://127.0.0.1:8000/memory/search?q=K3'
```

字段与端点细节以 agent-memory 仓 `jiuwen_memory/server/memory_server.py`
为准（fork riscv64-ohos 分支 README 有设备端差异说明：哈希 embedding 替换、
SQLite 向量库路径 `$OJW_HOME/data/vecstore.db`）。

## 4. 组件安装矩阵（独立安装）

```bash
. /data/harmonybrew/hbrew-env.sh
brew install hbrew/riscv/openjiuwen            # 基础闭包（agent-core develop 线）
brew install hbrew/riscv/openjiuwen-swarm      # AgentServer
brew install hbrew/riscv/openjiuwen-apps       # sidecar（:8765）
brew install hbrew/riscv/openjiuwen-agent-memory   # memory_server（:8000）
brew install hbrew/riscv/openjiuwen-deepsearch     # 深度研究 agent 引擎
brew install hbrew/riscv/openjiuwen-careersim      # 职业模拟 runner
brew install hbrew/riscv/openjiuwen-agent-runtime  # 运行时框架（源码包）
brew install hbrew/riscv/openjiuwen-agent-protocol # A2A/MCP/Registry
brew install hbrew/riscv/openjiuwen-agent-tools    # 工具集
brew install hbrew/riscv/openjiuwen-skillhub       # 技能市场
brew install hbrew/riscv/openjiuwen-agent-dx       # 分布式执行器
brew install hbrew/riscv/openjiuwen-symbiosis      # 具身智能（mock 可跑）
```

服务自启动：`brew-services start openjiuwen-apps`（见 brew-services 框架）。

## 5. 排错速查

- 服务未起：`hilog` 无输出时先看 `$HOME/ojw-run` 下日志；launcher 会 `cd` 到该目录；
- SSL 报错：确认 `/etc/ssl/certs/cacert.pem` 存在（launcher 已自动设 `SSL_CERT_FILE`）;
- python 冲突：所有 launcher 均 `unset PYTHONHOME PYTHONPATH` 后重建，应用侧勿再注入；
- `WebAssembly.Memory` 失败：node 必须 trapfix 版（见 node riscv64-ohos 分支补丁 0001）。
