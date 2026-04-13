# Potato Agent

`potato_agent` 是一套基于 `Hermes` 后端和 `Open WebUI` 前端的多用户智能体部署方案。

这套仓库的目标不是重新实现一个完整的 agent 框架，而是把下面几件事稳定地接起来：

- Open WebUI 负责 Web 登录、用户和模型可见性
- Hermes 负责 LLM 调用、工具执行和终端能力
- 每个 Open WebUI 用户绑定一个独立 Linux 用户
- 每个 Linux 用户运行一个独立 Hermes systemd 服务
- 用户前端统一看到一个模型名 `Hermes`
- 后台通过唯一 `model_id`、唯一 `prefix_id` 和 Linux 用户隔离实现多租户隔离

这份 README 面向第一次接触这个仓库的人，重点说明：

- 这套方案是怎么设计的
- 仓库里每个关键文件做什么
- 如何从源码部署到一台新机器
- 当前在线运行代码和仓库源码之间是什么关系

**核心设计**

这套方案的核心不是“让 Open WebUI 支持多用户”，因为 Open WebUI 本身已经支持多用户登录和模型权限。

真正要解决的是：

- WebUI 用户如何映射到独立 Linux 用户
- 每个用户如何拥有独立 Hermes 实例
- 前端如何保持统一模型名 `Hermes`
- 多个 Hermes OpenAI-compatible 连接如何避免在 Open WebUI 中被合并成同一个模型

解决方案是：

1. 每个用户一条 `users_mapping.yaml` 记录
2. 每个用户一个 Linux 用户，例如 `hmx_alice`
3. 每个用户一个 Hermes 服务，例如 `hermes-alice.service`
4. 每个 Hermes 实例都对外广告同一个基础模型名 `Hermes`
5. Open WebUI 给每个连接一个唯一 `prefix_id`
6. 再通过 private wrapper model 把最终显示名统一成 `Hermes`

这样用户视角只看到一个模型 `Hermes`，但底层实际是：

- Alice -> `hmx_alice` -> `/home/hmx_alice` -> `hermes-alice.service`
- Bob -> `hmx_bob` -> `/home/hmx_bob` -> `hermes-bob.service`

**隔离边界**

真正的隔离来自操作系统层，而不是前端页面。

隔离依赖：

- Linux 用户
- `HOME`
- `systemd User=`
- 文件系统权限

不是只靠：

- Open WebUI 权限模型
- `terminal.cwd`
- 前端隐藏目录

`terminal.cwd` 或 Lite 文件树中的根目录，只是默认落点，不是安全边界。

**仓库结构**

- `README.md`
  当前文档，说明设计和部署方式
- `CURRENT_PROGRESS.md`
  当前机器上的实际进展记录和验证状态
- `Hermes_OpenWebUI_multiuser_SOP.md`
  设计原则和长期架构说明
- `end_to_end_multiuser_integration.md`
  端到端接线思路
- `users_mapping.example.yaml`
  统一 mapping 模板
- `users_mapping.yaml`
  当前机器上的真实 mapping，通常不应直接提交到公共仓库
- `generate_multiuser_bundle.py`
  从 `users_mapping.yaml` 生成整套部署 bundle
- `provision_openwebui_hermes_user.py`
  一键开通一个 Open WebUI 用户并绑定 Linux/Hermes 实例
- `deprovision_openwebui_hermes_user.py`
  一键解绑并删除一个用户
- `deploy_openwebui_from_workspace.sh`
  将工作区 Open WebUI 后端源码整体部署到已安装的 Open WebUI 目录
- `deploy_lite_to_installed_openwebui.sh`
  只部署 Lite 前端相关文件和后端入口，适合快速迭代 `/lite`
- `LITE_FRONTEND.md`
  轻量前端的说明
- `open-webui/`
  Open WebUI 源码工作区
- `hermes-agent/`
  Hermes 源码工作区

**源码目录与实际运行目录**

这一点很重要。

当前仓库里的：

- `open-webui/`
- `hermes-agent/`

