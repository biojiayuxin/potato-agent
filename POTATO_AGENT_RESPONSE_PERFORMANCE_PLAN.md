# Potato Agent 响应速度优化方案

> 状态：待评审、待建立正式基线；本文只定义实施顺序和验收门禁，不代表生产变更授权。
>
> 更新时间：2026-07-24
>
> 上游参考：Hermes Agent v0.19.0（tag `v2026.7.20`），重点参考 PR #59332、#59389、
> #61131、#61414、#63941、#64460、#67236 和 #67788。
>
> 优化目标基线：Potato Hermes Lite `0.16.0+potato.lite.4`。

## 0. 执行摘要

不能把上游“首 token 加速约 80%”直接当作 Potato 的收益目标。上游约 4.3s 到 0.9s 的结果包含 Discord
capability HTTPS 探测（约 2s）和 MCP import（约 0.4s）；Potato 没有 Discord 工具，Lite profile 也已经关闭
MCP。Potato 当前最值得做的优化按收益和风险排序如下：

| 优先级 | Potato 改动 | 主要改善 | 决策 |
|---|---|---|---|
| P0 | 建立 cold/warm/stream 分段基准 | 防止把 provider 推理时间算成 Potato 收益 | 先做 |
| P1 | workspace ready 后预连接 gateway，预热 profile-neutral imports，异步 environment probe | 冷 draft 首次请求；组件预算先由 Phase 0 冻结 | 第一批实现 |
| P1 | 显示 reasoning 流，按 animation frame 合并 delta | 首次可见反馈和长回复流畅度 | feature flag 下实现 |
| P2 | journal-first + projection 批量写、当前消息局部 DOM patch | 长回复 SQLite、CPU 和掉帧 | correctness 测试后实现 |
| P3 | 图片估算、skill/tool 扫描等纯 CPU 优化 | 图片或大量 skills 场景 | 基准证明后实现 |
| 审批项 | mixed tool batch 并发、provider transport 或 durability 语义变更 | 多工具和网络路径 | 不进入首批 |

第一批以 Phase 0 冻结的相对改善和绝对上限共同验收，不承诺复现上游 80%。reasoning 展示主要降低感知
等待，不应报告成模型推理、首个正文 token 或 provider TTFT 下降。

## 1. 目标和边界

本方案优化 Potato Agent 自己能够控制的延迟，不把公网模型、上游 provider 排队或模型推理时间算成
Potato 的优化收益。目标分为三类：

1. 缩短冷 draft 第一次请求发往 provider 之前的同步等待。
2. 尽早显示第一个有效反馈，尤其是 reasoning 模型已经产生但当前网页没有展示的 reasoning 流。
3. 降低长回复期间 SQLite、WebSocket、Markdown 和 DOM 的重复工作，避免流式事件积压和页面掉帧。

第一批变更只能落在 Potato-owned 边界：

- `interface/`
- `hermes-lite/tui_gateway/`
- `hermes-lite/potato_hermes_lite/`
- Lite runtime profile、provider compatibility、配置缓存、skills 扫描和纯估算辅助逻辑

第一批明确不做：

- 不整体替换 v0.19 的 `conversation_loop.py`、`tool_executor.py` 或 provider transport。
- 不改变主 conversation/model turn loop、工具执行顺序、delegation 协调、approval、interrupt 或 retry 语义。
- 不通过降低 reasoning effort、缩短上下文、删除工具或更换模型来制造“提速”。
- 不修改 `/srv`、systemd、Linux 用户、权限或生产配置；部署和灰度需要单独授权。

## 2. 指标口径

所有结果必须同时报告场景、p50/p95、样本数和 95% bootstrap 置信区间。不能只报告一次最快结果。

| 指标 | 同一时钟域内的起点 | 终点 | 用途 |
|---|---|---|---|
| Workspace usable latency | browser 收到 runtime start 响应 | composer 可交互 | 防止预连接拖慢页面可用时间 |
| Gateway readiness latency | browser 收到 runtime start 响应 | browser 收到 `gateway.ready` | 衡量预连接能否利用输入前空窗 |
| Profile-neutral prewarm | gateway prewarm worker started | worker reached terminal state | 衡量可隐藏的 import/probe 工作及 CPU |
| Turn submission receipt latency | browser 提交 | browser 收到 accepted turn response | 直接覆盖 WebSocket、gateway 和 session/RPC 启动成本 |
| Gateway pre-provider overhead | gateway 接受 prompt | provider transport 开始请求 | 衡量 Agent build/prompt 的 cold/warm 关键路径 |
| Provider TTFT | mock provider 收到请求 | mock provider 发出首个 chunk | 隔离 provider 时间，不归因给 Potato |
| First reasoning visible-ready | browser 提交 | 首个非空 reasoning 完成 DOM commit | 衡量 reasoning 的感知等待代理指标 |
| First content visible-ready | browser 提交 | 首个非空正文完成 DOM commit | 防止仅显示 reasoning 就宣称正文 TTFT 改善 |
| Interface delivery latency | Interface 收到 gateway event | journal commit 后 WebSocket send 完成 | 衡量 listener、durability 和 bridge 开销 |
| Browser render latency | browser 收到 delta | 对应 rAF 完成 DOM commit | 隔离前端调度和渲染开销 |
| Completion durability | Interface 收到 `message.complete` | journal、display transcript 和 live state 一致 | 保证提速不牺牲恢复能力 |

每个进程使用自己的时间点命名，禁止把不同 clock domain 的值直接相减：

