# Lite Frontend

仓库中已经包含一套不依赖 npm 构建的轻量前端，直接复用 Open WebUI 后端。

当前支持：

- 登录
- 聊天
- 聊天切换
- 文件树浏览
- 文件下载

## 访问地址

Open WebUI 后端启动后，直接访问：

```text
/lite
```

例如：

```text
http://<your-openwebui-host>:3000/lite
```

## 代码位置

- 页面：`open-webui/backend/open_webui/static/lite/index.html`
- 样式：`open-webui/backend/open_webui/static/lite/styles.css`
- 逻辑：`open-webui/backend/open_webui/static/lite/app.js`
- 后端入口：`open-webui/backend/open_webui/main.py`

## 依赖的后端接口

Lite 前端直接调用这些 Open WebUI 接口：

- `POST /api/v1/auths/signin`
- `GET /api/v1/auths/`
- `GET /api/models`
- `GET /api/v1/chats/`
- `GET /api/v1/chats/{id}`
- `POST /api/v1/chats/new`
- `POST /api/v1/chats/{id}`
- `POST /api/chat/completions`

文件树不再依赖 Open WebUI 原生 terminal server，而是依赖 Lite 专用接口：

- `GET /api/lite/files/tree`
- `GET /api/lite/files/download`

## 文件树目录边界

Lite 文件树会根据当前登录用户，从 `users_mapping.yaml` 查找对应目录边界。

当前策略：

- 优先使用 `home_dir`
- 如果没有 `home_dir`，退回 `workdir`

因此 Lite 文件树默认会从：

```text
/home/<linux_user>
```

开始，而不是只从 `work` 子目录开始。

## 当前实现说明

- 默认使用当前用户可见的第一个模型
- 聊天记录直接复用 Open WebUI `chats` 接口
- 左侧可以切换聊天
- 右侧文件树支持展开和折叠
- 点击文件直接下载

## 适用场景

这套 Lite 前端适合：

- 想保留 Open WebUI 后端和多用户能力
- 但不想继续维护 Open WebUI 的重前端
- 希望在云服务器上避免安装完整前端构建依赖
