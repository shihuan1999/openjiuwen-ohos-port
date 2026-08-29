# openJiuwen OHOS riscv64 移植 — 构建与部署全记录

> 2026-08-24 全量闭环 ✅:WorkflowAgent 与 ReActAgent+LocalFunction 均在设备
> 10.0.91.108 上真实调用 rvcompute glm-5.2 跑通。
> **2026-08-25 Python 3.12.14 共存环境 `/data/python312` 上同样全闭环 ✅**
> (import 闭包 + ref_workflow + ReAct 双示例通过,`pip 26.2.1`)。
> 原则:**所有编译在编译服务器 snode7(heshihuan@10.0.50.17, 256C, 上限 -j64)完成,
> 本地 PC 只做文件中转(下载/上传)与源码编辑。**

## Python 3.12.14 交叉编译(2026-08-25,复用 python-ohos-port 3.11 配方)

- 源码 python.org `Python-3.12.14.tar.xz`;host build-python:snode7 自编 `~/py312-host`(x86 gcc,--disable-test-modules)。
- 依赖库直接复用 3.11 的 staging(rootfs/data/python3/lib),**但注意 trim 会删 staging include,需从各源码目录重收头文件**(zlib-src/bzip2-src/xz-5.6.4/third_party sqlite/libffi build 目录/expat-2.7.2/ncurses snapshots/readline-8.3 tarball 重解/openssl-src)。
- configure:`--host=riscv64-unknown-linux-musl --build=x86_64-linux-gnu --prefix=/data/python312 --with-build-python=~/py312-host/bin/python3.12 --without-ensurepip --with-openssl=<staging> CPPFLAGS/LDFLAGS 指向 staging`,config.site 同 3.11 四行。
- **3.12 交叉坑**:
  1. 模块探测升级为运行时测试 → `_bz2` 判 missing(config.site 的 `py_cv_module_*` 无效,被预置 n/a)。解法:`Modules/Setup.local` 写 `*shared*` + `_bz2 _bz2module.c -I<staging>/include -L<staging>/lib -lbz2 -Wl,-rpath,/data/python312/lib`(源文件名是 `_bz2module.c` 不是 `_bz2.c`);staging 的 libbz2.so 是断链,需重编(`gcc -shared -fPIC blocksort.c bzlib.c compress.c crctable.c decompress.c huffman.c randtable.c`)。
  2. nis/_uuid 被宿主 pkg-config/tirpc 误探测为 yes → 链接失败。解法:configure 后 `sed -i 's/Modules\/nis\$(EXT_SUFFIX) //; s/Modules\/_uuid\$(EXT_SUFFIX) //' Makefile`(**每次重新 configure 后都要重做**,SHAREDMODS 是固化列表)。
  3. `Python/deepfreeze/deepfreeze.c`(14 万行)在 -O3 下 make 反复被杀(进程无声消失)→ 单独 `-O1` 编出 deepfreeze.o 再续 make 即跳过。
- EXT_SUFFIX 仍为 `.cpython-312-riscv64-linux-gnu.so`(musllinux wheel 的 .so 照旧 musl→gnu 复制改名)。
- trim 只在打包副本(`pack312`)上做,staging 保持完整以便增量重编;产物 `python3.12.14-ohos-riscv64-v2.tar.gz`(19M)。
- 设备:`tar -C /` 解出 `/data/python312`;`env.sh`(PATH/LD_LIBRARY_PATH=自身 lib/LD_PRELOAD=libriscvflush.so/TMPDIR/SSL_CERT_FILE);libriscvflush.so 与 libjpeg.so.9.6.0 从 3.11 目录复制;`python3.12 -m ensurepip` 后升 pip 26.2.1;pip.conf 全局共用。