```text
B0  browser received runtime-start response
B1  composer became interactive
B2  browser received gateway.ready
B3  browser submitted prompt
B4  browser received accepted turn response
B5  browser received first non-empty reasoning delta
B6  reasoning DOM commit completed in requestAnimationFrame
B7  browser received first non-empty content delta
B8  content DOM commit completed in requestAnimationFrame

I0  interface received gateway event
I1  authoritative journal commit completed
I2  websocket send completed
I3  message.complete projection flush completed

G0  gateway accepted prompt
G1  agent build started
G2  agent became ready
G3  provider transport dispatch started
G4  first provider chunk callback entered
G5  reasoning.delta/message.delta write completed

GW0 gateway.ready write completed
GW1 profile-neutral prewarm worker started
GW2 prewarm worker reached ready/failed terminal state

P0  mock provider received request
P1  mock provider emitted first chunk
```

服务端使用 `time.monotonic_ns()`，浏览器使用 `performance.now()`。跨进程只通过同一 `request_id/run_id` 关联，
只计算 B、I、G、P 各自内部的 span。B3 到 B6/B8 是浏览器同钟端到端指标，固定 mock provider 输出后可做
before/after 对比。rAF callback 表示 DOM commit，不等于像素已显示；需要近似 paint 时使用第二个 rAF，并在
报告中明确它是 proxy，不写成精确 paint 时间。

性能日志只允许包含 `run_id`、事件类型、字节数、delta 数量和耗时。禁止记录 prompt、reasoning 内容、
API key、用户目录或附件内容。生产 tracing 默认关闭或低比例采样。

## 3. 当前基线判断

### 3.1 冷启动

- `/api/tui/ws` 会在首次连接时创建每用户 gateway，但当前前端只在 `tuiBridgeRpc()` 内调用
  `ensureTuiBridge()`；`bootstrapSession()`、`startWorkspaceRuntime()` 和历史会话加载都不会主动连接。
- 因此冷 draft 的首次 WebSocket、gateway 启动和 `session.create` 都发生在用户点击发送后。Gateway 的 50ms
  deferred Agent build 只能与点击发送后的少量工作重叠，不能利用 workspace 加载和用户打字时间。
- Lite 首次导入 `run_agent + openai` 的本机隔离微基准约为 `0.874s`，但这只是组件上限：`run_agent.py`
  会在 import-time 读取 `HERMES_HOME`、加载 `.env` 并导入带模块级状态的 tools，不能在缺少 session profile
  context 时直接预导入。
- `get_environment_probe_line()` 仍在首次 system prompt 构建中同步执行多个 Python/pip 子进程，本机隔离
  微基准约为 `0.650s`。
- Potato proxy 的 `/v1/models` 在配置存在时已经返回 `context_length`，Web model switch 也会写入该值；只有
  model option 和 endpoint metadata 都缺少 context length 时，generic custom endpoint 才可能回退到 Ollama
  `/api/show`，额外产生约 `0-0.3s` 网络等待。

上述微基准是可回收的组件预算，不是生产端到端承诺。正式收益必须由第 4 节基准确认。

### 3.2 首次可见反馈

- Lite gateway 已无条件注册 reasoning callback 并产生 `reasoning.delta`；Bridge/run manager 也会先把该事件写入
  journal。`display.show_reasoning=false` 不会阻止事件产生。
- Web 前端不消费或绘制实时 `reasoning.delta`。Run manager 也没有把 reasoning delta 投影到当前 live/display
  assistant message，只在 `message.complete` 带回完整 reasoning 时更新最终消息，因此运行中刷新无法从 snapshot
  恢复已流出的 reasoning。
- 对 reasoning 时间较长的模型，用户会一直看到等待状态，即使模型已经持续输出 reasoning token。

这项优化不缩短模型推理时间，但可能把“几十秒无反馈”改成模型开始 reasoning 后立即可见。

### 3.3 流式吞吐

- Bridge `_broadcast_event()` 当前先顺序等待所有 event listener，再向 WebSocket subscriber 发送。
- `message.delta` listener 会写 event journal，并对每个 delta 读取、修改和保存整份 display transcript 与
  live state。
- 前端每个正文 delta 都调用 `renderMessages()`；该函数清空消息容器、重建全部消息，并对不断增长的正文
  重做 Markdown parse、sanitize 和 workspace linkify。
- 这会形成 growing-prefix 重算和全量 DOM 重建，长回复的 CPU/SQLite 工作量接近二次增长。

### 3.4 已有优化和无收益项

Potato 已有 tool definition cache、skills prompt LRU/disk snapshot、稳定 system prompt 和 50ms deferred Agent
build。下列 v0.19 项不应重复移植：

- Discord capability cache：Potato 工具清单不包含 Discord。
- 无 MCP 时的 import gate：Potato runtime profile 已关闭 MCP，gateway entry 也已经按 profile 跳过 discovery。
- classic CLI 按终端宽度 force-flush：Potato 主界面是 Web，不经过 classic CLI 输出路径。

## 4. Phase 0：基准和可观测性

### 4.1 实施内容

新增一个完全使用临时 `HOME/HERMES_HOME` 和本机 mock provider 的性能基准入口，建议位置：

```text
hermes-lite/scripts/benchmark_response_latency.py
```

基准至少覆盖：

1. true cold：新 Python/gateway 进程、进程级 cache 未热、预连接关闭。
2. prewarm 状态分层：脚本在 workspace ready 后固定等待 `0ms/1s/5s` 再提交，但按 B3 时 worker Event 的
   `not_started/in_flight/ready/failed` 实际状态归组，不能仅凭 dwell 把已完成样本算作 in-flight。
