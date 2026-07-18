# Potato Agent Hermes Lite 边界与迁移计划

> 状态：Lite 已完成生产切换；等待实际用户验收，legacy 运行时仅保留作回滚。
>
> 更新时间：2026-07-17

## 1. 源码与所有权边界

从本轮精简开始，`hermes-lite/` 是 Potato Hermes 唯一的新产品源码、构建输入和运行入口：

- Python 包、固定依赖、runtime profile、能力 manifests、skills 和 release scripts 都从
  `hermes-lite/` 读取。
- Web 会话只启动 Lite 的 `python -m tui_gateway.entry`。
- 每用户 systemd service 使用 Lite 提供的兼容命令 `hermes gateway run --replace`；该命令只是前台
  runtime guard，不启动上游消息网关。
- `interface/` 是 Lite 的宿主和权限边界，不允许通过 import fallback 或 editable install 回到旧源码树。

以下目录不再是新版本的构建或运行输入：

- `hermes-agent/` 是完整上游兼容树，也是当前 Lite 生产版本的临时回滚来源。
- `packaging/hermes/` 中的 upstream vendor、patch replay 和旧 release pipeline 是 legacy 审计/回滚资料。
  新构建不得调用其中的 `verify_profile.py` 或 `build_release.py`。Lite 自带的
  `hermes-lite/runtime-profile.yaml` 是新运行时 profile。

用户数据不属于任何源码树。`HERMES_HOME`、`state.db`、用户 skills、mapping 和 Interface 数据库继续放在
原有外部路径，切换二进制时不得复制、清空或重建这些目录。

## 2. 能力边界

Lite profile 定义 27 个允许暴露给模型的逻辑工具：

```text
terminal, process
read_file, write_file, patch, search_files
vision_analyze
browser_navigate, browser_snapshot, browser_click, browser_type
browser_scroll, browser_back, browser_press, browser_get_images
browser_vision, browser_console, browser_cdp, browser_dialog
skills_list, skill_view, skill_manage
execute_code, todo, memory, session_search, delegate_task
```

这 27 项是逻辑上限，不是每次模型请求必须携带的固定数量。运行时 availability check 可以根据当前依赖、
凭据和本地 backend 隐藏暂不可用的工具，例如 `vision_analyze`、`browser_cdp` 和 `browser_dialog`。任何请求
都只能是该清单的子集，不能通过用户配置、MCP、plugin 或 request override 增加第 28 项工具。

固定策略：

- model provider 只允许 `custom`，API mode 只允许 `codex_responses` 和 `chat_completions`。
- browser 只允许本地 backend；memory 使用内置实现，context engine 使用 compressor。
- web/search、MCP、cron、kanban、MoA、媒体生成、voice、computer use、消息平台和外部 provider 不进入
  runtime。
- user/project/entry-point plugin 和运行时自动依赖安装关闭。
- 保持现有图片附件和模型视觉路径，不新增 image attachment tool 或新的原生图片协议。当前智能体已有的
  图片理解能力继续按现有链路工作。
- 不新增 `clarify`、`sudo`、`secret` 网页交互；`clarify` 不进入模型 schema。已有且仍需要的
  `approval.respond` 和 `session.interrupt` 契约保留。

Interface 稳定契约包括 `session.create`、`session.resume`、`prompt.submit`、`session.interrupt`、
`approval.respond`、`command.dispatch`，以及 message/tool/error 流式事件。

## 3. 独立构建与验证

新构建只使用 Lite 自带脚本：

```bash
python3 hermes-lite/scripts/verify_lite.py \
  --python /opt/hermes-agent-venv/bin/python3

python3 hermes-lite/scripts/build_lite_release.py \
  --dry-run \
  --python /opt/hermes-agent-venv/bin/python3

python3 hermes-lite/scripts/build_lite_release.py \
  --python /opt/hermes-agent-venv/bin/python3 \
  --output /tmp/potato-hermes-lite-release
```

这里的旧 venv 仅临时提供构建依赖，不是源码输入。隔离 probe 使用 `python -S -B -P`，只注入 Lite release
tree 和明确列出的 dependency site-packages，并验证模块来源、source/wheel inventory、固定依赖、console
entrypoint、逻辑 27 工具上限和 forbidden imports。生产 Lite venv 故意不安装 `packaging`、`setuptools` 等构建
依赖；release 验证继续使用独立 build venv，不能为了运行 verifier 扩大生产 venv。

