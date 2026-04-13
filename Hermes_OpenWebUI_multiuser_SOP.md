# Hermes + Open WebUI 多用户隔离 SOP

版本：v1
适用目标：
- Open WebUI 作为统一前端
- Hermes 作为后端执行层
- 每个 WebUI 用户绑定一个独立 Linux 用户
- 每个用户独立工作目录、独立 Hermes 实例
- Open WebUI 前端对用户统一显示模型名为 “Hermes”
- 后台用唯一 model id 做隔离和授权

--------------------------------------------------
一、设计目标
--------------------------------------------------

本方案采用：
- 方案 1：每用户一个后端模型，但用统一命名规则隐藏复杂度

用户视角：
- Alice 登录后只看到 1 个模型：Hermes
- Bob 登录后也只看到 1 个模型：Hermes

后台实际：
- Alice -> hermes-alice -> Linux 用户 hmx_alice -> /home/hmx_alice/work
- Bob -> hermes-bob -> Linux 用户 hmx_bob -> /home/hmx_bob/work

这样既保留强隔离，又不增加用户认知负担。

--------------------------------------------------
二、职责边界
--------------------------------------------------

1. Open WebUI 负责：
- Web 账户
- 登录
- 用户/组/RBAC
- 模型资源可见性控制

2. Hermes 负责：
- LLM 调用
- 工具调用
- 终端 / 文件 / 会话执行

3. Linux 用户层负责：
- 文件系统隔离
- HOME 隔离
- cwd 隔离
- 进程身份隔离

重要结论：
- Open WebUI 自带多用户、组、模型访问控制
- 但不负责自动把 WebUI 用户映射为 Linux 用户
- 这层映射需要人工或脚本完成

--------------------------------------------------
三、推荐命名规范
--------------------------------------------------

1. WebUI 用户
- 直接使用真实邮箱/用户名
- 例：alice@example.com

2. Linux 用户
统一前缀：hmx_
- hmx_alice
- hmx_bob
- hmx_charlie

3. 工作目录
推荐：
- /home/hmx_alice/work
- /home/hmx_bob/work

4. Hermes profile
推荐与业务用户名一致：
- alice
- bob
- charlie

5. systemd 服务名
- hermes-alice.service
- hermes-bob.service

6. API 端口
建议从 8643 开始连续分配：
- alice -> 8643
- bob -> 8644
- charlie -> 8645

7. Open WebUI 模型
- 内部唯一 model id：
  - hermes-alice
  - hermes-bob
- 对外显示名称统一：
  - Hermes

说明：
- model id 必须唯一
- 显示名称可以统一，因为每个用户只会被授权看到属于自己的那个模型

--------------------------------------------------
四、资源映射表（强烈建议维护）
--------------------------------------------------

建议维护一份 users_mapping.yaml 或表格，作为事实源。

示例：

- webui_user: alice@example.com
  webui_display_name: Alice
  linux_user: hmx_alice
  workdir: /home/hmx_alice/work
  hermes_profile: alice
  systemd_service: hermes-alice.service
  api_port: 8643
  api_key: <独立随机key>
  model_id: hermes-alice
  model_name: Hermes

- webui_user: bob@example.com
  webui_display_name: Bob
  linux_user: hmx_bob
  workdir: /home/hmx_bob/work
  hermes_profile: bob
  systemd_service: hermes-bob.service
  api_port: 8644
  api_key: <独立随机key>
  model_id: hermes-bob
  model_name: Hermes

--------------------------------------------------
五、标准工作流（新增一个用户）
--------------------------------------------------

步骤 1：在 Open WebUI 中创建普通用户
步骤 2：创建对应 Linux 用户
步骤 3：创建独立工作目录
步骤 4：创建对应 Hermes profile
步骤 5：配置该 profile 的 API server 与 cwd
步骤 6：创建对应 systemd 服务，并以对应 Linux 用户身份运行
步骤 7：在 Open WebUI 中创建该用户专属模型
步骤 8：将该模型仅授权给该用户
步骤 9：测试登录后仅能看到 1 个名为 Hermes 的模型

--------------------------------------------------
六、Alice 样板（完整示例）
--------------------------------------------------