3. prewarm completed first turn：通过 benchmark trace 确认 GW2 为 ready 后提交，但仍是该 Agent 的第一次请求。
4. warm subsequent turn：同一 Agent 的第二轮和后续请求。
5. 107 个 `SKILL.md` 的 snapshot 命中和失效。
6. 纯文本长历史、1 MiB 图片和 8 MiB 图片历史。
7. 先流 5 秒 reasoning、再流正文的响应，分别记录首个非空 reasoning 和首个非空正文。
8. 1,000 个 16-byte delta 和 10,000+ 字符 Markdown；至少使用 1ms burst 和 100ms sparse 两种固定 cadence。
9. streaming 中的 interrupt、approval、浏览器断开重连和 gateway 退出恢复。
10. workspace 打开但不发送消息，记录 gateway 启动次数、prewarm CPU、空闲 RSS 和清理行为。

资源基线使用 legacy on-demand gateway ready 后 5 分钟、每 1s 一次的 RSS/CPU 样本；idle CPU band 定义为
该样本 p95。Unexpected gateway exit rate 定义为“非 logout/reconfigure/idle-cleanup 的 exit 数 / 100 次 gateway
start”，不能把预期回收算成崩溃。

预连接会把工作移到点击发送之前，因此必须同时报告 workspace usable latency、runtime-start 到
`gateway.ready`、B3-B2 readiness lead（以及 `B2 <= B3` 的比例）、B3-B4 turn receipt、点击提交到
visible-ready、G0 到 G3、额外 CPU/RSS；B3-B2 使用有符号值，负数表示提交时 gateway 尚未 ready。不能只
展示“点击后快了多少”。

mock provider 应记录 P0/P1，并按固定间隔发送 reasoning/content chunk。每种 cold/prewarm 状态和 warm/stream
场景至少运行 3 个 batch、每 batch 100 次，before/after 随机交错运行，并报告 95% bootstrap 置信区间。
测试机器、CPU governor、文件系统、浏览器、Python、Lite revision、skills 数量、输入大小和 delta cadence
必须随结果保存。
触发 `TURN_SUBMISSION_SOFT_TIMEOUT` 或 receipt recovery 的样本单独计为 degraded cohort，不能混入正常 B3-B4
分位数，也不能丢弃不报。

### 4.2 首轮验收门槛

- tracing eligible run 定义为“被采样且已创建 turn receipt 的 run”，包括正常完成和显式 error/interrupt；其中
  拥有该路径全部必需 B/I/G/P span 或明确 `not_applicable` 原因的比例至少 99%。各报表必须把 gateway
  pre-provider、provider TTFT、Interface delivery 和 browser render 分开，不得相减不同 clock domain。
- 同一场景三个 batch 各 100 次；以 pooled median 为分母，任一 batch median 与 pooled median 的绝对偏差
  不超过 10%。超过时先修基准，不开始优化。
- 所有 tracing 关闭时，warm 路径回退不超过 1%。
- Phase 0 完成后按固定硬件冻结每个场景的 baseline、相对改善目标和绝对上限。若 baseline 已低于预设绝对
  收益，不强求“下降 1s”，改用相对目标和置信区间，禁止选择性只报告 prewarm completed。

## 5. Phase 1：冷启动关键路径

### 5.1 Workspace ready 后预连接 Gateway，并做 profile-safe 预热

涉及文件：

- `interface/static/lite/app.js`
- `hermes-lite/tui_gateway/entry.py`
- `hermes-lite/tui_gateway/server.py`
- `hermes-lite/tools/env_probe.py`
- `hermes-lite/agent/agent_init.py`

设计：

1. `/api/runtime/start` 成功且 `resetWorkspaceState()` 完成后，前端非阻塞调用 `ensureTuiBridge()`，与
   `initializeWorkspaceData()` 并行。必须捕获预连接失败，不能让它使 workspace 启动失败；首次聊天 RPC 仍按
   现有路径同步重试。
2. 只在已认证且 runtime ready 的 workspace 发起预连接；logout、session expiry 和 workspace reset 继续关闭
   旧连接。Model switch 成功关闭旧 bridge 后必须再次触发预连接，否则切换后的第一轮仍是 cold path。为失败
   重试设置 backoff，禁止 bootstrap、model switch 与聊天提交形成双重连接或重连风暴。
3. Gateway 成功写出 `gateway.ready` 后启动一个 daemon prewarm worker，不阻塞 ready event 或 WebSocket 建立。
4. worker 只能导入经过 import-side-effect 审计的 profile-neutral allowlist（例如 `openai` SDK），并启动
   environment probe。`tools.env_probe` 是唯一明确审计的 tools 例外，且不得触发 registry discovery；禁止在
   这里导入 `run_agent`、`model_tools` 或其他 tool implementation modules：当前 `run_agent.py` 会在 import-time
   固化 `HERMES_HOME`、加载 `.env` 和 tool 模块状态。
5. 使用进程级 lock、Event/Future 和明确状态，保证并发 session 只启动一次；allowlist 中每个模块都要有
   “导入前后环境、registry 和 config cache 未改变”的测试。
6. `_make_agent()` 必须继续在 session 的 `set_hermes_home_override(profile_home)` 和 session context 已生效后
   导入 `run_agent` 并构造 Agent；不创建可跨用户复用的 AIAgent，不缓存用户配置对象。
7. 使用两个不同 `HERMES_HOME`、不同 `.env` 和 model config 的回归测试，证明预热不会让第二个 profile 读取
   第一个 profile 的状态。若未来要预热 `run_agent`，必须先消除或隔离这些 import-time side effects，并单独
   进入第 9 节审批。