### 3.12 依赖闭包(全部设备验证通过)
- **Rust 扩展**(snode7 `~/pydantic-cross/` 增量重编,`PYO3_CROSS_PYTHON_VERSION=3.12`,各 ~6s):pydantic-core 2.46.4 / rpds-py / jiter 0.16 / tiktoken 0.14 → `out312/` 组装,后缀 `cpython-312-riscv64-linux-gnu.so`。
- **C 扩展**(PYSRC 换成 `python312-src` 树,setup.py 由 python3.10 驱动,产物名 cpython-310 统一改名):numpy 1.26.4(SVML 禁用)/ Pillow 12.3.0 / pycryptodome 3.23.0(abi3 免改)/ greenlet 3.5.5。
- **纯 py**:`3.11 pip freeze`(63 项,手编包无 dist-info 正好被排除)→ `python3.12 -m pip install -r --no-deps`。
- **musllinux wheel**(官方 PyPI cp312):aiohttp 3.14.3 / yarl / propcache / **lxml 6.1.2 + python-docx 1.2.0(真 WordParser)**;装后 .so `cp` 改 gnu 后缀。
- **stub**:pymilvus、cryptography(直接从 3.11 site-packages 拷贝)。
- 打包:`~/pydantic-cross/device312-bundle.tar.gz`(18.8M)。


## 三机角色

| 角色 | 地址 | 要点 |
|---|---|---|
| 编译机 snode7 | `heshihuan@10.0.50.17` | spacemit clang(默认 target=riscv64-unknown-linux-musl+自带 OH sysroot,免加 --target);Rust 离线工具链 `~/rust-1.98`;python 3.11 头文件 `~/WorkSpace2/python-riscv64/python-src`(Include/ + 顶层 pyconfig.h + libpython3.11.a);python3.10 驱动 setup.py;工作区 `~/pydantic-cross/` |
| 设备 | `root@10.0.91.108` | OH 6.1 riscv64 musl;python 3.11.4 @ `/data/python3`(`. /data/python3/env.sh`);site-packages=`/data/python3/lib/python3.11/site-packages`;⚠️ EXT_SUFFIX=`cpython-311-riscv64-linux-gnu.so`(**gnu 不是 musl**,构建产物必须改名为 gnu) |
| PC | 本机 | 唯一外网;cargo vendor / 下载 wheel 与源码 → scp 中转;refenv(x86 全量,`refenv/`)作为依赖版本基准 |

## 交叉编译流水线

### A. Rust 扩展(pydantic-core / rpds-py / jiter / tiktoken)
1. PC:PyPI JSON API 下 sdist → `cargo vendor vendor` → `.cargo/config.toml` 写 vendored source → tar.xz → scp snode7。
2. snode7 `.cargo/config.toml` 追加:
   ```toml
   [target.riscv64gc-unknown-linux-musl]
   linker = "/data/home2/heshihuan/WorkSpace/spacemit-toolchain-linux-musl-x86_64-oh-20260630/bin/clang"
   ar = "llvm-ar"
   ```
3. 环境:`PATH=~/rust-1.98/bin:$TC/bin` + `PYO3_CROSS=1 PYO3_CROSS_PYTHON_VERSION=3.11 PYO3_CROSS_PYTHON_IMPLEMENTATION=CPython`(**不要** PYO3_CROSS_LIB_DIR,python-src 无 _sysconfigdata 会报错)。
4. `cargo build --release --target riscv64gc-unknown-linux-musl --features pyo3/extension-module -j64`
   (tiktoken 用 `--features python`)。
5. 组装:cdylib(libX.so)改名进包:
   - `pydantic_core/_pydantic_core.cpython-311-riscv64-linux-gnu.so`(纯 py 文件在 sdist `python/pydantic_core/`)
   - `rpds/{__init__.py(手写 stub `from .rpds import *`), rpds.<suffix>.so}`
   - `jiter/{__init__.py(同上), jiter.<suffix>.so}`(cdylib 名叫 libjiter_python 但导出 PyInit_jiter)
   - `tiktoken/_tiktoken.<suffix>.so` + 纯 py(tiktoken/ + tiktoken_ext/)
   Rust 工具链来源:static.rust-lang.org 的 `rust-1.98.0-x86_64-unknown-linux-gnu.tar.xz`(install.sh --without=rust-docs)+ `rust-std-...riscv64gc-unknown-linux-musl.tar.xz`(cp rustlib 目录),装在 `~/rust-1.98`。