假设：
- WebUI 用户：alice@example.com
- Linux 用户：hmx_alice
- Hermes profile：alice
- 端口：8643
- 工作目录：/home/hmx_alice/work
- model id：hermes-alice
- 显示名：Hermes

--------------------------------------------------
6.1 创建 Linux 用户
--------------------------------------------------

命令：

useradd -m -s /bin/bash hmx_alice
passwd hmx_alice
mkdir -p /home/hmx_alice/work
chown -R hmx_alice:hmx_alice /home/hmx_alice
chmod 700 /home/hmx_alice
chmod 700 /home/hmx_alice/work

建议：
- 不要给 sudo
- 不要放共享 SSH key
- HOME 尽量只给该用户自己访问

--------------------------------------------------
6.2 为该用户准备 Hermes HOME
--------------------------------------------------

推荐方式：
- Hermes 以该 Linux 用户运行
- HOME=/home/hmx_alice
- Hermes 配置落到 /home/hmx_alice/.hermes/

创建目录：

mkdir -p /home/hmx_alice/.hermes
chown -R hmx_alice:hmx_alice /home/hmx_alice/.hermes

说明：
- 如果 Hermes 已全局安装在 /root/.local/bin/hermes，需要确认其他用户能否执行
- 更稳妥的方式是使用系统可执行路径或为 hmx_alice 单独安装 Hermes

建议先确认：

sudo -u hmx_alice -H bash -lc 'command -v hermes && hermes --version'

--------------------------------------------------
6.3 创建 Hermes profile / 配置
--------------------------------------------------

目标：让 Alice 的 Hermes 实例具备：
- API_SERVER_ENABLED=true
- API_SERVER_PORT=8643
- API_SERVER_KEY=<独立key>
- terminal.cwd=/home/hmx_alice/work

推荐做法：
- 直接让 hmx_alice 使用自己的 HOME 与自己的 ~/.hermes
- 不一定必须再嵌套 profile；一个 Linux 用户通常只跑一个主实例时，可以直接用默认 profile

如果仍想保留 profile：
- 可使用 profile 名 alice
- 但对于一人一 Linux 用户场景，不强制需要 profile

最低配置要求：

/home/hmx_alice/.hermes/.env
中至少有：

API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8643
API_SERVER_KEY=<独立随机key>

/home/hmx_alice/.hermes/config.yaml
中至少保证：

terminal:
  backend: local
  cwd: /home/hmx_alice/work
  timeout: 180

model:
  default: gpt-5.4
  provider: custom
  base_url: <你的上游模型网关>
  api_key: <你的上游模型key>

注意：
- terminal.cwd 只决定默认目录，不代表绝对隔离
- 真正隔离依赖于 systemd 里的 User=hmx_alice

--------------------------------------------------
6.4 创建 systemd 服务
--------------------------------------------------

文件：
/etc/systemd/system/hermes-alice.service

建议内容：

[Unit]
Description=Hermes Agent for Alice
After=network.target

[Service]
Type=simple
User=hmx_alice
Group=hmx_alice
WorkingDirectory=/home/hmx_alice
Environment=HOME=/home/hmx_alice
ExecStart=/usr/local/bin/hermes gateway run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target

说明：
- User / Group 必须为 hmx_alice
- HOME 必须指向 /home/hmx_alice
- WorkingDirectory 建议就是该用户 HOME
- 如果 hermes 实际路径不是 /usr/local/bin/hermes，需要改成真实路径

启用：

systemctl daemon-reload
systemctl enable --now hermes-alice.service
systemctl status hermes-alice.service

检查监听：

ss -ltnp | grep 8643
curl -H "Authorization: Bearer <API_SERVER_KEY>" http://127.0.0.1:8643/v1/models

--------------------------------------------------
6.5 在 Open WebUI 中为 Alice 建专属模型
--------------------------------------------------

思路：
- Open WebUI 中新增一个后端连接到 Alice 的 Hermes 实例
- 内部 model id 使用 hermes-alice
- 显示名使用 Hermes

连接参数：
- Base URL: http://127.0.0.1:8643/v1
- API Key: <Alice 的 API_SERVER_KEY>

Open WebUI 里实际看到的基础模型可能是 hermes-agent
如果需要包装成“用户专属模型”，建议用 Open WebUI 的模型对象功能建立一个自定义模型：
- id: hermes-alice
- base_model_id: hermes-agent
- name: Hermes