8. 用户极快提交时只会等待尚未完成的 allowlisted import；`run_agent` 保持现有 profile-scoped 同步导入。
   任何预热失败都保留现有同步 fallback。

这样才能真正利用 workspace 打开到用户点击发送之间的空窗。预连接只启动 gateway，不提前调用
`session.create`，因此用户放弃 draft 时不会留下 Hermes 会话记录。代价是只浏览历史或文件的用户也会启动
一个空闲 gateway；Phase 0 必须量化该进程的 RSS 和回收行为。若 prewarm 后单 gateway idle RSS 超过 legacy
on-demand idle gateway 的 1.25 倍，或并发 workspace 基准超过 owner 冻结的主机内存预算，降级触发点为
composer 首次 focus 或第一次非空 input。现有 subscriber 离开后的 15s cleanup loop 应在 p95 30s 内回收
无 inflight gateway。

### 5.2 Environment probe 异步化

移植 v0.19 的单 worker/cache/Event 模式，但先保持 prompt 语义，再单独评估 fail-open：

- agent init 和 gateway prewarm 都可以幂等调用 `warm_environment_probe_async()`。
- 第一批默认 `prewarm_join`：若用户极快提交，prompt builder 等待同一个 worker 完成，生成内容与 legacy 同步
  probe 一致；正常情况下 worker 已在输入空窗完成。
- 可选 `prewarm_fail_open` 最多等待 50ms，超时则本 Agent 固定省略 Python toolchain 提示。worker 后续完成只
  供新 Agent 使用，禁止同一 Agent 的 system prompt 在后续轮次突然变化并破坏 prompt cache。
- 开启 fail-open 前必须通过 system-prompt golden、缺失/错配 Python 环境和工具选择质量用例；单独开关可立即
  回到 `prewarm_join` 或 `legacy`。
- worker 结束后供后续 session 使用，不能在 prompt 线程重复启动一组子进程；子进程 timeout、异常和临时
  文件问题不能无限阻塞 Agent turn。
- 保留 `_reset_cache_for_tests()` 的确定性测试接口，并覆盖 reset 与 in-flight worker 的 generation race。

### 5.3 消除 Potato proxy 的 Ollama 误探测

Lite 的 `custom` provider 同时服务 Ollama、vLLM、llama.cpp 和 Potato proxy，不能把整个 custom provider
全局标记成“非 Ollama”。优先采用配置驱动方案：

1. 先审计所有 Interface-managed model options：`context_length` 必须来自 provider 官方元数据、Potato proxy
   的受控 catalog 或 owner 审核配置，不能仅验证“正整数”。配置迁移时拒绝 model option、proxy metadata 和
   compression context 之间的不一致。
2. 保持并回归现有链路：Potato proxy `/v1/models` 返回已配置的 context length，Web model switch 通过
   `interface/model_options.py` 写入 model 和 compression config。另行覆盖 gateway 内 `/model` 路径，不能把
   Web 配置重写与 session-scoped model override 混为一条路径。
3. 对每个受管模型验证 context pressure/compression 阈值与权威值一致；有显式 metadata 时证明不会访问
   `/api/show`，缺失时仍保留 generic custom endpoint 的 Ollama auto-detection。
4. 若必须兼容没有权威 context length 的旧配置，再增加三态 capability：`ollama_compatible=true|false|auto`；
   generic custom 默认 `auto`，仅 Potato 生成的 proxy 配置写 false。禁止只凭任意 localhost URL 猜测。

### 5.4 小型冷路径优化

在 Phase 0 证明占比后依次移植：

- `hermes_time.py` 改用现有 `read_raw_config()` mtime/size cache，不在首次 prompt 直接重新解析 YAML。
- `utils.py` 增加安全的 `CSafeLoader` helper，并替换 config/plugin manifest 的热路径 `safe_load`。Lite 只有一个
  bundled model-provider manifest，因此不能照搬完整上游“约 0.9s”数字。
- `agent/prompt_builder.py::_build_skills_manifest()` 从两次递归改成一次带相同 pruning 规则的 `os.walk`。
- `tools/registry.py` 在 AST parse 前用 `"registry"`/`"register"` 文本做无漏报预筛。

### 5.5 Phase 1 验收

- `prewarm completed first turn` 的 B3-B4 p50 相对 true-cold baseline 至少下降 30%，直接验收预连接路径。
  对固定 TTFT、首 chunk 即正文的 mock provider，B3-B8 也至少相对改善 30%；若 Phase 0 的可回收组件预算
  不少于 1s，再要求 B3-B8 绝对下降至少 1s。G0-G3 作为 gateway 内部归因指标单独报告。
  `prewarm in-flight` 和 `0ms dwell` 不得并入 completed 样本。
- 1s dwell 场景中 `B2 <= B3` 的 ready-before-submit 比例至少 90%；同时报告 B3-B2 的 p50/p95 lead。
- Warm G0-G3、B3-B4 和 B3-B8：p50/p95 均不得回退超过 5%。
- Workspace usable latency p95 不得回退超过 5%；预连接失败后首次提交仍可按现有路径建立连接。
- prewarm 不延迟 `gateway.ready`，也不新增非 daemon 生命周期问题。
- 同一用户 workspace bootstrap/model switch 只创建一个 active gateway；logout/reset 后没有遗留 WebSocket 或
  重连任务。20 个并发 workspace 最多产生 20 个 gateway；GW2 后 30s 开始的连续 30 个 1s CPU 样本不得超过
  `max(legacy idle p95 + 2 percentage points, 5%)`。
- prewarm 后单 gateway idle RSS 不超过 legacy on-demand idle gateway 的 1.25 倍；无 inflight subscriber 的
  gateway p95 30s 内回收。总内存还必须低于 canary 前由 owner 记录的主机预算。