是源码工作区，不一定等于线上正在运行的代码。

在当前机器上，线上运行位置是：

- Open WebUI 安装目录：`/opt/open-webui-venv`
- Open WebUI 数据目录：`/opt/open-webui-data`
- Hermes 可执行入口：`/usr/local/bin/hermes`
- Hermes Python 环境：`/opt/hermes-agent-venv`

也就是说：

- 修改仓库里的源码，不会自动让线上服务生效
- 如果你采用“开发环境 + 部署环境”分离方式，需要把改动部署到安装路径后再重启服务

推荐实践：

- `3000` 端口跑稳定版安装环境
- 单独用另一个端口跑源码开发实例

**运行形态**

当前方案默认采用下面这种部署形态：

1. Hermes 作为系统安装命令存在
2. 每个用户一个 systemd 服务
3. Open WebUI 作为系统服务运行
4. Open WebUI 通过 OpenAI-compatible API 连接到各个 Hermes 实例

示例：

- Open WebUI 服务：`open-webui.service`
- Hermes 服务：`hermes-user-test.service`
- 用户 home：`/home/hmx_user_test`
- 用户工作目录：`/home/hmx_user_test/work`

**统一 mapping 文件**

这套方案的事实源是 `users_mapping.yaml`。

它至少描述：

- Open WebUI 用户标识
- Linux 用户名
- 用户 home 目录
- 用户 Hermes home 目录
- 用户 workdir
- Hermes API 端口
- Hermes API key
- Open WebUI 连接前缀
- Wrapper model id
- systemd 服务名

示例见：`users_mapping.example.yaml`

当前支持 `${ENV_NAME}` 占位符，例如：

```yaml
api_key: ${POTATO_AGENT_SHARED_API_KEY}
```

脚本会在运行时从环境变量解析，而不是把真实 key 固定写进 YAML。

**环境变量**

当前推荐至少准备：

```bash
export POTATO_AGENT_SHARED_API_KEY='sk-...'
```

这个变量会被以下脚本使用：

- `generate_multiuser_bundle.py`
- `provision_openwebui_hermes_user.py`
- `deprovision_openwebui_hermes_user.py`

**部署方式一：从 mapping 生成完整 bundle**

这是最适合从零部署到一台新机器的方式。

1. 克隆仓库

```bash
git clone https://github.com/biojiayuxin/potato-agent.git
cd potato-agent
```

2. 准备 mapping

```bash
cp ./users_mapping.example.yaml ./users_mapping.yaml
```

编辑 `users_mapping.yaml`，补齐：

- `hermes.model.base_url`
- `open_webui.wrapper_owner_user_id`
- 每个用户的 `openwebui_user_id`
- 每个用户的邮箱、Linux 用户、目录、端口等信息

3. 导出共享 API key

```bash
export POTATO_AGENT_SHARED_API_KEY='sk-...'
```

4. 生成 bundle

```bash
python3 ./generate_multiuser_bundle.py ./users_mapping.yaml --output-dir ./generated_bundle
```

生成结果包含：

- 每用户 Hermes `.env`
- 每用户 Hermes `config.yaml`
- 每用户 systemd unit
- `openwebui/connections.json`
- `openwebui/wrappers.full.json`
- `openwebui/wrappers.import.json`
- `summary.json`
- `checklist.md`
- `apply_host.sh`

5. 安装 Linux 用户和 Hermes 服务

以 root 执行：

```bash
./generated_bundle/apply_host.sh
```

这个脚本会：

- 创建 Linux 用户
- 创建 `/home/<linux_user>`
- 创建 `/home/<linux_user>/.hermes`
- 创建 `/home/<linux_user>/work`
- 安装每用户 Hermes 配置
- 安装并启动每用户 systemd 服务

6. 验证 Hermes 实例

例如：

```bash
systemctl status hermes-alice.service
curl -H "Authorization: Bearer <alice-api-key>" http://127.0.0.1:8643/v1/models
```

7. 如果你已经有一套安装版 Open WebUI 在跑，需要把仓库里的源码部署到安装路径

