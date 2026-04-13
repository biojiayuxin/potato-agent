# Open WebUI Backend Reduction Plan

本文档用于指导后续逐步精简当前仓库中的 Open WebUI 后端代码。

目标不是一次性把 Open WebUI 全部改烂，而是按批次收缩到当前项目真正需要的最小能力集，避免每次改动都触发大面积连锁故障。

## 当前项目真正需要保留的最小能力集

当前项目最终需要保留的核心能力只有：

1. 登录
2. 当前会话用户信息
3. 聊天列表
4. 打开聊天
5. 新建聊天
6. 更新聊天
7. 模型列表 `/api/models`
8. 聊天转发 `/api/chat/completions`
9. Lite 文件树 `/api/lite/files/tree`
10. Lite 文件下载 `/api/lite/files/download`
11. 基础健康检查 `/health`

换句话说，当前项目的目标后端，不是完整 Open WebUI，而是一个：

- 以 Open WebUI 用户系统和数据库为基础
- 以 Hermes 为唯一模型后端
- 以 Lite 前端为主要使用界面
- 以聊天和文件树为核心能力

## 精简原则

后续裁剪必须遵守下面几个原则：

1. 先停止注册，再删除代码
2. 先砍外围功能，再重写聊天主链
3. 先保证 Lite 可用，再继续裁剪数据库相关能力
4. 不要先动当前 provisioning / deprovisioning 依赖的数据库结构
5. 每一轮裁剪后都要验证：
   - 登录
   - `/api/models`
   - `/api/chat/completions`
   - 聊天列表和打开聊天
   - `/lite`
   - `/api/lite/files/tree`

## 当前关键代码链路

当前后端真正影响 Lite 工作流的关键链路如下：

### 1. 启动入口

- `open-webui/backend/open_webui/main.py`

### 2. 登录

- `open-webui/backend/open_webui/routers/auths.py`
- `open-webui/backend/open_webui/utils/auth.py`
- `open-webui/backend/open_webui/models/users.py`
- `open-webui/backend/open_webui/models/auths.py`

### 3. 聊天与聊天列表

- `open-webui/backend/open_webui/routers/chats.py`
- `open-webui/backend/open_webui/models/chats.py`

### 4. 模型聚合与权限过滤

- `open-webui/backend/open_webui/routers/openai.py`
- `open-webui/backend/open_webui/utils/models.py`
- `open-webui/backend/open_webui/models/models.py`
- `open-webui/backend/open_webui/models/access_grants.py`

### 5. 主聊天入口

- `open-webui/backend/open_webui/main.py`
  - `/api/chat/completions`
- `open-webui/backend/open_webui/utils/middleware.py`
- `open-webui/backend/open_webui/utils/chat.py`

### 6. Lite 文件树

- `open-webui/backend/open_webui/main.py`
  - `/lite`
  - `/api/lite/files/tree`
  - `/api/lite/files/download`

## 第一批：立即可以精简的内容

这一批的目标不是立即删文件，而是先让它们不再进入运行路径。

换句话说：

- 先从 `main.py` 取消注册或跳过初始化
- 暂时不删源文件
- 降低启动复杂度和运行耦合

### 1. 启动阶段可直接停用的逻辑

文件：`open-webui/backend/open_webui/main.py`

建议优先停用：

1. `install_tool_and_function_dependencies()`
2. 启动时预拉模型 `get_all_models(...)`
3. `set_tool_servers(...)`
4. `set_terminal_servers(...)`

原因：

- 对当前 Lite 产品没有核心价值
- 只会增加启动时间和故障面
- 当前已经不再依赖原生 terminal server 和 tool server

### 2. 可先从 `main.py` 停止注册的路由

文件：`open-webui/backend/open_webui/main.py`

当前 Lite 路线下，以下 router 都可以作为第一批停用对象：

1. `pipelines.router`
2. `tasks.router`
3. `images.router`
4. `audio.router`
5. `retrieval.router`
6. `configs.router`
7. `users.router`
8. `channels.router`
9. `notes.router`
10. `models.router`
11. `knowledge.router`
12. `prompts.router`
13. `tools.router`
14. `skills.router`
15. `memories.router`
16. `folders.router`
17. `groups.router`
18. `files.router`
19. `functions.router`
20. `evaluations.router`
21. `analytics.router`
22. `utils.router`
23. `terminals.router`
24. `ollama.router`
25. `openai.router` 中非核心配置/验证类用途
26. `scim.router`