关键原则：
- 后端连接是 Alice 专属的 8643
- 模型显示名统一为 Hermes
- 模型对象只授权给 Alice

--------------------------------------------------
6.6 在 Open WebUI 中授权该模型只给 Alice
--------------------------------------------------

利用 Open WebUI 的模型访问控制：
- /api/v1/models/model/access/update

目标：
- hermes-alice 只授予 Alice
- 不授予其他用户

结果：
- Alice 登录后，只看到一个模型：Hermes
- Bob 登录后不会看到 hermes-alice

--------------------------------------------------
七、Bob 样板（复制 Alice 即可）
--------------------------------------------------

将以下字段替换：
- alice -> bob
- hmx_alice -> hmx_bob
- 8643 -> 8644
- hermes-alice -> hermes-bob
- /home/hmx_alice/work -> /home/hmx_bob/work
- API key 换成 Bob 自己的独立 key

其余 SOP 完全一致。

--------------------------------------------------
八、统一命名策略（方案 A）
--------------------------------------------------

前端统一显示：
- Hermes

后台唯一标识：
- hermes-alice
- hermes-bob
- hermes-charlie

授权策略：
- Alice 只被授权 hermes-alice
- Bob 只被授权 hermes-bob

因此：
- 所有人都只看到 1 个模型，名字都叫 Hermes
- 但每个人实际连的是自己的后端实例

这就是“统一命名隐藏复杂度”的核心做法。

--------------------------------------------------
九、安全建议
--------------------------------------------------

1. 禁止开放注册
- 由管理员手工创建 WebUI 用户
- 避免未受控用户接入

2. 不要让用户级 Hermes 进程以 root 运行
- 必须使用 User=hmx_xxx

3. 每个用户使用独立 API key
- 不要复用一个 API_SERVER_KEY

4. Hermes API 只监听本地
建议：
- API_SERVER_HOST=127.0.0.1

不要直接把每个用户的 Hermes API 暴露公网。

5. Open WebUI 对外统一提供入口
- 用户只访问 Open WebUI
- 不直接访问 Hermes 端口

6. 尽量关闭不必要工具能力
如果某些用户不需要 terminal/file 等高权限工具，应考虑后续做差异化后端配置。

--------------------------------------------------
十、维护建议
--------------------------------------------------

1. 新增用户时，严格按 SOP 执行，不手工临时改
2. 始终维护 users_mapping.yaml 作为事实源
3. 每个用户单独 systemd 服务
4. 端口规划固定，避免冲突
5. 用户删除时同步执行：
- 停服务
- 删除 Open WebUI 模型授权
- 视情况保留或归档工作目录
- 视情况保留或删除 Linux 用户

--------------------------------------------------
十一、推荐的新增用户检查清单
--------------------------------------------------

新增用户完成后，检查：

[ ] WebUI 用户已创建
[ ] Linux 用户已创建
[ ] /home/<linux_user>/work 已创建
[ ] 目录权限正确
[ ] Hermes 服务已启动
[ ] 127.0.0.1:<port>/v1/models 可访问
[ ] Open WebUI 中模型已创建
[ ] 模型显示名为 Hermes
[ ] 模型仅授权给该用户
[ ] 该用户登录后只看到 1 个 Hermes
[ ] 实际执行 pwd 时落在自己的 work 目录

--------------------------------------------------
十二、后续可自动化的方向
--------------------------------------------------

后续可做一个脚本，例如：
create_hermes_user.sh
输入：
- webui_email
- username
- port

自动完成：
- 创建 Linux 用户
- 创建工作目录
- 写 ~/.hermes/.env
- 写 config.yaml
- 创建 systemd 服务
- 输出待配置的 Open WebUI 模型参数

--------------------------------------------------
十三、当前阶段推荐执行策略
--------------------------------------------------

建议先做 1 个样板用户（例如 alice）验证全链路：
- WebUI 用户
- Linux 用户
- Hermes 用户级服务
- Open WebUI 模型授权
- 隔离后的 cwd 验证

确认样板跑通后，再批量复制到其他用户。

这比一开始全量迁移更稳妥。
