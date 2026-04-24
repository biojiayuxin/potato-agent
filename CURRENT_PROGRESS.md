# 当前进展

更新时间：当前会话
项目目录：`/root/potato_agent`

## 当前架构

项目当前已经收口为：

- `interface/` 作为网页入口
- `hermes-agent/` 作为当前兼容的 Hermes 源码工作区
- 每个网页用户绑定一个独立 Linux 用户
- 每个 Linux 用户运行一个独立 Hermes systemd 服务
- 聊天、文件访问和工作目录权限由对应 Linux 用户身份隔离

## 当前主线文件

- `README.md`
- `CURRENT_PROGRESS.md`
- `users_mapping.yaml`
- `interface/`
- `hermes-agent/`
- `provision_interface_user.py`
- `deprovision_interface_user.py`
- `bind_existing_linux_user.py`
- `unbind_existing_linux_user.py`

## 当前已完成能力

### 1. Interface 独立后端已落地

- 不再依赖 Open WebUI 用户库
- 使用 `interface/data/interface.db` 作为网页用户库
- 聊天直接代理 Hermes `/v1/chat/completions`
- 模型列表直接代理 Hermes `/v1/models`
- 会话列表和历史直接读取每用户 Hermes `state.db`

### 2. Lite 前端已迁移到 interface

- 前端位置：`interface/static/lite/`
- 支持：
  - 登录
  - 注册
  - 聊天
  - 会话切换
  - 文件树
  - 文件下载
  - 附件上传
  - Markdown 渲染
  - 流式工具进度展示

### 3. 展示态 transcript 已持久化

- `interface` 会把页面展示用的消息结构写入 `interface.db`
- 刷新页面后优先读取展示态 transcript，而不是重新猜 Hermes 原始消息结构
- 解决了流式样式和刷新后样式不一致的问题

### 4. 标题逻辑已调整

- 新会话先使用临时标题
- 刷新页面或切换会话时：
  - 如果 Hermes 已生成标题，则显示 Hermes 标题
  - 如果 Hermes 标题为空，则保留临时标题

### 5. 注册功能已支持

- 登录页已支持注册入口
- 注册流程使用异步 job
- 注册完成后显示 `Go to sign in`
- 不自动为用户登录
- 注册过程不会中断老用户使用

### 6. 归档功能已接入 interface

- `interface` 启动后会运行后台调度器
- 每天凌晨 `03:00` 检查所有用户会话
- `last_active` 超过 7 天的 `api_server` 会话会归档到：
  - `interface/data/archive.db`
- 归档成功后会删除活跃库中的对应会话
- 已记录归档运行日志 `archive_runs`

### 7. 用户管理脚本已收口

- `provision_interface_user.py`
  - 创建一个系统托管的新用户
  - 自动创建 Linux 用户并开通 Hermes

- `deprovision_interface_user.py`
  - 删除一个系统托管用户
  - 可选 `--delete-home`

- `bind_existing_linux_user.py`
  - 绑定服务器上已存在的 Linux 用户
  - 默认复用该用户的：
    - `~/.hermes`
    - `~/work`

- `unbind_existing_linux_user.py`
  - 安全解绑一个已绑定的现有 Linux 用户
  - 不删除 Linux 用户本身，不删除 home 目录

### 8. Hermes 运行时已改为登录按需启动

- 注册或绑定用户时：
  - 只创建网页账号、Linux 用户映射、`~/.hermes` 配置和 systemd unit
  - 不再立即拉起该用户的 Hermes service
- 用户登录网页工作台时：
  - `interface` 会显式启动对应用户的 Hermes service
  - 等待 `/v1/models` 就绪后才进入聊天页面
- Lite 登录页已新增运行时启动过渡态：
  - 显示 `Starting your Hermes runtime`
  - 启动成功后再进入 workspace
- 如果 Hermes service 启动失败：
  - 前端会直接展示后端返回的调试错误
  - 错误中包含 `systemctl status` / `journalctl` 片段，便于排障
- 为后续空闲休眠机制做了结构预留：
  - `interface` 侧已新增显式运行时启动入口
  - 后续可以在此基础上实现“30 分钟无用户消息则停止服务并回到登录页”
- 模型配置批量下发时：
  - 只重启当前正在运行的 Hermes service
  - 已停止的用户实例会在下次登录工作台时自动应用新配置

### 9. 已接入运行时空闲休眠与任务保护

- `interface` 已新增运行时状态存储：
  - 记录 `runtime_started_at`
  - 记录 `last_user_message_at`
  - 记录后台任务活跃时间
  - 记录登录态撤销时间
- 当前空闲休眠策略：
  - 默认 30 分钟未收到新的用户消息时，允许进入休眠判定
  - 后台调度器按固定周期检查各用户运行时
- 前台长任务保护：
  - `POST /api/chat/completions` 会创建 `foreground_chat lease`
  - 即使前台长时间没有新输出，只要该请求未结束，就不会触发休眠
- 后台任务保护：
  - `interface` 会旁路读取每个用户 `~/.hermes/processes.json`
  - 只要 Hermes 仍登记有活跃后台进程，就不会停止该用户 Hermes service
  - 后台任务运行期间会持续刷新后台活跃时间
  - 后台任务结束后，会从最后一次后台活跃时间重新开始计算空闲超时，不会在任务刚结束时立刻踢下线
- 安全策略：
  - 如果 `processes.json` 读取或解析失败，采用 fail-open
  - 即该轮跳过休眠，避免误杀长后台任务
- 会话回收：
  - 当运行时满足空闲休眠条件并被停止后，当前网页登录态会失效
  - 前端会轮询 `/api/auth/session`，在 idle timeout 后自动回到登录页

## 当前运行约定

- Hermes 命令入口：`/usr/local/bin/hermes`
- Hermes 部署源码目录：`/opt/hermes-agent-src`
- Hermes 虚拟环境：`/opt/hermes-agent-venv`
- Interface 虚拟环境：`/opt/interface-env`
- Interface 访问地址：`http://<host>:3000/lite`

## 当前事实源

- `users_mapping.yaml`
  - 用户到 Linux/Hermes 资源的映射
- `interface/data/interface.db`
  - 网页用户、展示态 transcript、注册任务
- `interface/data/archive.db`
  - 归档会话、归档运行记录

## 当前说明

- 仓库已经删除第一批 Open WebUI 时代遗留代码和部署脚本
- `interface/mapping.py` 已去掉大部分 Open WebUI 兼容字段逻辑