- `prewarm_join` 的 prompt 与 legacy 逐字一致；`prewarm_fail_open` 的同步等待不超过 50ms，且同一 Agent 后续
  prompt 不因 worker 完成而变化。
- 两个不同 `HERMES_HOME` 的 config、`.env`、skills 和 registry 状态不交叉污染。
- Interface-managed proxy 的固定基准中 `/api/show` 请求数为 0。
- 107 skills fixture：manifest 冷扫描相对改善至少 3 倍且低于 20ms，warm validation 低于 5ms；新增、删除、
  编辑和 symlink 场景与当前结果一致。绝对值只对 Phase 0 冻结的硬件和文件系统有效。

## 6. Phase 2：首个可见反馈和前端调度

### 6.1 贯通 reasoning 流

涉及文件：

- `interface/session_run_manager.py`
- `interface/display_store.py` 和 live-state snapshot/recovery 路径
- `interface/static/lite/app.js`
- `interface/static/lite/styles.css`
- mock-provider E2E 和新的 Interface regression tests

实现要求：

1. 不重复增加 gateway callback：`reasoning.delta` 已产生、转发并先写 journal。
   `POTATO_REASONING_PROJECTION=legacy|live` 控制 run manager 是否建立运行中 projection；
   `POTATO_REASONING_STREAM_UI` 只控制浏览器是否绘制。两者都关闭时保持当前
   transport/journal/final-message 行为，不删除审计事件。
2. Phase 2A 先落地第 7.1 节的 journal identity 最小前置：live mode 的 append 返回 committed `journal.id`，
   新 gateway-origin row 写 idempotency key，listener retry 不会让同一 reasoning delta 插入两次；legacy mode
   继续写 null key，关闭 flag 时保持当前行为。
3. Live mode 下，run manager 在 journal commit 后按 `(run_id, gateway_seq)` 更新每 run 的内存 reasoning
   projection，并在 active live snapshot 中返回它；不能为每个 reasoning token 重写整份 display transcript。
4. Interface 重启或内存 projection 丢失时，从 authoritative journal 按 `journal.id` 重建 active run，以及
   已 terminal 但尚未 canonicalize 的 run；gateway-origin reasoning 按 `(run_id, gateway_seq)` 幂等去重。
5. `message.complete`、interrupt、error、run.failed 和 gateway exit 等所有 terminal path，都必须在关闭 run
   context/标记最终状态前，把内存 projection 或 journal 中截至 terminal `journal.id` 的 reasoning 物化到最终
   assistant message，并记录 canonicalized marker。迟到的 complete 只能幂等补全，不能重复或覆盖 interrupt
   边界后的内容。
6. 前端增加 `reasoning.delta` handler，把 reasoning 累加到当前 streaming assistant message，并在 assistant
   message 内使用稳定、可折叠区域；正文开始后默认折叠但不销毁内容。
7. inactive session 只更新对应 session buffer，不触发当前页面 render。`message.complete`、interrupt 和
   reconnect snapshot 按 `(run_id, gateway_seq)`/message id 去重合并，禁止简单字符串拼接造成重复。
8. metrics 和普通日志只记录 reasoning 字节数，禁止记录内容。Journal、导出和 retention 仍含 reasoning 的
   现状必须在启用 UI 前完成产品/安全复核；UI flag 不是数据留存开关。

Raw reasoning 是否生产默认展示属于产品和安全决策。代码先受 feature flag 控制；默认开启前必须确认 provider
返回的是允许展示的 reasoning/summarized reasoning，并取得 owner 批准。

### 6.2 每帧合并 delta

前端按 session 分别缓存正文和 reasoning delta，只安排一个 `requestAnimationFrame`：

- 每帧最多更新一次状态和一次当前消息 DOM。
- `message.complete`、error、interrupt、approval 和 session switch 前同步 flush buffer。
- 普通模式不再为每个 token 更新调试状态文本。
- 用户离开底部时不得强制滚动；保持现有 auto-scroll 和 scroll restore 契约。

### 6.3 避免每 token 全量重建 transcript

为当前 streaming message 建立按 message id 定位的稳定 DOM node，只 patch：

- reasoning body
- assistant content body
- 当前 execution progress/tool row
- streaming/complete 状态

历史消息和 sidebar 不随 token 重建。最终 complete 时执行一次 canonical `renderMessages()`，确保实时轻量路径与
持久化快照最终一致。

### 6.4 Phase 2 验收

- reasoning 已开启时，B5 到 B6 的 browser receive-to-DOM-commit p50 < 16ms、p95 < 50ms。
- 正文 B7 到 B8 的 browser receive-to-DOM-commit p95 < 50ms。B3 到 B6/B8 也要报告，但不能把固定 mock
  provider 之外的模型时间归因给前端。
- 同一 animation frame 最多一次 transcript DOM 更新。
- 1,000 delta 场景不再调用 1,000 次全量 `renderMessages()`。
- 10,000+ 字符响应期间不得出现由流式渲染造成的 >100ms main-thread long task。
- complete、interrupt、error、refresh、Interface restart 和 reconnect 后内容逐字一致，无重复 reasoning 或正文。
- Desktop 和 mobile viewport 截图无重叠、溢出或 reasoning 区域遮挡正文。

## 7. Phase 3：持久化和 WebSocket 流水线

涉及文件：

- `interface/tui_gateway_bridge.py`
- `interface/session_run_manager.py`
- `interface/display_store.py`
- session live-state、interrupt、approval、tip reconcile 和 bridge registry tests