第一批保留的公开入口建议只剩：

1. `auths.router`
2. `chats.router`
3. `main.py` 中的 `/api/models`
4. `main.py` 中的 `/api/chat/completions`
5. `main.py` 中的 `/lite`
6. `main.py` 中的 `/api/lite/files/tree`
7. `main.py` 中的 `/api/lite/files/download`
8. `/health`

### 3. 第一批预期收益

收益：

- 启动路径明显变短
- 不再被大量无关 feature 牵连
- 后续删除文件时风险更低
- 更容易看清最小产品真正依赖哪些模块

## 第二批：高价值重写与核心瘦身

这一批是后端体积和复杂度的核心来源，也是当前项目最应该投入精力重写的部分。

### 1. 重写 `utils/middleware.py`

文件：`open-webui/backend/open_webui/utils/middleware.py`

这是当前后端最大的“复杂度黑洞”。

当前它把这些完全不同层次的能力都绑在了一起：

1. folder knowledge
2. retrieval / web search
3. memory
4. image generation
5. code interpreter
6. tools
7. terminal tools
8. MCP
9. skills
10. filters
11. background tasks
12. follow-ups
13. title generation
14. tags generation

对于当前项目，这里面大部分都属于冗余功能。

建议目标：

把它重写成一个最小版聊天前处理层，只做：

1. 验证模型可见性
2. 整理聊天 payload
3. 透传到 Hermes
4. 将结果写回 `Chats`

如果后续不做原生工具链，`utils/middleware.py` 最终体积应远小于当前版本。

### 2. 精简 `routers/openai.py`

文件：`open-webui/backend/open_webui/routers/openai.py`

当前它同时处理：

1. OpenAI-compatible 连接
2. Azure OpenAI
3. Anthropic 兼容
4. Responses API
5. audio speech
6. connection verify
7. config update
8. 多种认证方式

而当前项目真正只需要：

1. 拉 Hermes `/v1/models`
2. 对模型应用 `prefix_id`
3. 对聊天请求做最小代理转发
4. 支持流式输出

建议最终目标：

- 只保留 Hermes 所需的 OpenAI-compatible 子集
- 移除 Azure / Anthropic / Responses / audio / 管理性外围能力

### 3. 精简 `utils/models.py`

文件：`open-webui/backend/open_webui/utils/models.py`

当前它同时处理：

1. OpenAI models
2. Ollama models
3. function models
4. arena models
5. action / filter 元数据注入
6. custom model merge

当前项目只需要：

1. OpenAI-compatible 基础模型
2. custom wrapper model
3. access grant 过滤

建议保留：

1. Hermes 基础模型拉取
2. wrapper model 合并
3. 用户可见模型过滤

建议移除：

1. Ollama 支持
2. function models
3. arena models
4. action / filter 处理

### 4. 精简 `routers/auths.py`

文件：`open-webui/backend/open_webui/routers/auths.py`

当前这个文件过重，混了：

1. 登录
2. 注册
3. LDAP
4. OAuth
5. token exchange
6. trusted header auth
7. API keys
8. webhook
9. 管理员加用户等扩展能力

而当前项目 Lite 前端真正需要：

1. `signin`
2. `get_session_user`
3. 可选 `signout`

建议目标：

- 最终重写为最小认证模块
- 仅保留当前产品真正使用的认证路径

### 5. 精简 `routers/chats.py`

文件：`open-webui/backend/open_webui/routers/chats.py`

Lite 当前只需要：

1. list
2. get by id
3. create
4. update

当前 router 还包含：

1. stats
2. export
3. import
4. share
5. pinned
6. archive
7. folder 相关能力
8. 批量统计导出

这些都应进入后续裁剪范围。

## 第三批：在第二批稳定后可继续移除的内容

这一批不是不能删，而是建议在聊天主链和认证主链完成瘦身后再动。

### 1. `tasks` 体系

相关位置：

- `open-webui/backend/open_webui/main.py`
- `open-webui/backend/open_webui/utils/middleware.py`
- `open-webui/backend/open_webui/routers/tasks.py`

当前 tasks 主要用于：

1. 自动标题
2. follow-ups
3. tags 生成
4. task stop/list

对当前产品价值不高，但在现有实现中和聊天中间层耦合较深。

建议：

- 先让聊天链不依赖 background task
- 再移除 tasks 路由和 redis task 相关逻辑

### 2. `notes / channels / folders / tags`

相关文件：