### B. C/Cython 扩展(numpy / Pillow / pycryptodome / greenlet)
统一配方(snode7,以 numpy 为例):
```bash
export TC=~/WorkSpace/spacemit-toolchain-linux-musl-x86_64-oh-20260630/bin
export PYSRC=$HOME/WorkSpace2/python-riscv64/python-src
export CC=$TC/clang CXX=$TC/clang++ AR=$TC/llvm-ar
export LDSHARED="$TC/clang -shared"
export CFLAGS="-I$PYSRC/Include -I$PYSRC -O2"   # 指向 3.11 头(驱动解释器是 3.10 无妨)
python3 setup.py build -j 64 ...
# 产物 .so 名为 cpython-310-x86_64-linux-gnu.so → 批量 sed 改成 cpython-311-riscv64-linux-gnu.so
```
- snode7 的 python3.10 用户目录曾装过 setuptools75,会劫持 distutils(`distutils.msvccompiler` 缺失);numpy 构建需系统 setuptools 59.6 + **Cython 3.0.11(--user)**。
- **numpy 1.26.4**:`NPY_DISABLE_SVML=1` 必加(否则宿主探测把 x86 AVX512 SVML .s 塞进链接);`--disable-optimization`;BLAS env 置 None。验证:matmul/linalg/random/fft 全对。
- **Pillow 12.3.0**:① 先交叉编 zlib 1.3.1 与 libjpeg(ijg jpeg-9f,configure --host=riscv64-unknown-linux-musl);② patch setup.py `_add_directory` 开头跳过 `/usr`、`/usr/local` 前缀(否则宿主 glibc 头混入报 `__gnuc_va_list`);③ `setup.py build_clib` → `build_ext -j64 --disable-platform-guessing --disable-tiff --disable-freetype --disable-lcms --disable-webp --disable-jpeg2000 --disable-raqm --disable-xcb`(--disable-* 只属于 build_ext!)→ `build_py`(实际不拷 py,**手动 cp src/PIL/*.py** 进 build/lib/PIL);④ 部署时 libjpeg.so.9.6.0(真文件!tar 只打包了符号链会翻车)+ libz.so.1(设备已有)放 `/data/python3/lib`。验证:PNG+JPEG 读写。
- **pycryptodome 3.23.0**:同配方;abi3 后缀免改。**构建后必须 grep 日志确认 0 个 x86_64-linux-gnu-gcc**(并行&后台子进程曾丢 env 整个编成 x86,产出 NEEDED libc.so.6 → 设备报 "unsupported relocation type 8")。验证:AES-GCM。
- **greenlet 3.5.5**:clang++ -shared 单 .so(早已完成),包体 sdist src/greenlet/ 纯 py 组装。

### C. 纯 Python(直接 PyPI 官方 py3-none-any wheel 中转,不编译)
typing_extensions-4.16.0、attrs、aiohappyeyeballs、aiosignal、
jsonschema-4.26.0 + referencing-0.37.0 + jsonschema_specifications-2025.9.1 +
jsonschema_path-0.5.0 + pathable-0.6.0、
httpx2-2.12.0 + httpcore2-2.12.0 + truststore-0.10.4、
multidict/yarl/frozenlist/propcache(官方纯 wheel)。
aiohttp-3.14.3 无官方纯 wheel → snode7 `AIOHTTP_NO_EXTENSIONS=1 pip wheel --no-build-isolation`。

### D. Stub(有意不移植,调用即 NotImplementedError)
| 模块 | 替谁背 | 原因 |
|---|---|---|
| pymilvus(仅 client.utils.is_successful) | openjiuwen inmemory checkpointer 硬 import | 真库拖 grpcio/orjson/pandas;该 import 是死代码 |
| cryptography(hazmat 最小面) | dashscope api_request_factory 硬 import | Rust+OpenSSL 未移植;加密请求特性设备不用 |
| docx(Document/oxml/table/text) | openjiuwen WordParser 硬 import | 真库需 lxml(C+libxml2),未移植;word 解析功能设备不可用 |

## 设备侧坑
- **EXT_SUFFIX 是 gnu**:所有手工 .so 命名后缀 `.cpython-311-riscv64-linux-gnu.so`。
- **重刷丢失 `/data/python3/lib/libriscvflush.so`**(ctypes/libffi 需要):snode7 重编
  `void __riscv_flush_icache(void*s,void*e,unsigned long f){syscall(__NR_riscv_flush_icache,s,e,f);}`
  clang -shared -fPIC → scp `/data/python3/lib`。libz.so.1.3.1 设备自带。
- pip 装 wheel **文件名必须规范**(name-version-tag.whl,裸 xxx.whl 会被拒且静默)。
- 设备 pip:`. env.sh && python3 -m pip install --no-deps --no-index <规范名>.whl`;手工组装包(pydantic_core/greenlet/rpds/jiter/tiktoken/numpy/PIL/Crypto/stub)直接 tar 展开 site-packages。

## 验证结果(2026-08-24)
- import 闭包:openjiuwen 全链 + WorkflowAgent + LocalFunction ✅
- `examples/ref_workflow.py`(设备):glm-5.2 返回 "Why 6 fear 7? 7 8 9!" ✅
- `examples/react_local_tool.py`(设备):LLM 并行 tool_calls(calculator 128*64=8192 +
  device_status 读真实 /proc/cpuinfo 16 核 rv64imafdcvh/Sv39)→ 中文汇总 ✅
- numpy 1.26.4 / PIL 12.3.0(PNG+JPEG) / pycryptodome AES-GCM / greenlet 切换 ✅

## 软件源生态(2026-08-25 实测)

设备 `~/.config/pip/pip.conf`:
```ini
[global]
index-url = https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
extra-index-url = https://git.spacemit.com/api/v4/projects/33/packages/pypi/simple
```
(需先 `. /data/python3/env.sh`——curl/pip 都依赖 SSL_CERT_FILE)

**各源性质与可用性:**

| 源 | 内容 | OHOS musl 可用性 |
|---|---|---|
| tuna(官方 PyPI 镜像) | 纯 py 全量 + **musllinux_1_2_riscv64**(cp311 实测:aiohttp/yarl/propcache/lxml) | ✅ 直接 pip;**坑:wheel 里 .so 是 musl 后缀,须改/链成 gnu 后缀**(设备 EXT_SUFFIX) |
| spacemit gitlab 项目33 | 2432 包;`linux_riscv64`/`manylinux_2_3x_riscv64` = **glibc**(Bianbu 用),numpy/scipy/pandas/torch/onnxruntime 等海量 | ❌ 二进制不可用(实测 dlopen 报缺 ld-linux-riscv64-lp64d.so.1);纯 py wheel 可用 |
| RISE GitLab 项目56254198 | 49+ 包 manylinux_2_35_riscv64(numpy/scipy/pandas/pillow, py3.10-3.13) | ❌ 同上,glibc |
| Alpine apk 仓库 | musl 原生 riscv64(v3.20+ 有 py3-numpy/scipy;v3.24 是 py3.14) | ❌ apk 格式 + Python≥3.12 ABI(设备 3.11);设备 python 若升 3.12+ 可"解包 apk 白嫖" |

**glibc 兼容层实验(证伪"加 glibc 链接器即可复用"):**
把 Ubuntu 24.04 riscv64 的 ld-linux-riscv64-lp64d.so.1 + libc.so.6(2.39) + libgcc_s/libm 放进设备
LD_LIBRARY_PATH 后,musl ld 能把 glibc 库当普通库加载,但 numpy glibc wheel 卡在
`__isoc23_fscanf: symbol not found` 的重定位失败(musl ld 不做 glibc 符号版本解析;且 glibc
libc.so.6 自带 TLS_TPREL/IFUNC 重定位,OHOS musl 之前已实测拒绝 relocation type 8)。
dlopen 场景双 libc 并存无解;想完整用 glibc 生态只能整套用户态隔离(proot/chroot Bianbu rootfs
+ glibc python),属另一工程。

**musllinux riscv64 时间线**:PyPI 2025-06~08 才开始接受 riscv64 wheel;cibuildwheel≥3.1.2 /
auditwheel 6.2 / 官方 musllinux_1_2_riscv64 镜像就绪;当前发布 musllinux riscv64 的主流包还少,
覆盖在增长——依赖闭包的 musl 二进制缺口(pydantic-core/rpds-py/jiter/tiktoken/greenlet/
pycryptodome/cryptography/orjson)现阶段仍走 snode7 交叉编译流水线,定期重查 PyPI 覆盖即可。

**已解锁(2026-08-25)**:lxml 6.1.2 + python-docx 1.2.0(musllinux+后缀改名)→ WordParser 真功能
可用,可移除 docx stub(装到 site-packages 时同样 musl→gnu 改名);aiohttp/yarl/propcache 可换
官方 musllinux 二进制版(替换自编纯 py 版,提速)。

## LLM 配置
baseURL `https://api.rvcompute.com:60000/v1`,model `glm-5.2`,provider openai,SSL verify off
(key 见 examples/*.py)。tiktoken cl100k 词表可联网拉取。

## agent-memory 仓库迁移 + 鸿蒙知识库 agent(2026-08-27 全闭环 ✅)

**迁移范围**(openJiuwen-ai 组织 19 仓中设备相关的核心三仓):
- **agent-memory** ✅ 全迁移:仓库 `/data/home/repos/agent-memory`(git 完整,site-packages
  经 `.pth` 指向),memory_server 跑在 **:8000**(uvicorn/fastapi),REST `/add_messages/`
  `/search_memory/` 等全接口可用。
- **agent-memory-plugin** ✅(agent-memory 仓库子目录):OpenClaw/code_agent/hermes 三种
  插件形态本质都是调 memory_server REST,设备侧以 kb_agent 的 `ltm_search`/`remember`
  工具落地(即 plugin 的 REST 接入形态),跨会话记忆实测命中。
- **agent-tools** ❌ 不迁移:infer_router(KV Cache 感知推理路由)/openJiuwen-vllm-affinity
  是 vLLM/SGLang GPU 集群的服务端组件,边缘设备不适用。

**设备端架构**(全部零侵入上游代码,经启动器 monkey-patch):
```
/data/agents/memory/start_memory_server.py  # 启动器:patch APIEmbedding/create_vector_store
├── local_hash_embedding.py  # 本地哈希 embedding(384维,FNV双哈希 n-gram,离线确定性)
│    # rvcompute 95 个模型全是 chat,无 embedding API → 自建
├── sqlite_vector_store.py   # BaseVectorStore 的 SQLite+numpy 余弦实现
│    # chromadb(hnswlib C++)/milvus/es/gauss 全不可用 → 自建
└── .env: INDEX_BACKEND=file(markdown+SQLite) + VECTOR_STORE_TYPE=local_sqlite(开关)
```
- 依赖(纯 py,--no-deps 装):fastapi/starlette/uvicorn/aiosqlite/gmssl/jieba/watchdog/
  click/h11/annotated_doc(fastapi 0.141 新增依赖)。
- LLM 抽取链:add_messages → glm-5.2 抽取(用户画像/摘要/语义记忆)→ markdown+chunks 落盘
  → 检索(哈希向量余弦 ×0.65 + bigram ×0.35 混合)实测 0.53~0.57 命中。

**坑(本节新增)**:
1. 设备 sqlite3 **无 FTS5**(官方 python 构建未启用)→ file index 的关键词检索退化,
   全靠向量路径(pure-python cosine fallback 正常)。
2. `semantic_store` 以 `texts=`/`batch_size=` **关键字**调 embed_documents,覆写签名必须对齐;
   FileMemoryIndex.search 则 `await embed_query`(必须 async)。
3. 首次启动有建表/僵尸清理竞态(`no such table: mem_meta_task` 报错一次,表建好后自愈)。
4. `search_user_mem` 硬走 SemanticStore(需 vector_store),file 模式不配 VECTOR_STORE_TYPE
   会静默返回空 → 必须提供向量存储(SQLite 实现即为解)。
5. pip 直接装 fastapi 会拉新版 pydantic → pydantic-core sdist 构建必死,必须 `--no-deps`。
6. 日志路径 `./logs/` 相对 CWD,独立脚本必须先 chdir 到 /data/agents/memory
   (详细日志在 logs/logs/{memory,store,llm}.log,不在 server.log)。
7. **未定根因怪癖(最终模型)**:memory_server 启动后 REST `search_memory` 恒返回空
   (写入/health 正常),必须在外部进程跑一遍**三步配方**才恢复且立即生效、永久保持:
   ①裸 sqlite3 读 memory.db ②FileMemoryIndex 初始化 ③带真实命里的 search——
   **缺一不可**(二分实测:仅① / ①② / ②③ 均无效)。已固化为
   `warmup_index.py` + `boot_start.sh` 的自验证恢复循环(每 45s 真实检索探针
   `search_ok.py`,空则重跑配方,实测开机 +54s 第 1 轮即 OK)。根因疑在
   WAL/懒同步跨进程状态,后续可查 file_index 的初始同步路径。

**鸿蒙知识库 agent**(功能更完善 = 知识库 + 实时硬件 + 长期记忆):
- `/data/agents/kb_agent.py`:kb_docs/kb_search(语料混合检索)/device_probe(cpu|mem|
  thermal|storage|net|drm|audio|all 实时探测)/ltm_search(长期记忆)/remember(写入记忆)。
- 语料 `/data/agents/kb_data/` 5 文件:K3 Pico-ITX 官方规格、官方论坛指南#970、SpacemiT K3
  芯片/生态(官网+公开报道)、设备实时硬件快照(collect_device_hw.sh)、OHOS 移植实录。
- sidecar AGENTS 注册 `"kb"`,端到端 20s 全绿(带来源引用的结构化报告);
  记忆闭环:remember 写入 → 新会话 ltm_search 命中 2 条 ✅。
- AgentHub HAP **第三 Tab「📚 知识库」**(Index.ets tag 分发扩到三分支),hapdev 构建
  安装启动成功(dumpLayout 验证 Tab 渲染)。
- **开机自启**:/vendor/etc/init.pico.cfg 增加 `agentboot` 服务 → /data/agents/boot_start.sh
  (拉起 memory_server:8000 + agent sidecar:8765,端口探测防重)。
- PC 侧档案:memory-deploy/(记忆部署件) kb-deploy/(kb_agent/语料采集/测试脚本)
  kb-corpus/(语料源) agenthub-src/(Index.ets);设备 git /data/home/repos/ohos-agents@55c0b38。
- 前端开发 skill:`~/.zcode/skills/ohos-frontend-dev/`(SKILL.md + agenthub_sidecar/
  hapdev_deep 两参考,沉淀 ArkTS 坑/hdc 传输规则/sidecar 契约/hapdev 深坑)。

## Agent Store v3 + 知识库 HAP + 端云协同（2026-08-28 全闭环 ✅）

### 新迁移组件（openJiuwen-ai 组织 19 仓盘点后的零/轻依赖筛选）
| 组件 | 结论 | 要点 |
|---|---|---|
| **deepsearch**（dev 分支 193f7c9，pin openjiuwen==0.1.17 与设备完全匹配） | ✅ | `/data/agents/pkg/openjiuwen_deepsearch`（.pth 注册）；纯 py 依赖离线装（jinja2/json-repair/tenacity/networkx/aiolimiter/tldextract/markdown/bs4/latex2mathml/mathml2omml/pyvis/python-dotenv/jsonpickle/mdurl/linkify/uc-micro/requests-file/opentelemetry-api+sdk+semconv）；**自定义本地检索引擎** `/data/agents/kb_local_search.py`（CustomLocalSearchConfig 契约：类接受 LocalSearchEngineConfig.model_dump() kwargs + async aresults()，search_engine_name="custom"）；`info_collector_search_method="local"` 不需要任何联网搜索 API |
| **CareerSim-BDCI26** | ✅ | career-emulator 1.0.1 纯 py wheel + **数据集在 career-emulator-bdci26 wheel**（distribution 模式，`CAREER_EMULATOR_DATASET_SOURCE=distribution` 免 git clone）；msgpack 用 sdist 纯 py 文件（fallback.py 自带，删 .pyx/.h）；绕过 jiuwenswarm：`career_agent.py` 直接驱动 GameEngine（new_game/observe/take_action + LLM 选 choice） |
| **agent-dx SDK** | ✅ 真承重 | wheel 打包（pyproject dynamic version 读指针文件 `python/VERSION`（内容"../VERSION"）会炸——staging 目录里 echo 0.1.0 > VERSION 再 pip wheel）；`yuanrong.agentruntime` 纯标准库；sidecar 所有 agent run 经 `store_engine.ModuleAgentExecutor(AgentExecutor)` → `RequestContext(SessionContext(id,None), turn_id, message, output=_NullOutput())` → Complete |
| jiuwenswarm/skillhub/agent-runtime/jiuwensymbiosis/agent-tools/relay/sciencediscovery 等 | ❌ 目录收录 | 重依赖（chromadb/faiss/playwright/k8s/mysql/redis/JVM/Node/机器人本体），商店里标 cloud/na + 原因说明 |

**设备端 stub 新增**：pypdfium2（PdfDocument 占位）、pandas（DataFrame 占位）、IPython.display（IFrame 占位）、pymilvus 扩展（MilvusClient/AnnSearchRequest/WeightedRanker/RRFRanker/DataType/Function/connections/utility + client.search_result.SearchResult + client.types.LoadState）。

### Agent Store v3（sidecar :8765）
- `/data/agents/store_engine.py`：CATALOG 21 条（19 组织仓 + 设备本地 diag/perf/kb）；`/data/agents/store/state.json` 启停/运行计数；执行层走 agent-dx 契约。
- REST：`GET /api/store`（目录+统计 9装/7云/5na，云端 catalog 自动 merge）、`POST /api/store/toggle`、原 `/api/run`/`/api/task` 不变（新式 agent= `async arun(query, record)`，旧式= build_agent()+Runner）。
- LLM 默认 `http://127.0.0.1:16000/v1`（经 USB rport 隧道到 PC 中继，见下）。

### 知识库存储与端云协同
- `/data/agents/kb_store.py`：SQLite `/data/agents/kbapp/kb.db`（docs/notes/chunks/history/meta），无 FTS5 → 关键词走 LIKE+CJK bigram，语义走 local_hash_embedding 384 维 float32 BLOB + numpy 余弦；hybrid=0.55cos+0.30overlap+0.15exact；`list_notes` LEFT JOIN docs 带 doc_title。
- REST：`/api/kb/docs|doc/{id}|doc/{id}/favorite|search|notes|note/{id}|meta`（meta=categories/tags/history/stats）。
- `/data/agents/sync_client.py`：LWW by updated_at；`GET /cloud/api/pull?since=` + `POST /cloud/api/push`；`/api/sync/now|status|cloud-url`；实测双向（设备建文档→0.3s 上云；云端控制台推文档→设备 pull→检索 source=cloud）。

### PC 云端（cloud/）
- `cloud_kb_server.py` :9800（stdlib http.server+sqlite kb_cloud.db）：pull/push/docs/store-catalog。
- `llm_relay.py` :127.0.0.1:16000：设备侧 http → PC TLS → api.rvcompute.com:60000（cert no-verify）。
- **USB 隧道**（本次台架网隔离的解法）：`hdc rport tcp:16000 tcp:16000` + `hdc rport tcp:9800 tcp:9800`（设备 127.0.0.1 → PC 同端口；fport=PC→设备）。设备网络当前状态：eth0 UP 但 DHCP 失效/网关不通（已配静态 10.0.90.156/23 + default gw 10.0.90.254，ARP 可解析但转发被隔离）；时钟也停在 2000 年——`ctypes clock_settime(CLOCK_REALTIME, PC纪元)` 校准（重启丢失）。

### kbapp HAP（com.example.kbapp，/data/hap-dev/work/kbapp）
- agenthub 工程克隆改名（app.json5 bundleName + AppScope string.json）；Index.ets ~1700 行五 Tab：知识库(搜索/语义切换/分类/收藏/新建/详情+笔记)、AI 深研(5引擎选择/事件时间线/Markdown报告/引用溯源/存为文档)、笔记(标签/列表/批注详情)、商店(统计/筛选/卡片/半模态详情/运行/禁用)、我的(同步面板/日志/统计宫格/长期记忆 :8000/设置)。
- `hapdev run kbapp` 一键构建安装启动 ✅；uitest dumpLayout 验证（**hdc shell cat 大 JSON 会挂起，用 hdc file recv**）。
- UI 设计：`kbapp-design/DESIGN_SPEC.md`（546 行，glm-flash-vision 产出）+ mockup.html + screens/*.png（浏览器截图）+ 视觉评审记录（P0=状态语义一致性，已修）。

### 本节新坑
1. **deepsearch llm_config 的 api_key 必须每个角色独立 bytearray**：create_llm_obj 的 finally `zero_secret()` 原地清零，pydantic 会共享同一 bytearray 实例 → 后 3 个角色 Authorization 全是 \x00（现象=APIConnectionError Connection error，中继零请求；httpx.send 补丁打印 header 才定位）。
2. **deepsearch llm_model_factory 不传 model_name**：LLMConfig.hyper_parameters 里带 {"model_name": ...}（工厂会把 hyper_parameters setattr 进 ModelRequestConfig）。
3. deepsearch 必须用 **dev 分支**（main 8/7 的代码 import 旧版 openjiuwen.harness.tools.web_tools，0.1.17 已重构进 web/ 包）。
4. hdc shell 无 awk：`ps -ef|grep` 取 pid 在 PC 侧解析；`hdc file send` 目标必须是带规范文件名的完整路径（目录目标假成功建目录；wheel 文件名不规范 pip 拒装）。
5. sidecar 后台启动要 `< /dev/null` 否则 hdc 通道被 daemon 继承 fd 挂住。
6. **ArkTS 崩溃规避**：DocCard（ForEach item 的 @Builder 内）嵌套 tags ForEach 在特定数据状态下触发 stateMgmt "undefined is not callable"（jscrash 日志在 /data/log/faultlog/faultlogger/）；改固定 3 个条件 Text 后根除。
7. IAB 浏览器批量截图 quirk：goto 后同 tab 第二张截图必失败（"capture failed for guest"），每屏开新 tab 首截。
8. pip 装纯 py 离线包：PC `pip download --platform any --python-version 312 --only-binary=:all: --no-deps` → hdc 发送 → 设备 `pip install --no-index --no-deps`（设备 pip 无 DNS 时）。