```bash
sudo ./deploy_openwebui_from_workspace.sh
```

默认会把：

```text
./open-webui/backend/open_webui
```

同步到：

```text
/opt/open-webui-venv/lib64/python3.11/site-packages/open_webui
```

并重启：

```text
open-webui.service
```

**部署方式二：一键开通单个用户**

如果你的 Open WebUI 和 Hermes 基础环境已经存在，最方便的方式是直接开通用户。

命令：

```bash
python3 ./provision_openwebui_hermes_user.py \
  alice \
  alice@example.com \
  'AlicePassword123'
```

支持的关键参数：

- `--mapping`
- `--openwebui-db`
- `--openwebui-python`
- `--hermes-bin`
- `--openwebui-service`

这个脚本会自动完成：

- 创建或更新 Open WebUI 用户
- 更新 `users_mapping.yaml`
- 创建 Linux 用户
- 写入用户自己的 Hermes `.env` 与 `config.yaml`
- 安装并启动用户自己的 Hermes 服务
- 更新 Open WebUI 连接配置
- 导入 private wrapper model
- 重启 Open WebUI 并验证登录

**删除单个用户**

命令：

```bash
python3 ./deprovision_openwebui_hermes_user.py \
  alice \
  'AlicePassword123'
```

支持：

- `--delete-home`
- `--keep-openwebui-user`
- `--mapping`
- `--openwebui-db`
- `--openwebui-service`

这个脚本会清理：

- wrapper model
- access grants
- Open WebUI connection
- Open WebUI 用户及相关聊天数据
- Hermes systemd 服务
- Linux 用户
- `users_mapping.yaml` 中的用户记录

**Open WebUI 模型侧设计**

这套方案里有一个关键细节：

- Hermes API server 对外广告的基础模型名可以统一是 `Hermes`
- 但是 Open WebUI 的每个连接必须设置唯一 `prefix_id`

否则多个 Hermes 连接返回同名基础模型时，Open WebUI 会把它们合并掉。

正确做法：

1. Hermes 都返回 `Hermes`
2. Open WebUI 连接分别使用：
   - `hermes-alice`
   - `hermes-bob`
3. Open WebUI 内部基础模型变成：
   - `hermes-alice.Hermes`
   - `hermes-bob.Hermes`
4. 再为每个用户创建 wrapper：
   - `id = hermes-alice`
   - `name = Hermes`

最终用户只看到属于自己的 `Hermes`。

**Lite 轻量前端**

仓库里还包含一套不依赖 npm 构建的 Lite 前端，代码位置：

- `open-webui/backend/open_webui/static/lite/index.html`
- `open-webui/backend/open_webui/static/lite/styles.css`
- `open-webui/backend/open_webui/static/lite/app.js`

Open WebUI 后端入口：

- `open-webui/backend/open_webui/main.py`

Lite 前端当前支持：

- 登录
- 聊天
- 聊天切换
- 文件树浏览
- 文件下载

访问入口：

```text
/lite
```

例如：

```text
http://<host>:3000/lite
```

当前 Lite 文件树不是依赖 Open WebUI 原生 terminal server，而是依赖 Lite 专用接口：

- `GET /api/lite/files/tree`
- `GET /api/lite/files/download`

这两个接口会根据当前登录用户，从 `users_mapping.yaml` 找到对应目录边界。

当前策略是：

- 优先使用 `home_dir`
- 没有时退回 `workdir`

也就是说，Lite 文件树默认从 `/home/<linux_user>` 开始，而不是只从 `work` 子目录开始。

**开发后如何部署到已安装 Open WebUI**

如果你采用“开发环境 + 部署环境”分离方式，通常流程是：

1. 修改仓库里的源码
2. 把改动部署到安装版 Open WebUI
3. 重启服务

仓库已经提供两个脚本。

1. 全量部署 Open WebUI 后端源码

适合：

- 修改了 `routers/`
- 修改了 `models/`
- 修改了 `utils/`
- 修改了 `main.py`
- 修改范围不只 Lite