测试入口：

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/hermes-agent-venv/bin/python3 -B -m pytest \
  -q -p no:cacheprovider -c /dev/null \
  hermes-lite/tests

PYTHONDONTWRITEBYTECODE=1 /opt/hermes-agent-venv/bin/python3 -B -m pytest \
  -q -p no:cacheprovider -c /dev/null \
  hermes-lite/tests_packaging

PYTHONDONTWRITEBYTECODE=1 /opt/hermes-agent-venv/bin/python3 -B -m pytest \
  -q -p no:cacheprovider -c /dev/null \
  hermes-lite/tests_e2e/test_mock_provider_e2e.py
```

mock-provider E2E 使用临时 `HOME/HERMES_HOME` 和本机 HTTP endpoint，已覆盖
`prompt.submit -> message.complete`、`session.resume`、流式 interrupt 和危险 terminal command 的 approval
deny；同时覆盖 `codex_responses` 的真实 stdio gateway 调用链，以及终止事件 `response.output=null` 时的
流式重建。它不访问真实 provider 或生产数据。

## 4. 生产迁移状态

2026-07-17 已完成全量 Lite 切换；2026-07-18 部署审批链路热修复：

```text
release:       /opt/potato-hermes-lite/releases/20260718T034917Z-0.16.0-potato.lite.3-fa84c4f3
current:       /opt/potato-hermes-lite/current
version:       0.16.0+potato.lite.3
wheel SHA256:  fa84c4f33ceb2a98acf6e55d631ecc08a211740bb24e04184694e33f7765dbce
profile SHA256: 976592bc66c27bfdf596e25fe556d7fc19c09d4d23da436298670fe574d691ec
```

- `/usr/local/bin/hermes` 和 Interface gateway Python 均指向 Lite；进程列表中没有 legacy
  `hermes-agent` 或 `slash_worker`。
- 11 个 mapped Hermes unit 已原子刷新，统一使用 Lite skills、browser 和
  `/opt/potato-hermes-lite/current/config/runtime-profile.yaml`。
- 首次全量切换前 active 的 3 个 Hermes service 已恢复；`.lite.2` 修复切换前 active 的 1 个服务也已恢复；
  其余 unit 保持原来的 inactive 状态。
- 正式切换分别比较 12,744、12,746 和 12,761 条保护记录，mapping、Interface 数据、Hermes 配置、数据库、
  sessions、memories 和 skills 均无新增、删除或变化。每用户 `HERMES_HOME/home` 的大型软件环境使用路径、
  ownership、mode、size、mtime、ctime 和 symlink target 的流式树摘要，避免读取数十 GB 包缓存内容。
- `.lite.2` 补入 Lite 自有的 Responses 流运行时；`potato_agent` 使用实际 `codex_responses` 配置和临时
  `HOME/HERMES_HOME` 完成 `session.create -> prompt.submit -> message.complete`，未写入原有 Hermes 会话。
- `.lite.3` 为每个危险命令审批生成稳定 ID，Gateway 只解析精确匹配项并幂等处理同选择重试；前端增加
  提交超时、连接恢复和操作所有权保护，旧请求不能解锁后续审批或用短暂网络错误覆盖权威对话快照。
- mock-provider RPC E2E、真实本地 Chrome 导航/snapshot、`pip check`、Lite tests 和 Interface 定向回归均通过。
- unit、代码和状态回滚资料保存在 root-only `/var/backups/potato-agent/` 下。

## 5. Legacy 删除门禁

完整 `hermes-agent/`、`packaging/hermes/`、`/opt/hermes-agent-src` 和 `/opt/hermes-agent-venv` 已不参与在线
进程，但本轮仍保留作回滚。只有满足以下条件后才物理删除：

1. 用户完成 create/resume、图片、skills、interrupt、approval、浏览器和实际模型调用验收。
2. 观察期内 Interface 与 Hermes unit 没有 Lite 特有回归。
3. 使用保留备份完成一次明确的回滚演练，或由 owner 明确接受不演练直接退役的风险。
4. 删除操作再次排除 mapping、Interface data、所有 `HERMES_HOME`、用户 workdir 和托管 skills。

任何带 `--delete`/`rsync --delete` 的操作都不得指向 mapping、Interface data 或 `HERMES_HOME`。删除旧源码
不等于删除用户数据，二者必须作为不同变更处理。