### 7.1 保留 durability 的批量投影

第一版不能直接改成“先发 WebSocket，后写数据库”。采用以下顺序：

1. event listener 先把每个 gateway event 和 gateway seq 单独提交到 authoritative event journal，保持当前
   durability barrier；第一版不对 journal 做 group commit。
2. `journal.id` 是 gateway-origin 和 Interface synthetic event 的唯一全序。为新 gateway-origin row 增加 nullable
   `gateway_event_key=(user_id, session_id, run_id, gateway_seq)` 和 partial unique index；listener retry 使用
   insert-or-get 保留第一次插入并忽略重复。Synthetic 和历史 row 的 key 为 null，可以复用 live state's last
   gateway seq，只以独立 `journal.id` 排序；迁移不删除或强制重写历史 row。Live/batched mode 写 key 并在内存
   幂等 replay，legacy mode 继续写 null key，关闭 flags 不改变旧插入语义。
3. 每 session 在内存中按 `journal.id` 更新 display/live projection，并对 gateway-origin seq 拒绝回退。
4. display transcript 和 live state 按 16-50ms 或 4KiB 阈值批量写入，而不是每 token 读取和保存整份 JSON。
5. journal commit 完成且 projection 已有序入队后 listener 即可返回，bridge 再按原顺序发送 WebSocket；不要求
   普通 delta 的 projection 已落盘。
6. `message.complete`、error、interrupt、approval request/response、session switch、bridge exit 和 shutdown
   必须等待 projection 强制 flush。

若现有恢复路径不能仅凭 journal 重建尚未 flush 的 projection，必须先补齐恢复和幂等 replay 测试，不能用
“低概率崩溃”接受丢失。任何把 WebSocket 提前到 journal durability 之前的设计都转入第 9 节审批项。

### 7.2 Projection 批处理

- 第一条和后续 reasoning/正文 delta 在 journal commit 后立即对 WebSocket 可见；display/live projection 进入
  16-50ms 或 4KiB 的短窗口，窗口只影响 snapshot 落盘，不影响 event delivery。
- 不复用语义不同的 `STREAM_FLUSH_INTERVAL_SECONDS=0.5`，新增名字明确的 projection 配置。
- 每用户、每 session 独立 buffer、seq cursor 和 lock，禁止跨用户 cache key 或共享 mutable message list。
- 设置队列和字节上限；超限时同步 flush，而不是丢事件或无限占用内存。
- 当前 bridge 在 awaited listener 全程持有 per-listener delivery lock，下一事件不能进入同一 journal batch。因此
  journal group commit 不属于这一阶段。若 profile 证明 journal commit 是瓶颈，需另行设计“有序入队、共享
  commit future、commit 后才放行 WebSocket”的 sequencer，并按第 9 节审批，不能伪装成现有 listener 内 batching。

### 7.3 Slow subscriber 隔离

- Queued subscriber mode 为每个 WebSocket subscriber 建立独立、有界 FIFO 和单一 sender task；只有 I1
  journal commit 完成的事件才能入队，队列内保持原始 seq 顺序。
- 队列同时限制 event count 和 serialized bytes，初始门槛为 1,000 events/4MiB；超过任一门槛或单次
  `send_text` 超过 2s，只断开该 subscriber，不阻塞 journal、projection 或其他 subscriber。
- 浏览器重连后通过 live snapshot + journal replay 恢复，不能假设断开的 subscriber 收到了 queue 中的 final
  event。Legacy mode 保持现有直接 send，便于独立回滚。
- 指标中的 WS queue depth 明确定义为每 subscriber 尚未完成 send 的 FIFO event/byte 数，不把无关 asyncio
  task 数量混入。

### 7.4 增量 Markdown

先完成 rAF 合并和局部 DOM patch，再依据 profile 决定是否实现 block-incremental Markdown：

- 已闭合的段落、代码 fence 和列表 block parse/sanitize/linkify 一次后冻结。
- 只重新解析仍在增长的 tail block。
- fence、表格、数学块未闭合时保留 tail，不提前冻结错误结构。
- final 事件始终完整 parse/sanitize 一次作为权威结果。

如果 rAF + 局部 DOM 已满足 Phase 2 指标，不为了追随上游而引入增量 parser 复杂度。

### 7.5 Phase 3 验收

- 1ms cadence、16-byte 的 1,000 delta 场景按 flush 配置分档验收：16ms 至少减少 85%，32ms 至少 90%，
  50ms 至少 95%；4KiB threshold 在该 fixture 中不得提前触发。100ms sparse 场景不强求合并率，但延迟和
  正确性不得回退。发布候选默认使用 32ms，只有实测满足前端和 recovery SLO 才可调整。
- `journal.id` 严格递增且唯一；gateway-origin `(run_id, gateway_seq)` retry 不产生重复，run 内 gateway seq 不
  回退；synthetic event 允许复用关联 seq，但 replay 全部按 `journal.id`，不丢、不乱序。
- crash/restart、浏览器刷新和 bridge 重启后最终 transcript 与未崩溃执行逐字一致。
- approval、interrupt 和 final event 不得被 batching 延迟超过 50ms。
- 无人工 backpressure 的健康本地 subscriber，I0 到 I2 p95 < 100ms，并分别报告 I0-I1 journal 和 I1-I2
  queue/send。250ms/send 的慢 subscriber 场景不适用 I0-I2 SLO；该场景要求 I0-I1 p95 相对 healthy 回退不
  超过 5%，queue 不超过 1,000 events/4MiB，达到 2s timeout 后只断开慢 subscriber。