命令：

```bash
sudo ./deploy_openwebui_from_workspace.sh
```

可选参数：

```bash
sudo ./deploy_openwebui_from_workspace.sh \
  --src ./open-webui/backend/open_webui \
  --dest /opt/open-webui-venv/lib64/python3.11/site-packages/open_webui \
  --service open-webui.service
```

如果只想复制，不想立刻重启：

```bash
sudo ./deploy_openwebui_from_workspace.sh --no-restart
```

2. 只部署 Lite 前端相关改动

适合：

- 修改了 `static/lite/index.html`
- 修改了 `static/lite/styles.css`
- 修改了 `static/lite/app.js`
- 修改了 Lite 相关 `main.py` 入口

命令：

```bash
sudo ./deploy_lite_to_installed_openwebui.sh
```

如果只复制，不立刻重启：

```bash
sudo ./deploy_lite_to_installed_openwebui.sh --no-restart
```

这两个脚本的默认路径就是当前机器现场使用的路径：

- 源码目录：`./open-webui/backend/open_webui`
- 安装目录：`/opt/open-webui-venv/lib64/python3.11/site-packages/open_webui`
- 服务名：`open-webui.service`

如果你换了一台机器，安装路径不一样，可以通过 `--dest` 和 `--service` 覆盖。

**新机器最小部署前提**

别人克隆这个仓库后，要能部署起来，至少需要这些前提：

1. Linux 主机，支持 systemd
2. 已安装 Hermes，可执行入口可用，例如：

```bash
/usr/local/bin/hermes --version
```

3. 已安装 Open WebUI，或准备自己从源码/venv 启动
4. Open WebUI 数据目录可访问，例如：

```text
/opt/open-webui-data
```

5. 一个可用的上游模型网关，例如 OpenAI-compatible 服务
6. root 权限，用于：

- 创建 Linux 用户
- 写 systemd unit
- 重启服务

**推荐部署顺序**

1. 先准备 Hermes 可执行环境
2. 再准备 Open WebUI 基础服务
3. 编辑 `users_mapping.yaml`
4. 导出 `POTATO_AGENT_SHARED_API_KEY`
5. 先跑 `generate_multiuser_bundle.py`
6. 再执行 `apply_host.sh`
7. 验证每个 Hermes 服务
8. 验证 Open WebUI 模型可见性
9. 最后验证 Lite 前端和文件树

**常见误区**

1. 误区：只配置 `terminal.cwd` 就完成隔离

不是。

真正隔离来自 Linux 用户、`HOME`、`systemd User=` 和文件权限。

2. 误区：改了仓库源码，线上就会自动生效

不是。

如果你的线上运行环境是安装版：

- 源码在仓库里
- 运行代码在 `/opt/...` 或 venv/site-packages 里

你需要额外部署和重启。

3. 误区：Hermes 天然提供 Open WebUI 文件树所需接口

不是。

Hermes 提供的是 OpenAI-compatible 模型与聊天接口，不是 Open Terminal 那套文件浏览接口。仓库里已经用 Lite 专用文件 API 绕开了这个问题。

**当前仓库更适合谁**

适合：

- 需要多用户 Linux 隔离的 Hermes 部署
- 想继续使用 Open WebUI 用户体系和模型授权
- 不想继续维护 Open WebUI 重前端，希望使用轻量化工作台

不适合：

- 单机单用户的极简试玩环境
- 不打算使用 systemd / Linux 用户隔离的部署方式

**建议阅读顺序**

1. `README.md`
2. `users_mapping.example.yaml`
3. `generate_multiuser_bundle.py`
4. `provision_openwebui_hermes_user.py`
5. `LITE_FRONTEND.md`
6. `CURRENT_PROGRESS.md`

**后续维护建议**

如果你准备长期维护这套系统，建议把环境分成两套：

- 开发环境：直接跑源码
- 部署环境：安装版 + systemd

这样开发时改仓库源码即可，验证通过后再部署到运行环境，避免改动直接影响在线服务。