- `routers/chats.py`
- `models/chats.py`
- `models/users.py`
- `routers/channels.py`
- `routers/notes.py`
- `models/tags.py`
- `models/folders.py`

当前 Lite 前端没有这些产品形态，对当前项目属于明显超配。

但当前这些模型层彼此有交叉引用，因此不建议一开始就直接删除文件。

建议顺序：

1. 先停止对这些模块的公开路由暴露
2. 再从聊天主链中移除引用
3. 最后再删表或删模型代码

### 3. `files.router` 与 `terminals.router`

当前 Lite 文件树已经走自定义接口：

- `/api/lite/files/tree`
- `/api/lite/files/download`

因此 Open WebUI 原本的：

- `routers/files.py`
- `routers/terminals.py`

对当前目标可以视为遗留能力。

建议：

- 在第二批完成后，将其正式停用并移除

## 第四批：最终形态可移除的大模块

这一批对应的是“如果最终只保留 Lite 产品和 Hermes 后端接入”，则基本没有继续保留价值的模块。

### 1. Ollama 整套

相关文件：

- `routers/ollama.py`
- `utils/models.py` 中的 Ollama 聚合逻辑

当前项目目标明确是 Hermes，不是 Ollama。

### 2. Retrieval / Knowledge / Memories

相关文件：

- `routers/retrieval.py`
- `routers/knowledge.py`
- `routers/memories.py`
- `utils/middleware.py` 中相关注入逻辑

如果项目不再沿用 Open WebUI 原生知识库路径，这一整块都可清理。

### 3. Images / Audio / Embeddings

相关位置：

- `main.py` 中相关 endpoint
- `routers/openai.py` 中的音频代理逻辑

如果产品只保留文本聊天和文件树，这些能力都不再是核心需求。

### 4. 企业化外围能力

包括：

1. SCIM
2. LDAP
3. OAuth 扩展路径
4. license 相关逻辑
5. telemetry
6. audit logging

这些都不是当前项目的第一优先级功能。

## 当前阶段不要优先动的部分

虽然目标是瘦身，但下面这些部分当前不建议优先下手。

### 1. 数据库基础层

相关文件：

- `open-webui/backend/open_webui/internal/db.py`
- 各种 `models/*.py`

原因：

- 当前 provisioning / deprovisioning 直接写 SQLite
- 现有自动化依赖当前表结构

在替换 provisioning 方案前，不建议先改 schema。

### 2. `models.models` / `models.access_grants` / `models.groups`

原因：

- 当前每用户只看到自己的 `Hermes` wrapper 依赖这一层
- 没有这层，当前模型隔离策略就会断

### 3. `/api/models` 主链

虽然它很重，但当前 Lite 登录后的模型加载依赖它。

可以重写和精简，但不建议在早期直接移除。

## 推荐的实际裁剪顺序

为了降低风险，建议按这个顺序推进：

### 第一步

先从 `main.py` 停止注册不需要的 router，停掉不必要的 startup 逻辑。

### 第二步

重写最小聊天主链：

- 简化 `/api/chat/completions`
- 摆脱 `utils/middleware.py` 中的大量高级功能依赖

### 第三步

精简 `routers/openai.py`、`utils/models.py`、`routers/auths.py`、`routers/chats.py`

### 第四步

删除 websocket、tasks、retrieval、tools、memory、images、terminal 相关旧功能

### 第五步

最后再处理更深层的模型和数据库清理

## 第一轮裁剪后的理想最小后端

如果第一轮裁剪成功，最终后端应该接近下面这个结构：

### 保留

1. 最小登录模块
2. 最小聊天列表与聊天存储模块
3. 最小模型聚合模块
4. Hermes 聊天代理模块
5. Lite 文件树与下载模块
6. 健康检查

### 移除或停用

1. websocket
2. tasks
3. retrieval
4. images
5. audio
6. notes / channels / folders
7. tools / functions / skills / memories
8. terminals / files 原生路由
9. Ollama
10. Azure / Anthropic / Responses 兼容外围
11. SCIM / LDAP / OAuth 扩展 / telemetry / audit

## 一句话总结

当前最该砍掉的不是某几个零散函数，而是 Open WebUI 后端里整条“原产品工作台能力链”：

- 启动预热
- websocket
- tasks
- 大型中间层 `utils/middleware.py`
- retrieval / tools / memory / images / code interpreter / terminal
- 多 provider 兼容层

后续精简应围绕“保留 Lite 产品所需最小能力”这一条主线推进，而不是继续在原来的大后端上做局部小修。
