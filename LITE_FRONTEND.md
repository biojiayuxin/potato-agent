# Lite Frontend

当前仓库已经新增一个不依赖 npm 构建的轻量前端，直接复用 Open WebUI 后端：

- 登录
- 聊天
- 聊天切换
- 文件树浏览
- 文件下载

## 访问地址

在 Open WebUI 后端启动后，直接访问：

```text
/lite
```

例如：

```text
http://<your-openwebui-host>:8080/lite
```

## 代码位置

- 页面：`open-webui/backend/open_webui/static/lite/index.html`
- 样式：`open-webui/backend/open_webui/static/lite/styles.css`
- 逻辑：`open-webui/backend/open_webui/static/lite/app.js`
- 路由入口：`open-webui/backend/open_webui/main.py`

## 依赖的后端接口

轻前端直接调用现有 Open WebUI 后端接口：

- `POST /api/v1/auths/signin`
- `GET /api/v1/auths/`
- `GET /api/models`
- `GET /api/v1/chats/`
- `GET /api/v1/chats/{id}`
- `POST /api/v1/chats/new`
- `POST /api/v1/chats/{id}`
- `POST /api/chat/completions`
- `GET /api/v1/terminals/`
- `GET /api/v1/terminals/{id}/files/cwd`
- `GET /api/v1/terminals/{id}/files/list`
- `GET /api/v1/terminals/{id}/files/view`

## 当前实现说明

- 默认使用当前用户可见的第一个模型
- 默认使用当前用户可见的第一个 terminal
- 文件树根目录取 terminal 返回的 `cwd`
- 点击目录名进入该目录
- 点击文件直接下载
- 点击左侧聊天项切换聊天

## 适用场景

这套 Lite 前端适合：

- 想保留 Open WebUI 后端和多用户能力
- 但不想继续维护 Open WebUI 的重前端
- 希望在云服务器上避免安装完整前端构建依赖
