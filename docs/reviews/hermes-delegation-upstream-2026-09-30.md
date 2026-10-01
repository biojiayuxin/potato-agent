# Hermes 子智能体机制审查与 Lite 移植评估

审查日期：2026-09-30（Asia/Shanghai）。

> 状态更新：审查后，项目所有者明确回复“OK，这个移植是值得做的，可以开始移植。”。下文审查部分记录移植前行为；已授权的实现与验证见文末。

## 结论

最新上游已经实现后台委派、按活动判断超时、运行中追加指令、部分中断结果回传和完成事件持久化。这些机制能解决 Lite 的大部分问题，值得定向回移，但不能直接替换一个工具文件，也不能据此承诺“真正超时后一定取回完整工作”。上游真正超时的返回分支仍然是 `summary: null`；强制判定后台任务卡死时也可能没有任务结果。

建议实现一个 Lite 自有的委派生命周期与结果存储层，参考上游的后台分发和投递协议，并额外补齐超时快照及结果读取。保留现有模型/工具执行主循环和 provider 行为。后台委派本身仍会改变“委派协调”语义，因此正式实现需要项目所有者明确授权这一范围。

## 审查版本与方法

- 上游：<https://github.com/NousResearch/hermes-agent>。
- GitHub 查询时的最新 HEAD：[`f42f579cf8bac4918ac9599bece71618afadd846`](https://github.com/NousResearch/hermes-agent/commit/f42f579cf8bac4918ac9599bece71618afadd846)，提交时间 2026-09-30 12:59:14（Asia/Shanghai）。
- 查询时最新正式 release 为 [`v2026.9.24`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.24)。本报告的行为结论针对上述 HEAD，不代表已验证该 release 具备相同行为。
- Lite 基于当前工作区，仓库 HEAD 为 `490166d5bb240c217025cfbd50b0869d56c19742`。已有用户改动保留。
- 完整浅克隆传输停滞，已停止；改用 GitHub 提交树和原始文件/blob API 获取相关源码。审查快照位于 `/tmp/potato-hermes-review-snapshot-f42f579/`，36 个下载文件逐一通过固定提交树中的 Git blob SHA 校验，清单为该目录下的 `review-manifest.json`。
- 阅读了运行代码及上游对应回归测试；使用隔离临时 HOME/HERMES_HOME 和模拟子智能体验证当前 Lite 的超时、阻塞与结果格式化行为。没有使用真实模型 API，没有访问生产聊天数据，没有变更部署。
- 没有执行上游完整测试套件，也没有完成移植或兼容性验证；以下是源码审查及移植设计结论。

## 对三个问题的回答

| 问题 | 当前 Lite | 最新上游 HEAD | 判断 |
|---|---|---|---|
| 固定 10 分钟超时 | 默认 600 秒；已有配置可调，但按总运行时间计时 | 默认关闭委派时长上限；正值配置改为无进展窗口，有进展就续期 | 上游解决了“正常长任务也被时间上限杀掉”的主要原因 |
| 超时后内容无法交回主智能体 | 超时返回空摘要，立即中断、移出跟踪并清理 | 正常中断可提取最后一段助手文字；结果/日志保存更完善；真正超时仍可返回空摘要 | 部分解决；需要额外做超时快照与可读取的结果记录 |
| 主智能体必须一直等待 | 单任务同步等待；批量任务虽并发，主智能体仍等整个工具调用结束 | 有接收后续结果能力的顶层会话默认后台运行，完成后开启新轮投递 | 可解决，但必须同时接入 Lite 的会话调度与结果消费者 |

“子任务并行”与“主智能体可以继续推理”是两件事。当前 Lite 能并行执行多个子任务，甚至同轮其他工具也可能并发执行，但下一轮模型调用仍要等工具批次结束。

## 当前 Lite 的可复现根因

1. `hermes-lite/tools/delegate_tool.py:406` 的 `_get_child_timeout()` 默认 600 秒，配置和环境变量都有最低 30 秒限制。`_run_single_child()` 在约第 1572 行调用 `future.result(timeout=child_timeout)`，这是总运行时长上限，进展不会续期。
2. 约第 1653 行的超时返回明确写入 `summary: None`。约第 1902 行开始的 `finally` 随即注销任务、移出父智能体的 `_active_children` 并调用 `child.close()`，不会再把迟到结果送回父智能体。
3. 约第 2172 行的单任务分支直接调用 `_run_single_child()`；批任务分支也在本次工具调用内收齐结果。`hermes-lite/run_agent.py:4994` 的分发入口没有后台委派参数或后台结果契约。
4. Lite 已有 `AIAgent.steer()`（`run_agent.py:2364`）及主循环中的 steer 消费点，不必为了追加指令整体替换模型循环。
5. `hermes-lite/tui_gateway/server.py:4374` 附近已有后台进程通知与空闲时启动新轮的入口，但它服务的是进程事件。`hermes-lite/tools/process_registry.py:1857` 的格式器没有 `async_delegation` 分支：直接塞入上游事件不会把摘要传给模型。现有通知去重也需改为区分 `delegation_id`，会话归属检查需覆盖上下文压缩、重新连接及恢复。

隔离模拟验证结果：

```text
未设置 child_timeout_seconds -> 600.0 秒
child_timeout_seconds = 1800 -> 1800.0 秒
child_timeout_seconds = 0 -> 30.0 秒
超时 -> status=timeout, summary=null
超时清理 -> 子线程尚未结束时 child.close() 已被调用
子任务未完成 -> 委派调用没有返回
正常完成 -> 已有摘要可以返回
上游形状的 async_delegation 事件 -> Lite 现有通知格式器不保留摘要
```

模拟把等待期限缩短至亚秒级来触发相同分支，没有真的等待 10 分钟。它复现的是委派协调与格式化契约，不是生产 provider 的网络行为。第一组探针出现了未裁剪 browser 模块导入告警，相关断言均通过；没有据此推断浏览器功能状态。

**配置兼容注意：不能在当前 Lite 中照抄新版的 `child_timeout_seconds: 0`。** 当前 Lite 会把它转换成 30 秒。短期缓解可以把已有配置调到例如 1800 秒，但仍然同步阻塞、仍可能丢失超时摘要；本次未修改运行配置。Lite 默认子任务迭代预算仍是 50，调长时间不会自动调高迭代预算。

## 上游已实现的机制及实际边界

### 1. 活动超时，而非总时长超时

[`tools/delegate_tool_config.py:148`](https://github.com/NousResearch/hermes-agent/blob/f42f579cf8bac4918ac9599bece71618afadd846/tools/delegate_tool_config.py#L148) 将非正数解析为禁用超时，默认值也是禁用。正值是无进展窗口，不再是子任务允许存在的总时间。

[`tools/delegate_tool_child_run.py:789`](https://github.com/NousResearch/hermes-agent/blob/f42f579cf8bac4918ac9599bece71618afadd846/tools/delegate_tool_child_run.py#L789) 用 API 调用数、当前工具、活动时间戳判断进展；有变化就续期，无进展窗口约 80% 时通过 steer 提醒尽快返回。即使不配这个窗口，心跳仍检测长期停滞。源码中的停滞阈值为无工具时约 450 秒、工具内约 1200 秒，另有 API/工具自己的限制，所以“关闭总时长上限”不等于无限等待任何故障。

上游回归文件 `tests/tools/test_delegate_liveness_timeout.py` 同时覆盖“活跃任务超过短窗口仍完成”和“冻结任务被超时处理”。本次阅读了测试，未运行上游测试环境。

### 2. 顶层后台运行，下一轮接收结果

[`run_agent.py:1362`](https://github.com/NousResearch/hermes-agent/blob/f42f579cf8bac4918ac9599bece71618afadd846/run_agent.py#L1362) 将模型发起的顶层委派送入后台；嵌套 orchestrator 为了汇总自己的工作者，仍同步等待。

[`tools/delegate_tool_dispatch.py:416`](https://github.com/NousResearch/hermes-agent/blob/f42f579cf8bac4918ac9599bece71618afadd846/tools/delegate_tool_dispatch.py#L416) 返回包含 `delegation_id`、`subagent_ids`、日志路径等信息的 dispatch handle，把子任务从父轮中断列表转交给后台任务管理。父智能体可以继续处理独立工作，然后结束当前轮。

完成结果通过 [`tools/async_delegation.py`](https://github.com/NousResearch/hermes-agent/blob/f42f579cf8bac4918ac9599bece71618afadd846/tools/async_delegation.py) 进入队列，再由 [`tui_gateway/session_notifications.py:470`](https://github.com/NousResearch/hermes-agent/blob/f42f579cf8bac4918ac9599bece71618afadd846/tui_gateway/session_notifications.py#L470) 校验归属、领取事件并启动后续轮。**结果只在轮次之间投递，不会实时插进一个正在执行的模型轮。**

需要一起保留的边界：

- 没有后续结果消费者的有限会话会回退同步；后台容量满时也会回退同步。因此原样移植不是“任何情况下绝不等待”。
- 普通后续消息不应取消已脱离当前轮的子任务；明确的 Stop、关闭/重置所属会话则要停止任务树。
- 默认同一批次全部完成后一起返回。`delegation.independent_completions: true` 才启用每项/每组独立返回；分组改变的是投递，不是执行依赖关系。
- `action=list/steer/stop` 管理运行中的任务。steer 在迭代边界被消费，不会强行抢占正在执行的工具，也不等于恢复一个已终止子智能体。

### 3. 保存已完成结果，不等于恢复正在运行的任务

[`tools/async_delegation.py:206`](https://github.com/NousResearch/hermes-agent/blob/f42f579cf8bac4918ac9599bece71618afadd846/tools/async_delegation.py#L206) 保存完成事件，`record_unit_child()` 还会逐项保存批次中已经完成的结果。投递使用数据库 claim、确认、失败释放和重放机制。正常持久化成功的前提下，完成后、投递前进程重启不必丢失成果。

但它不是执行检查点：进程死亡时还在运行的子任务被标记为 `unknown`，不会自动重建 Python 执行栈继续任务。恢复事件可以带日志尾部和工作区 Git 状态，供父智能体判断下一步；没有承诺自动续跑或外部副作用恰好执行一次。磁盘写入失败时，上游也会退回只投递内存事件。

### 4. 正常中断有部分产出，真正超时仍有空结果路径

[`tools/delegate_tool_child_run.py:552`](https://github.com/NousResearch/hermes-agent/blob/f42f579cf8bac4918ac9599bece71618afadd846/tools/delegate_tool_child_run.py#L552) 在 child 正常返回 `interrupted` 时，从 messages 中选出最后一段非占位助手文字作为 summary，并保留工具调用轨迹。这比只返回 “Operation interrupted” 更有用，但只是已有文字，未必是完整进度总结。

**尚未解决的缺口：**同文件约第 925 行，`await_child()` 真正放弃等待时仍构造 `summary: None`。它不会等待子智能体生成最终总结。后台卡死强制终结的 [`_stalled_result()`](https://github.com/NousResearch/hermes-agent/blob/f42f579cf8bac4918ac9599bece71618afadd846/tools/async_delegation.py#L1084) 也可能返回空批结果。已经流出的内容可能保存在日志/会话里，但没有统一保证自动汇总回父智能体。

[`_defer_close_after_timeout()`](https://github.com/NousResearch/hermes-agent/blob/f42f579cf8bac4918ac9599bece71618afadd846/tools/delegate_tool_child_run.py#L405) 将关闭资源延迟到执行线程退出，避免 SQLite/网络资源在子线程的 finally 过程中被提前关闭。这是移植超时处理时应保留的修复。相关上游测试为 `test_delegate_interrupted_partial_output.py`、`test_delegate_timeout_cleanup.py`。

## 建议的 Lite 移植范围

采用定向回移并适配 Lite，避免把最新上游整体同步进来。上游现已拆分出多个 delegation 模块，并依赖新的 state schema、数据库连接管理、daemon pool、周期调度、通知呈现、profile 上下文及其他运行时能力；很多依赖不在 Lite 的支持面内。

| 部分 | 建议落点 | 必须实现的契约 |
|---|---|---|
| 子任务生命周期与结果记录 | 新增 `hermes-lite/potato_hermes_lite/delegation*.py` | 稳定任务 ID；状态、摘要、日志引用、产物、错误、部分结果保存；幂等完成/投递 |
| 工具分发和控制 | `hermes-lite/tools/delegate_tool.py` 与 `hermes-lite/run_agent.py::_dispatch_delegate_task` 的最小接入 | 顶层提交即返回；模型可 list/steer/stop；可读取已结束任务的结果；原有同步内部调用明确保留 |
| 超时与回收 | 委派执行层与 Lite 生命周期适配 | 活动窗口；超时先发停止信号；有界宽限期；快照已有产出；执行线程退出后关闭资源 |
| 后台结果送回父会话 | `hermes-lite/tui_gateway/server.py` 和 Lite 通知适配层 | 空闲时启动后续轮；忙碌时排队；按委派 ID 去重；正确认领和确认；结果归属不丢失 |
| 会话存储、权限与生产集成 | Lite session storage / `interface/` 已有桥接边界 | 继承私有状态库权限、内部会话隐藏、工具 allowlist；审批 ID 隔离；Stop/断线/重连/上下文压缩行为一致 |
| 验证及打包 | Lite tests、tests_e2e、tests_packaging | 模拟 provider 完整流程；源码验证与 release builder；生成可审查候选产物 |

第一阶段先修长任务误杀和已有成果保留：活动窗口、中断摘要、超时快照、安全清理。第二阶段接后台任务记录与投递，并提供运行中的追加指令和停止；如果需要“先完成的子任务先交回”，再开放独立完成选项。可以在同一功能分支完成两阶段后统一验收。

为真正满足“不要让主智能体重做”，建议超时结果至少带：

```json
{
  "task_id": "stable-id",
  "status": "timeout",
  "partial": true,
  "summary": "已经确认的部分结论；若未产生可用文本则明确为空",
  "artifacts": [],
  "transcript_ref": "owned-result-reference",
  "error": "停滞原因与已请求的停止操作"
}
```

摘要必须来自已记录内容，不把“已尝试写入/上传”等同于已完成副作用。父智能体可按任务 ID 读取已保存的记录，基于证据只续做未完成部分。只存在于尚未返回的 provider 请求、内存或外部工具中的内容，任何实现都不能凭空恢复。

不建议第一阶段开放自动“恢复已终止子智能体执行”：那需要额外定义如何恢复上下文、处理外部副作用及防止重复执行。运行中 steer 与停止后读取结果已经能显著减少重复劳动。

## 验收条件

1. 用模拟时钟或短窗口验证：持续有进展的任务可以超过旧 600 秒等效上限；真正停滞会结束并带原因。
2. 子任务未结束时，父智能体已能完成另一个独立工具调用/模型轮；前台轮忙碌时子结果排队，空闲后只开启预期次数的后续轮。
3. 正常中断、超时、API 失败、迭代耗尽都返回诚实状态和已有产出；未生成摘要也可按 ID 找到已保存轨迹；迟到线程退出不触发重复完成或重复唤醒。
4. 批次部分完成后异常退出，已完成项仍能恢复投递；进程死亡时的未完成项标记 unknown，不自动重做外部操作。
5. 后续用户消息不误杀子任务；显式 Stop 能停止整棵树；审批待决、过期、取消及迟到响应仍满足当前精确请求 ID 契约。
6. 会话压缩、更换父 agent 实例、重新连接和两个独立会话同时存在时，结果只能到原所属会话；内部子会话仍不能经 Interface 被列出、分享或直接恢复。
7. 明确验证资源清理及上限：线程/连接不泄漏、旧结果有保留策略、容量满时行为可见、费用统计不遗漏、不默默提升并发数。
8. 按仓库要求运行 Lite runtime/packaging 测试、单独运行 mock-provider gateway E2E，并在部署前运行 `verify_lite.py` 和 release builder。若改 Interface，追加相关接口与前端验证。

## 授权边界与本次改动

根目录 `AGENTS.md` 明确要求：

> Do not change the inherited Hermes core agent orchestration semantics, including the main conversation/model turn loop, tool-call scheduling and execution order, delegation coordination, or provider/transport control flow.
>
> If a defect cannot be fixed at a Lite-owned boundary without changing that orchestration, stop and obtain explicit owner approval before editing it.

从同步委派改为后台委派会改变 delegation coordination，即使管理器放到 Lite 自有包内，也不能视为绕过这条要求。可申请的具体授权范围是：**委派分发、子任务生命周期、结果持久化/投递及必要的 gateway 接入**。不需要以此次移植为理由整体升级模型主循环或 provider 控制流。

审查阶段仅新增这份文档，未修改运行代码、`hermes-agent/` 基线、系统配置、用户/权限、systemd 或 `/srv` 部署状态。正式生产配置调整和部署仍需各自的所有者授权。


## 已授权的 Lite 实现

目标版本：`0.19.0+potato.lite.10`。本次按上述授权定向移植，没有整体替换上游核心，也没有改变主模型循环、工具执行顺序或 provider 控制流。

- `potato_hermes_lite/delegation.py` 管理后台子任务；`delegation_runner.py` 实现活动窗口、输出快照及延迟回收；`delegation_store.py` 使用私有 SQLite 保存任务和结果；`delegation_gateway.py` 绑定会话归属、结果认领与确认。
- 模型发起的顶层委派，在绑定了结果消费者的 Potato Web 会话中立即返回任务 ID。主智能体可继续独立工作，并结束当前轮。普通新消息不会中断已派发子任务。没有消费者的调用和嵌套 orchestrator 保留同步行为。
- 默认不再有 600 秒总时长限制。正值 `child_timeout_seconds` 现在是无进展窗口；API、工具及流式输出的进展会续期。默认停滞保护仍为无工具 450 秒、工具内 1200 秒；停止宽限期为 5 秒。独立的模型/工具超时和迭代预算仍有效。
- 超时或中断保留已有输出，包含最多 16,000 字符摘要、近期最多 8 条工具输出（每条最多 2,000 字符）、已记录产物路径及子会话 ID。正常完成的结果保留在 ledger 中；投递给父模型的通知会裁剪上下文，可以通过 `action=result` 查阅已存结果。
- 支持 `spawn/list/steer/stop/result`。并发容量满时明确返回错误，不悄悄退回同步等待。默认同批聚合；开启 `independent_completions` 后按任务或 `group` 分别投递。
- 完成通知经 Interface 的现有受管 `prompt.submit` 路径开启后续轮，维持前台租约和 turn ID 契约。网页新增助手回复，不伪造用户消息；用户忙碌时保留结果等待下一轮。后台任务另有运行时租约，避免主轮结束后被空闲回收。
- 子任务审批使用独立会话键和精确请求 ID；沿用网页审批队列，审批结束或过期后恢复此前的主会话状态。显式 Stop 停止后台任务并抑制自动唤醒，记录仍可读取。
- 数据库位于用户 Hermes home 的 `potato-delegations.db`，文件权限为 0600。已投递/明确停止且无运行中任务的记录保留 7 天；未投递结果不会按该期限丢弃。进程异常退出后的未完成任务标记 `unknown`，保留最近落盘快照及同批已完成结果，不自动重新执行。

必要边界：它不是执行检查点。无法恢复尚未输出的模型内容，也不能强行终止不可中断的 Python/外部调用；这类线程在退出前仍占并发容量。认领/确认可防止通常的重复通知，但进程在接收结果的父轮中途崩溃后，结果可能再次投递，不能据此保证父轮外部副作用恰好一次。`steer` 仍在迭代边界生效。

发布时需要同时更新 Lite 与 Interface，才能获得后台自动续接、后台租约及审批支持。实现阶段只修改源码、测试、清单与候选构建产物；`hermes-agent/`、系统服务和 `/srv` 生产部署未变更。


## 实现验证记录

验证使用 CPython 3.12 的独立 build 环境及临时 HOME/HERMES_HOME。Interface 依赖从现有只读环境加载，额外的 pytest-asyncio 仅安装到 `/tmp/potato-delegation-test-deps`。没有安装或升级生产环境依赖。

| 检查 | 结果 |
|---|---|
| `pytest -c /dev/null -p no:cacheprovider hermes-lite/tests hermes-lite/tests_packaging` | 145 passed |
| `tests_e2e/test_mock_provider_e2e.py`，真实 stdio gateway + 本地模拟模型 | 原 8 项全通过；补充旧客户端同步兼容场景单独通过，共 9 项 |
| `pytest interface/test_*.py`（全部 Interface 测试） | 1210 passed，132 skipped（测试本身的环境/可选条件） |
| 最后一次后台租约及委派 Interface 专项 | 39 passed |
| `node --check interface/static/lite/app.js`、`git diff --check` | 通过 |
| `verify_lite.py` 的源码/候选 wheel 隔离探针 | 通过；模型工具集合和依赖边界维持既有契约 |
| `build_lite_release.py --dry-run` 与实际候选发布构建（含已校验浏览器资产） | 通过 |

新增回归覆盖活动续期与停滞超时、保留工具证据、工作线程退出前不关闭资源、后台容量拒绝、运行中 steer/stop、会话归属隔离、分组完成、压缩别名、领取结果后的异常退出恢复、持久化暂时失败重试、构造失败清理、API 错误与迭代耗尽、旧客户端同步兼容，以及主轮完成后的子审批、过期/迟到响应、重复完成通知和网页助手独立续接。真实 gateway 测试验证了子任务未结束时父轮先完成，普通新消息不停止子任务，之后保存的结果只进入所属父会话。

候选目录：`/tmp/potato-hermes-lite-delegation-20260930-candidate`。

候选 wheel：`potato_hermes_lite-0.19.0+potato.lite.10-py3-none-any.whl`，SHA256：`c03b37e0f09ad7ef7cb07f2e71e054ee0ebd2aad0bd0f0b85eadb7b178210756`。

实现验收阶段尚未部署；后续生产切换记录见下节，Interface 与 Lite 已配套更新。现有工作区中与模型选项等相关的用户改动已保留；前端缓存版本在原 `20260930-deep-test` 基础上追加 `-delegation`，相应缓存版本断言已同步。


## 生产部署记录（2026-09-30）

项目所有者随后明确授权使用 sudo 部署并自行测试。北京时间 18:14 已完成切换：

- 活跃发布：`/opt/potato-hermes-lite/releases/20260930T101051Z-0.19.0-potato.lite.10-c03b37e0`，`current` 已原子切换至该目录。安装器按锁定 wheelhouse 离线安装，依赖指纹、安装文件校验及 `pip check` 通过。
- Interface 更新了 `session_run_manager.py`、`tui_gateway_bridge.py`、新增 `delegation_events.py`，以及 Lite 网页 `app.js`、`index.html`，随后重启 `potato-interface.service`。切换前没有活跃任务租约或 gateway 进程。
- 已安装 wheel 的真实 gateway + 本地模拟模型冒烟测试通过，覆盖后台派发、父会话后续消息、结果回传与去重。
- 部署后 `/health`、首页和新版 JS 均返回 HTTP 200；首页缓存版本和 JS 内容散列与候选一致；服务 active/running、NRestarts=0，启动日志未发现错误标记。
- 旧发布仍保留；Interface 文件备份及切换元数据在 `/var/backups/potato-agent/delegation-20260930T101051Z`。安装/切换脚本和计划保存在 root 私有的 `/var/tmp/potato-delegation-deploy-20260930T101051Z`。
- 如需回退此部署，在确认当前仍为该 Lite 10 发布后，由所有者运行：`sudo python3 /var/tmp/potato-delegation-deploy-20260930T101051Z/cutover.py --rollback`。该命令恢复旧 Interface 文件和 Lite 9 指针并重启 Interface；不会回滚用户数据。

本次部署未变更服务配置、模型密钥、会话密钥、用户映射或生产数据库内容，也未改动 `hermes-agent/` 基线。

## `/proc/stat` 生产兼容修复（Lite 11）

用户反馈后，定向只读检查其指定对话：共有 3 次委派尝试，均在启动子任务前抛出 `/proc/stat` 的 `FileNotFoundError`。对应对话没有子会话和委派 ledger 任务记录，因此这几次失败没有产生可供取回的子任务结果。错误虽然被上层包装成 OpenAI-compatible API call error，实际来源是新引入的本地进程身份记录。

Lite 10 的 `process_identity()` 调用 `psutil.Process().create_time()`；psutil 为将进程出生时钟转换为 Unix 时间，读取全局 `/proc/stat`。生产 Interface 的 `ProtectProc=invisible`、`ProcSubset=pid` 隐藏了该文件，但仍允许读取进程自身的 `/proc/<pid>/stat`。此前安装包冒烟测试未使用这一生产隔离条件，因而遗漏了该兼容问题。已在反馈账号对应的 Linux 用户及相同 procfs 隔离条件下复现旧版错误。

Lite 11 在自有 `delegation_store.py` 边界修复：Linux 使用 PID 与进程出生 ticks 组成版本化身份，直接读取每进程 stat；处理进程名含空格、括号或非 UTF-8 的情形，识别 PID 复用和僵尸进程，兼容旧版身份的保守恢复。非 Linux 保留原有 psutil 路径。此补丁没有修改核心编排流程或放宽服务隔离。比较 Lite 10/11 wheel 的运行内容，仅进程记录模块与包版本发生变化。

验证结果：

- Lite runtime/packaging：155 passed；mock-provider gateway E2E：9 passed。后台委派场景新增实际子工具执行证据。
- `verify_lite.py` 源码/wheel 隔离验证、发布构建 dry-run 与正式构建、离线依赖安装及已安装文件/依赖指纹校验均通过。
- 使用反馈账号对应的 Linux 用户，通过临时 systemd 服务设置 `ProtectProc=invisible`、`ProcSubset=pid`、`PrivateTmp=yes`，确认 `/proc/stat` 不可见后，对**已安装的 Lite 11** 完成进程身份和 ledger 全生命周期验证。
- 同一隔离环境的已安装 gateway 测试通过：子工具执行、父轮提前结束及后续消息、结果回传与去重、父轮结束后的子审批拒绝、审批过期与迟到回复、旧客户端同步兼容。测试使用临时目录和本地模拟模型，没有重放真实对话任务。

沿用所有者的部署授权，北京时间 2026-09-30 19:05:19 切换至 `/opt/potato-hermes-lite/releases/20260930T110403Z-0.19.0-potato.lite.11-95123256`，并重启 Interface。切换前活跃运行租约为 0；切换后 active/running、NRestarts=0，`/health` 返回 HTTP 200 且 status=true，生产 procfs 限制保持有效。检查时切换后的日志中未发现 traceback 或 `/proc/stat` 错误标记。本次仅切换 Lite 发布指针，无需更新 Interface 文件。

Lite 11 wheel SHA256：`951232569e62be012de9d0e4e2d1c60e9ab8836288a7be73b871f0b4a2831cdb`。构建候选位于 `/tmp/potato-hermes-lite-procstat-lite11-candidate`；root 私有部署计划、安装日志、隔离测试脚本与日志在 `/var/tmp/potato-delegation-procstat-20260930T110403Z`，回退元数据在 `/var/backups/potato-agent/delegation-procstat-20260930T110403Z`。旧 Lite 10 发布仍保留；必要时在无活跃任务且当前仍为本次发布的前提下，使用 `sudo python3 /var/tmp/potato-delegation-procstat-20260930T110403Z/cutover.py --rollback` 恢复旧指针并重启 Interface，不回滚用户数据。

## 纯 HTTP 轮询覆盖后台委派与汇总（Lite 12）

用户确认两个助手回复框可以保留，要求修复第二轮回复必须再次输入才能刷新，并明确沿用固定轮询，不引入分级轮询或复杂查询接口。

原因为前端在首轮 `completed` 时停止 `/live` 查询，之后依赖浏览器收到 `delegation.state/ready` 事件重新获取状态；生产环境不支持 WebSocket，后台生成的第二条回复因此无法自行被发现。

本次为现有 `session_live_state` 增加默认值为 0 的 `background_pending` 字段，现有会话与 `/live` 响应自然携带该布尔值，无新增接口。子任务运行或结果等待投递时保持该标记；领取结果前已创建的 queued/starting/running 汇总轮接续轮询。前端沿用 2 秒间隔，只有前台轮和后台工作均结束才停止。输入是否可用仍根据前台轮判断，首轮结束不会仅因后台子任务锁住输入。页面重载可从已保存状态恢复轮询，显式 Stop、gateway 退出及服务启动清理会清除过期标记。

Lite 自有委派适配层在派发返回前发送后台状态，并在同一个管理器锁内读取、发布运行任务与待投递结果的状态，避免任务完成/派发交界处误报空闲。未改动继承的主循环、工具执行次序或模型 provider 流程。

验证结果：

- Interface 全部测试：1213 passed、134 skipped（包含 2 项需单独开启的浏览器回归）。
- 单独开启 Playwright，禁用 WebSocket，仅使用模拟 HTTP 接口：2 passed。覆盖首轮已结束、子任务仍运行、刷新页面、主智能体流式汇总、第二轮在两次查询间瞬间完成、最终停止轮询及保留两个回复框；没有发送额外提示。截图在 `/tmp/potato-delegation-polling-screenshots/`。
- Lite runtime/packaging：155 passed；真实 gateway + 模拟模型 E2E：9 passed。
- 源码/wheel 验证、发布 dry-run 和候选构建、离线依赖和安装指纹校验通过。
- 在生产数据库的 root 私有副本演练新增字段，现有显示对话内容散列、live 行数保持一致，SQLite quick_check 通过；演练副本随后删除。
- 已安装 Lite 12 在反馈账号对应 Linux 用户及 `ProtectProc=invisible`、`ProcSubset=pid`、`PrivateTmp=yes` 下，进程身份、ledger、后台子工具与结果回传/去重、子审批拒绝/过期及同步兼容测试通过。

沿用所有者的部署授权，北京时间 2026-09-30 20:06:42 完成切换。活跃发布为 `/opt/potato-hermes-lite/releases/20260930T120426Z-0.19.0-potato.lite.12-0648807f`；配套更新 Interface 的 `display_store.py`、`session_run_manager.py`、`delegation_events.py`、Lite `app.js` 和 `index.html`。脚本切换前检查无活跃租约，备份原文件及数据库后重启 Interface；没有调整 systemd 隔离配置。上线后服务 active/running、NRestarts=0，`/health`、`/chat`、新 JS 返回 HTTP 200，静态资源散列和缓存版本匹配，实际会话查询已返回新的布尔字段。检查时启动后无 traceback 标记。

Wheel SHA256：`0648807f7b8f15bc5fd37ffcf087981afaead1f30a7a6e33e43dac4bd9effc73`。root 私有部署记录在 `/var/tmp/potato-delegation-polling-20260930T120426Z`，备份在 `/var/backups/potato-agent/delegation-polling-20260930T120426Z`。若需回退且当前仍为本次发布，使用 `sudo python3 /var/tmp/potato-delegation-polling-20260930T120426Z/cutover.py --rollback` 恢复 Lite 11 与原 Interface 文件；新增数据库列向后兼容，脚本不回滚用户数据。

### 子智能体状态提示

按用户要求，聊天列表现有 `Responding…` 位置在主轮空闲且 `background_pending` 为 true 时显示 `Subagents working`，覆盖子任务运行和结果等待交接。主轮运行/汇总时仍显示 `Responding…`，全部完成后清除提示；没有增加说明文字或改变输入、轮询逻辑。

2 项禁用 WebSocket 的浏览器回归通过，验证状态切换、刷新后恢复和最终清除；JS 语法及 diff 检查通过。截图：`/tmp/potato-delegation-status-screenshots/native-delegation-subagents-working.png`。

北京时间 2026-09-30 20:24:50，仅原子更新生产 `app.js` 与 `index.html`，缓存版本追加 `-status`，不重启服务。新页面、JS 内容与健康检查验证通过，Lite 版本仍为 12。原静态文件及散列记录保存在 `/var/backups/potato-agent/subagent-status-20260930T122450Z`。

## 停止后审批窗口无法关闭的修复（2026-10-01）

复查发现：Stop 与子审批事件竞争状态锁时，迟到事件可以把 `interrupted` 改回 `awaiting_approval`；待子任务计数归零，网关退出清理又因只遍历内存计数而遗漏该审批。纯 HTTP 模式下，空闲网关会被回收，再点任何审批按钮均得到 `session not found`，后端遗留审批未清理，刷新页面仍会出现窗口。

按所有者授权，仅修改 Interface 的 `session_run_manager.py` 与 `delegation_events.py`：

- 已中断会话忽略迟到子审批和结果交接事件；用户开启新一轮后正常接受新的子审批。
- 网关退出时同时检查该用户已保存的后台状态与审批，覆盖内存计数已清空的遗留请求。保留事件序号，避免 HTTP 前端拒绝清理后的快照。
- 审批 RPC 明确返回会话不存在时清理该失效会话的审批队列，返回前端已有的 `approval request is no longer pending`，关闭窗口。超时等不确定错误保留待审批请求，允许重试。

智能审批授权范围及单独停止子任务时的即时取消通知不在本次修复范围。前端资源、Lite wheel、模型配置及系统服务配置未变更。

验证：相关测试 52 passed；Interface 全量 1225 passed / 138 skipped；随后新增的“停止后新任务仍可审批”回归 1 passed。6 项禁用 WebSocket 的浏览器测试通过，其中 4 项使用真实 Interface 状态处理与隔离 SQLite 数据验证迟到审批、网关退出、失效会话点击、轮询停止与刷新后不再弹窗，另 2 项验证原有子任务到汇总的轮询交接。Lite 源码验证、发布构建 dry-run、Python 编译及 diff 检查通过。

沿用所有者部署授权，在确认无有效运行租约、无活跃对话或后台任务后，于北京时间 2026-10-01 11:03 更新上述两个后端文件并重启 `potato-interface.service`。生产文件散列与测试版本一致；已安装 Interface 的隔离冒烟验证迟到审批拒收、网关退出清理及失效审批点击恢复通过。`/health`、`/chat` 返回 HTTP 200，服务 active/running、NRestarts=0，部署后检查无 traceback / ERROR / CRITICAL 标记。

原文件、散列、属主权限及部署脚本保存在 `/var/backups/potato-agent/approval-stop-20261001T030327Z`。如需回退，在确认没有活跃任务后运行 `sudo python3 /var/backups/potato-agent/approval-stop-20261001T030327Z/cutover.py --rollback /var/backups/potato-agent/approval-stop-20261001T030327Z`；脚本校验当前文件仍为本次版本后恢复原文件并重启 Interface，不回滚用户数据。