- 慢 WebSocket subscriber 不阻塞 journal/projection worker 或其他 subscriber；断开后能通过 snapshot/journal
  恢复最终内容。
- SQLite busy/exception 时 fail closed 到现有同步路径，并输出不含用户内容的结构化错误。

## 8. Phase 4：长会话和每轮 CPU 优化

### 8.1 图片和请求大小估算

Lite 当前每个 API iteration 会：

```python
total_chars = sum(len(str(msg)) for msg in api_messages)
approx_tokens = estimate_messages_tokens_rough(api_messages)
approx_request_tokens = estimate_request_tokens_rough(api_messages, tools=...)
```

改为一次 image-stripped message estimate，并单独加入 tool schema estimate。要求：

- `approx_request_tokens` 会驱动 `_ollama_context_limit_error` 的错误/退出分支。新的 tool schema estimate 必须与
  当前 `estimate_request_tokens_rough(messages, tools=...)` 返回整数逐项相等，包括 `(len(str(tools))+3)//4`
  的舍入；不能用“更准确但不同”的算法替换。
- 覆盖所有 Potato tool schema snapshot，以及刚低于、等于和刚高于 context-limit 门槛的 fixture，证明分支、
  iteration budget refund、状态事件和最终消息完全一致。
- 先枚举 `total_chars/request_char_count` 的所有消费者。只有确认 Potato profile 中它仅用于日志或无决策的
  telemetry hook，才可改成 token-derived rough proxy；事件字段和类型保持兼容，并记录图片场景数值会变化。
  若任何启用的 hook 把精确字符数用于控制流或配额，移入第 9 节。
- 覆盖纯文本、小图、8 MiB 图、多图、tool result 和长历史。
- 本机 8 MiB fixture 的估算目标低于 1ms；当前隔离微基准约 40.8ms。

这项变更虽落在 `conversation_loop.py` 附近，但只能替换纯估算表达式，禁止顺带改动 turn loop 分支、压缩时机、
retry 或 provider payload。若无法证明 estimate 和上述控制流逐项等价，整项移入第 9 节，不得在首批修改 core
loop。

### 8.2 Tool schema estimate cache

只有 Phase 0 证明该项可见时才增加 cache。优先把估算值附着到 Agent 的稳定 tool snapshot，而不是创建容易
发生 `id()` 复用或跨 session 污染的无界全局 cache。toolset 变化时必须显式失效。

### 8.3 Skill discovery signature cache

为 `skills_list/skill_view` 的 `_find_all_skills()` 增加短 TTL signature cache：

- key 包含实际 `HERMES_HOME`、skills roots、disabled set、platform 和过滤模式。
- 返回 copy，调用者不能污染共享 cache。
- category 新增/删除、disabled config 变化立即失效；原地编辑用不超过 30 秒 TTL 限制陈旧时间。

### 8.4 Phase 4 验收

- 在 Phase 0 冻结硬件上，8 MiB 图片 estimate p95 < 1ms 且相对改善至少 20 倍。
- tool schema 热估算 p95 < 0.1ms，toolset 改变后结果立即更新。
- 300 skills fixture 的 warm `_find_all_skills()` < 1ms 且相对改善至少 10 倍；107 skills 真实 fixture < 1ms。
- compression、context-limit error、iteration budget 和 status event 结果保持一致；request hook 的调用次数、
  字段和类型不变，允许已明确记录的 `request_char_count` 估算值变化。

## 9. Owner Approval Required

以下项目可能有较大收益，但不能混入前四阶段：

1. v0.19 mixed tool batch segmentation：把安全调用分段并发会改变工具调度和完成时序。
2. 任何修改 `_execute_tool_calls*`、delegation 并发、工具 barrier 或结果注入顺序的变更。
3. `api_content` sidecar、enabled request hook contract、跨轮 provider KV/prompt cache key 或历史持久化语义调整。
4. provider client 复用、连接池、retry、deadline、cancellation 或 stale-stream 控制流调整。
5. 在 session profile context 建立前预导入 `run_agent`，或重构其 import-time `.env`/tool module 状态。
6. journal group commit、改造 listener delivery sequencer、在 authoritative journal commit 前向浏览器发送事件，
   或降低 finished response durability。
7. 生产默认展示 raw reasoning。
8. systemd、`/srv` deployment、生产配置、用户、权限或服务重启。

这些项目必须单独提交设计、故障模型、回滚办法和 owner 批准，不能以“性能优化”名义绕过 orchestration
guardrail。

## 10. 测试矩阵

每个阶段至少运行对应定向测试；进入发布候选前运行完整 Lite 套件。

```bash
python -m pytest \
  interface/test_model_options.py \
  interface/test_model_proxy.py \
  interface/test_tui_gateway_bridge_registry.py \
  interface/test_session_live_state_regressions.py \
  interface/test_session_display_regressions.py \
  interface/test_session_run_manager_interrupt.py \
  interface/test_session_run_manager_approval.py \
  interface/test_session_run_manager_tip_reconcile.py

python -m pytest -c /dev/null hermes-lite/tests hermes-lite/tests_packaging

python -m pytest -c /dev/null \
  hermes-lite/tests_e2e/test_mock_provider_e2e.py

node --check interface/static/lite/app.js

python3 hermes-lite/scripts/verify_lite.py \
  --python /opt/potato-hermes-lite-dev/bin/python
```

前端性能不能只靠静态字符串断言。应增加真实浏览器 harness，验证：

- reasoning 先于正文时可见。
- rAF coalescing 和局部 DOM patch 次数。
- 长 Markdown、代码 fence 和表格中间态。
- inactive tab/session 不做无关 render。
- scroll、session switch、interrupt、approval 和 reconnect。
- Desktop 和 mobile viewport 的截图对比；按 `AGENTS.md` 把截图附到前端变更评审。

