# openJiuwen on OpenHarmony riscv64（K3 pico 设备端全套）

openJiuwen 及其组件在 OpenHarmony 6.1 riscv64（Spacemit K3 pico）上的完整
移植与设备端部署：Python 3.12 交叉编译运行时、依赖闭包（Rust/C 扩展）、
多 agent sidecar、agent-memory 长期记忆、知识库（SQLite 向量库）、
DeepSearch/CareerSim/agent-dx 组件迁移、Agent Store v3、kbapp 五 Tab
HAP 应用与端云协同。

> 完整移植记录（构建流水线、坑、验证）见 [DEPLOY.md](DEPLOY.md)。

## 仓库结构

| 目录 | 内容 |
|---|---|
| `src/device-src/` | 设备端 sidecar v1：agent_server(:8765) + app_diag / perf / kb 三 agent |
| `src/kb-src/` | sidecar v3：kb_store(SQLite+hash embedding)、kb_local_search、deepsearch_agent、career_agent、store_engine(Agent Store v3)、sync_client、agent_server |
| `src/kb-deploy/` | kb_agent v2（接 agent-memory 长期记忆）+ 开机自启补丁 |
| `src/memory-deploy/` | agent-memory server（:8000，自研 hash embedding + SQLite 向量库）部署脚本 |
| `src/agenthub-src/` | AgentHub HAP（设备端 agent 控制 UI）Index.ets |
| `src/kbapp-src/` | 知识库 HAP `com.example.kbapp`（五 Tab，1920×1080）Index.ets + 布局快照 |
| `src/cloud/` | PC 云端：cloud_kb_server(:9800) + llm_relay(:16000，http→TLS 中继) |
| `src/examples/` | 最小示例（本地工具调用 / workflow） |

## 快速部署（Release 资产）

Release 附带四类资产（校验和见各 tar 内 SHA256SUMS）：

| 资产 | 说明 | 设备目标 |
|---|---|---|
| `python3.12.14-ohos-riscv64-v2.tar.gz` | Python 3.12.14 运行时（含 pip/venv/HTTPS） | `/data/python312` |
| `wheels312.tar.gz` | 依赖闭包 wheels（pydantic-core/rpds/jiter/tiktoken/numpy/Pillow/...） | `pip install --no-index` |
| `deploy-pkg.tar.gz` | 迁移组件：openjiuwen_deepsearch(dev 193f7c9) + career_sim_runner + yuanrong(agent-dx) | `/data/agents/pkg` |
| `agent-memory-device.tar.gz` | agent-memory server | `/data/agents/memory` |
| `kbapp-entry-signed.hap` | 知识库 HAP（已签名可直接安装） | `bm install` |

```sh
# 1) Python 运行时（tar 顶层为 data/ 前缀）
mkdir -p /data/python312-gh && tar xzf python3.12.14-ohos-riscv64-v2.tar.gz -C /data/python312-gh
P=/data/python312-gh/data/python312
# 关键：先清掉系统泄漏的 python 环境变量，再设库路径
unset PYTHONHOME PYTHONPATH && export LD_LIBRARY_PATH=$P/lib
$P/bin/python3.12 -V          # Python 3.12.14
# 2) pip 自举（tar 不带 pip，须设备端 ensurepip）+ 依赖 wheels（24 个，离线装）
$P/bin/python3.12 -m ensurepip
$P/bin/python3.12 -m pip install --no-index --find-links=<wheels目录> <包...>
# 3) sidecar
mkdir -p /data/agents && cp src/kb-src/*.py /data/agents/
# 4) LLM 配置（默认走本机中继 127.0.0.1:16000，见 DEPLOY.md「LLM 配置」）
export API_KEY=sk-xxxx          # 真实 key 通过环境变量注入，源码默认占位符
export LLM_BASE_URL=http://127.0.0.1:16000/v1
cd /data/agents && setsid nohup python3.12 agent_server.py > server.log 2>&1 < /dev/null &
curl -s http://127.0.0.1:8765/api/health
# 5) kbapp HAP（Release 已附签名包，设备实测 bm install 一次通过）
/data/hap-dev/bin/withtoken bm install -p kbapp-entry-signed.hap   # 需 LD_LIBRARY_PATH=/system/lib64/platformsdk
```

> 2026-08-29 实测：以上 1/2/5 步在 K3 pico 从 GitHub Release 全流程走通
> （python -V ✓、pip 离线装 click 8.5.0 ✓、kbapp 安装成功 ✓）。

## 关键契约速记（详见 DEPLOY.md）

- **deepsearch `llm_config` 每角色必须独立 bytearray**（zero_secret 原地清零）；
  `llm_model_factory` 不接 model_name，模型名走 `hyper_parameters`。
- CareerSim 免 clone：`CAREER_EMULATOR_DATASET_SOURCE=distribution`。
- agent 执行层统一走 `store_engine.ModuleAgentExecutor`（agent-dx 契约）。
- sidecar 单任务串行；`/api/health` 应报 agents 与 model。
- ArkTS：`@Builder×ForEach` 嵌套重渲染会崩（kbapp DocCard 教训）。