还必须增加 env probe legacy/prewarm/fail-open、双 `HERMES_HOME` import 隔离、model switch 后重新预连接、
context length 权威值/分支等价和 journal replay reasoning 的定向回归。所有 profile 隔离测试使用两个互不相同的
临时 HOME、`.env`、config 和 skills marker。

测试和 benchmark 不使用真实 API key、真实用户 HOME、生产数据库或公网 provider。

## 11. 发布、灰度和回滚

每个 Phase 独立提交和发布，不做一个包含所有层的大补丁。

建议 feature flags：

```text
POTATO_PERF_TRACE=0|1
POTATO_GATEWAY_PRECONNECT=0|1
POTATO_RUNTIME_PREWARM=off|neutral
POTATO_ENV_PROBE_MODE=legacy|prewarm_join|prewarm_fail_open
POTATO_REASONING_PROJECTION=legacy|live
POTATO_REASONING_STREAM_UI=0|1
POTATO_STREAM_RENDER_MODE=legacy|raf|incremental
POTATO_PROJECTION_MODE=legacy|batched
POTATO_PROJECTION_FLUSH_MS=16..50
POTATO_WS_SUBSCRIBER_MODE=legacy|queued
```

要求：

- flag 关闭或选择 `legacy` 必须回到当前行为，便于单项回滚和 A/B 基准。`flush_ms=0` 不作为 legacy 的
  同义词，避免两套实现边界含糊。
- flags 在各自 lifecycle（workspace/gateway/Agent/run）开始时 snapshot，运行中不得切换持久化或 prompt 语义。
  回滚 batched projection 前先强制 flush 所有 buffer；journal/event 格式必须前后兼容，不允许 rollback 依赖
  数据库降级。
- 浏览器需要的 preconnect/UI flags 通过现有 authenticated runtime config 暴露，禁止把其他环境变量或秘密
  一并下发。
- `POTATO_REASONING_STREAM_UI=1` 必须依赖 `POTATO_REASONING_PROJECTION=live`；无效组合在启动时拒绝，不能
  静默降级成缺少 reconnect recovery 的半实现。
- 先用 mock provider 和临时 HOME 验证，再在获得部署授权后做单用户 canary。
- canary 的定量停止线：workspace usable 或 warm G0-G3 p95 回退超过 5%；健康 subscriber 的 I0-I2 p95
  超过 100ms；queued mode 的 WS queue depth p95 超过 100 events 或 max 超过 1,000 events；SQLite
  busy/error 超过 0.1% journal events 或高于
  baseline；单 gateway idle RSS 超过 legacy 的 1.25 倍；无 inflight gateway p95 30s 未回收；每 100 次 gateway
  start 的 unexpected exit 数高于 baseline 0.1。主机总内存预算在 canary 前由 owner 按最大并发数冻结。
- 任一 durability、乱序、重复、跨用户污染、prompt profile 污染或 approval/interrupt 回归都按零容忍立即关闭
  对应 flag，不等待达到统计阈值。
- 性能指标达标但 correctness suite 不通过，仍视为失败。

## 12. 实施顺序和完成定义

按以下顺序推进：

- [ ] Phase 0：建立可重复基准和时间点，不先凭感觉改代码。
- [ ] Phase 1A：workspace ready 后预连接 gateway，只预热 profile-neutral imports，并异步启动 env probe。
- [ ] Phase 1B：审计 Potato proxy model option 的权威 context length，消除受管模型的 `/api/show` fallback。
- [ ] Phase 1C：timezone/config、skills manifest、AST prefilter 等低风险冷路径优化。
- [ ] Phase 2A：journal event identity 前置完成后，贯通 reasoning live projection/recovery/UI feature flag。
- [ ] Phase 2B：rAF 合并和当前 streaming message 局部 DOM patch。
- [ ] Phase 3A：保持 journal 每事件 durability，批量写 display/live projection，并隔离慢 subscriber。
- [ ] Phase 3B：只在指标需要时实现 block-incremental Markdown。
- [ ] Phase 4：图片估算、tool schema estimate 和 skill discovery cache。
- [ ] 单独评审第 9 节核心调度候选，不与以上项目混合。

整个方案完成的定义：

1. true-cold、prewarm in-flight、prewarm completed 和 warm 全部独立报告；completed first turn 的 B3-B4
   p50 相对改善至少 30%。固定 mock provider 下 B3-B8 p50 也至少改善 30%，且在 Phase 0 组件预算允许时
   绝对下降至少 1s；G0-G3 用于归因。
2. Workspace usable 和 warm 路径 p50/p95 回退不超过 5%，预连接 CPU/RSS/回收满足 Phase 1 门禁。
3. B5-B6 与 B7-B8 browser receive-to-DOM-commit p95 < 50ms；健康 subscriber 下 I0-I2 p95 < 100ms。
   不存在跨 clock domain 相减的验收项。
4. 32ms release 配置下，1ms cadence、16-byte 的 1,000 delta 场景 projection 写次数下降至少 90%，前端
   每帧最多一次 DOM 更新；其他 flush 值按 Phase 3 分档门槛验收。
5. 首个 reasoning 和首个正文 visible-ready 分开报告，不把 reasoning 展示表述为正文或 provider TTFT 提升。
6. interrupt、approval、final delivery、refresh/restart recovery 和 transcript 一致性全部通过。
7. 没有扩大 Lite capability surface，没有改变核心 Agent orchestration 语义，也没有 profile/HOME 污染。
