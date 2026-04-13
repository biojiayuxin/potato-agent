const state = {
  token: localStorage.getItem('lite_token') || '',
  user: null,
  models: [],
  selectedModel: null,
  chats: [],
  activeChatId: null,
  activeChat: null,
  rootPath: '',
  currentPath: '',
  expandedPaths: new Set(),
  treeCache: new Map(),
  isSending: false,
};

const dom = {
  loginView: document.getElementById('login-view'),
  workspaceView: document.getElementById('workspace-view'),
  loginForm: document.getElementById('login-form'),
  loginError: document.getElementById('login-error'),
  chatError: document.getElementById('chat-error'),
  chatList: document.getElementById('chat-list'),
  messages: document.getElementById('messages'),
  chatTitle: document.getElementById('chat-title'),
  modelName: document.getElementById('model-name'),
  composerForm: document.getElementById('composer-form'),
  promptInput: document.getElementById('prompt-input'),
  sendButton: document.getElementById('send-button'),
  newChatButton: document.getElementById('new-chat-button'),
  logoutButton: document.getElementById('logout-button'),
  userEmail: document.getElementById('user-email'),
  fileTree: document.getElementById('file-tree'),
  cwdLabel: document.getElementById('cwd-label'),
  refreshFilesButton: document.getElementById('refresh-files-button'),
  chatItemTemplate: document.getElementById('chat-item-template'),
  messageTemplate: document.getElementById('message-template'),
};

let loginInFlight = false;
let bootstrapInFlight = false;

const api = async (path, options = {}) => {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData) && !headers.has('Content-Type') && options.body) {
    headers.set('Content-Type', 'application/json');
  }
  if (state.token) {
    headers.set('Authorization', `Bearer ${state.token}`);
  }

  const response = await fetch(path, {
    credentials: 'include',
    ...options,
    headers,
  });

  if (!response.ok) {
    let detail = `Request failed: ${response.status}`;
    try {
      const json = await response.json();
      detail = json?.detail || json?.error || detail;
    } catch {
      const text = await response.text().catch(() => '');
      if (text) detail = text;
    }
    throw new Error(detail);
  }

  return response;
};

const nowSeconds = () => Math.floor(Date.now() / 1000);
const uuid = () => {
  if (crypto?.randomUUID) return crypto.randomUUID();
  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const showError = (element, message) => {
  if (!message) {
    element.hidden = true;
    element.textContent = '';
    return;
  }
  element.hidden = false;
  element.textContent = message;
};

const setLoginPending = (pending) => {
  loginInFlight = pending;
  const submitButton = dom.loginForm.querySelector('button[type="submit"]');
  submitButton.disabled = pending;
  submitButton.textContent = pending ? '登录中...' : '登录';
};

const escapeHtml = (text) =>
  text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

const formatTimestamp = (ts) => {
  if (!ts) return '';
  const date = new Date(ts * 1000);
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const getMessageChain = (chat) => {
  const history = chat?.chat?.history;
  if (!history?.currentId || !history?.messages) return [];
  const chain = [];
  let currentId = history.currentId;

  while (currentId) {
    const message = history.messages[currentId];
    if (!message) break;
    chain.push(message);
    currentId = message.parentId;
  }

  return chain.reverse();
};

const createEmptyChatPayload = (title = '新聊天') => ({
  title,
  models: state.selectedModel ? [state.selectedModel.id] : [],
  messages: [],
  history: {
    messages: {},
    currentId: null,
  },
  params: {},
  timestamp: nowSeconds(),
});

const deriveTitleFromMessages = (chat) => {
  const firstUserMessage = getMessageChain(chat).find((message) => message.role === 'user');
  if (!firstUserMessage?.content) return '新聊天';
  return String(firstUserMessage.content).trim().slice(0, 32) || '新聊天';
};

const getChatDisplayTitle = (chat) => chat?.chat?.title || chat?.title || '新聊天';

const upsertMessage = (chatPayload, message) => {
  if (!Array.isArray(chatPayload.messages)) {
    chatPayload.messages = [];
  }
  if (!chatPayload.history) {
    chatPayload.history = { messages: {}, currentId: null };
  }
  if (!chatPayload.history.messages) {
    chatPayload.history.messages = {};
  }
  chatPayload.history.messages[message.id] = message;
  chatPayload.history.currentId = message.id;
  const index = chatPayload.messages.findIndex((item) => item.id === message.id);
  if (index === -1) {
    chatPayload.messages.push(message);
  } else {
    chatPayload.messages[index] = message;
  }
};

const appendChildLink = (chatPayload, parentId, childId) => {
  if (!parentId) return;
  const parent = chatPayload.history.messages[parentId];
  if (!parent) return;
  parent.childrenIds = Array.isArray(parent.childrenIds) ? parent.childrenIds : [];
  if (!parent.childrenIds.includes(childId)) {
    parent.childrenIds.push(childId);
  }
};

const renderMessages = () => {
  dom.messages.innerHTML = '';
  const chain = getMessageChain(state.activeChat);

  if (chain.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent = '开始新的对话。';
    dom.messages.append(empty);
    return;
  }

  for (const message of chain) {
    const fragment = dom.messageTemplate.content.cloneNode(true);
    const article = fragment.querySelector('.message');
    const role = fragment.querySelector('.message-role');
    const content = fragment.querySelector('.message-content');
    article.classList.add(message.role === 'user' ? 'user' : 'assistant');
    role.textContent = message.role === 'user' ? '你' : 'Hermes';
    content.innerHTML = escapeHtml(String(message.content ?? '')).replaceAll('\n', '<br>');
    dom.messages.append(fragment);
  }

  dom.messages.scrollTop = dom.messages.scrollHeight;
};

const renderChatList = () => {
  dom.chatList.innerHTML = '';

  if (state.chats.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent = '还没有聊天记录。';
    dom.chatList.append(empty);
    return;
  }

  for (const chat of state.chats) {
    const fragment = dom.chatItemTemplate.content.cloneNode(true);
    const button = fragment.querySelector('.chat-item');
    const title = fragment.querySelector('.chat-item-title');
    const meta = fragment.querySelector('.chat-item-meta');

    title.textContent = getChatDisplayTitle(chat);
    meta.textContent = formatTimestamp(chat.updated_at || chat.created_at);
    if (chat.id === state.activeChatId) {
      button.classList.add('active');
    }

    button.addEventListener('click', () => openChat(chat.id));
    dom.chatList.append(fragment);
  }
};

const renderWorkspaceHeader = () => {
  dom.userEmail.textContent = state.user?.email || '';
  dom.chatTitle.textContent = getChatDisplayTitle(state.activeChat);
  dom.modelName.textContent = state.selectedModel ? `模型：${state.selectedModel.name || state.selectedModel.id}` : '未选择模型';
};

const renderWorkspace = () => {
  renderChatList();
  renderWorkspaceHeader();
  renderMessages();
};

const normalizeDirectory = (path) => {
  const normalized = String(path || '/').replace(/\\/g, '/');
  return normalized.endsWith('/') ? normalized : `${normalized}/`;
};

const joinPath = (directory, name, type) => {
  const base = normalizeDirectory(directory);
  return type === 'directory' ? `${base}${name}/` : `${base}${name}`;
};

const listDirectory = async (path, force = false) => {
  const directory = normalizeDirectory(path);
  if (!force && state.treeCache.has(directory)) {
    return state.treeCache.get(directory);
  }

  const relativePath = directory === '/' ? '' : directory.replace(/^\/+|\/+$/g, '');
  const json = await api(`/api/lite/files/tree?path=${encodeURIComponent(relativePath)}`, { method: 'GET' }).then((res) => res.json());
  const entries = Array.isArray(json?.entries) ? json.entries : [];
  entries.sort((left, right) => {
    if (left.type !== right.type) return left.type === 'directory' ? -1 : 1;
    return left.name.localeCompare(right.name);
  });
  state.treeCache.set(directory, entries);
  return entries;
};

const downloadFile = async (path) => {
  const relativePath = String(path || '').replace(/^\/+/, '');
  const response = await api(`/api/lite/files/download?path=${encodeURIComponent(relativePath)}`, { method: 'GET' });
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = path.split('/').pop() || 'file';
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};

const renderTreeNode = async (path, depth = 0) => {
  const entries = await listDirectory(path);
  const list = document.createElement('ul');
  list.className = depth === 0 ? 'tree-list' : 'tree-children';

  for (const entry of entries) {
    const nodePath = joinPath(path, entry.name, entry.type);
    const item = document.createElement('li');
    item.className = 'tree-node';

    const row = document.createElement('div');
    row.className = 'tree-row';

    let toggleOrSpacer;
    if (entry.type === 'directory') {
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'tree-toggle';
      toggle.textContent = state.expandedPaths.has(nodePath) ? '▾' : '▸';
      toggle.addEventListener('click', async () => {
        if (state.expandedPaths.has(nodePath)) {
          state.expandedPaths.delete(nodePath);
        } else {
          state.expandedPaths.add(nodePath);
          await listDirectory(nodePath);
        }
        renderFileTree();
      });
      toggleOrSpacer = toggle;
    } else {
      const spacer = document.createElement('span');
      spacer.className = 'tree-spacer';
      toggleOrSpacer = spacer;
    }

    const label = document.createElement('button');
    label.type = 'button';
    label.className = 'tree-label';
    label.textContent = `${entry.type === 'directory' ? '📁' : '📄'} ${entry.name}`;
    if (entry.type === 'directory' && normalizeDirectory(nodePath) === normalizeDirectory(state.currentPath)) {
      label.classList.add('active');
    }
    label.addEventListener('click', async () => {
      if (entry.type === 'directory') {
        state.currentPath = normalizeDirectory(nodePath);
        dom.cwdLabel.textContent = state.currentPath;
        state.expandedPaths.add(normalizeDirectory(nodePath));
        await listDirectory(nodePath);
        renderFileTree();
        return;
      }

      downloadFile(nodePath).catch((error) => showError(dom.chatError, error.message));
    });

    row.append(toggleOrSpacer, label);

    if (entry.type === 'file') {
      const download = document.createElement('button');
      download.type = 'button';
      download.className = 'tree-download';
      download.textContent = '↓';
      download.title = '下载';
      download.addEventListener('click', () => {
        downloadFile(nodePath).catch((error) => showError(dom.chatError, error.message));
      });
      row.append(download);
    }

    item.append(row);

    if (entry.type === 'directory' && state.expandedPaths.has(nodePath)) {
      item.append(await renderTreeNode(nodePath, depth + 1));
    }

    list.append(item);
  }

  return list;
};

const renderFileTree = async () => {
  dom.fileTree.innerHTML = '';
  if (!state.currentPath) {
    dom.fileTree.innerHTML = '<div class="empty-state">当前用户没有可用的工作目录。</div>';
    dom.cwdLabel.textContent = '';
    return;
  }

  dom.cwdLabel.textContent = state.rootPath || state.currentPath;
  const root = normalizeDirectory(state.rootPath || state.currentPath);
  try {
    const tree = await renderTreeNode(root);
    if (!tree.children.length) {
      dom.fileTree.innerHTML = '<div class="empty-state">当前目录为空。</div>';
      return;
    }
    dom.fileTree.append(tree);
  } catch (error) {
    dom.fileTree.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  }
};

const fetchWorkspaceFiles = async () => {
  const json = await api('/api/lite/files/tree', { method: 'GET' }).then((res) => res.json());
  state.rootPath = '/';
  state.currentPath = '/';
  state.expandedPaths = new Set([state.rootPath]);
  state.treeCache.clear();
  await listDirectory(state.rootPath, true);
  dom.cwdLabel.textContent = json?.root || '/';
  await renderFileTree();
};

const persistActiveChat = async () => {
  if (!state.activeChat) return;
  const title = deriveTitleFromMessages(state.activeChat);
  state.activeChat.title = title;
  state.activeChat.chat.title = title;
  renderWorkspaceHeader();

  const response = await api(`/api/v1/chats/${state.activeChat.id}`, {
    method: 'POST',
    body: JSON.stringify({ chat: state.activeChat.chat }),
  });

  const updated = await response.json();
  const index = state.chats.findIndex((chat) => chat.id === updated.id);
  if (index >= 0) {
    state.chats[index] = updated;
  } else {
    state.chats.unshift(updated);
  }
  state.chats.sort((left, right) => (right.updated_at || right.created_at || 0) - (left.updated_at || left.created_at || 0));
  state.activeChat = updated;
  state.activeChatId = updated.id;
  renderChatList();
  renderWorkspaceHeader();
};

const openChat = async (chatId) => {
  const response = await api(`/api/v1/chats/${chatId}`, { method: 'GET' });
  state.activeChat = await response.json();
  state.activeChatId = state.activeChat.id;
  renderWorkspace();
};

const createChat = async () => {
  const response = await api('/api/v1/chats/new', {
    method: 'POST',
    body: JSON.stringify({ chat: createEmptyChatPayload(), folder_id: null }),
  });

  const chat = await response.json();
  state.chats.unshift(chat);
  state.chats.sort((left, right) => (right.updated_at || right.created_at || 0) - (left.updated_at || left.created_at || 0));
  state.activeChat = chat;
  state.activeChatId = chat.id;
  renderWorkspace();
};

const fetchChats = async () => {
  const response = await api('/api/v1/chats/', { method: 'GET' });
  state.chats = await response.json();
  state.chats.sort((left, right) => (right.updated_at || right.created_at || 0) - (left.updated_at || left.created_at || 0));
  renderChatList();

  if (state.activeChatId) {
    const active = state.chats.find((chat) => chat.id === state.activeChatId);
    if (active) {
      await openChat(active.id);
      return;
    }
  }

  if (state.chats.length > 0) {
    await openChat(state.chats[0].id);
    return;
  }

  await createChat();
};

const fetchModels = async () => {
  const response = await api('/api/models', { method: 'GET' });
  const json = await response.json();
  state.models = Array.isArray(json?.data) ? json.data : [];
  state.selectedModel = state.models[0] || null;
  renderWorkspaceHeader();
};

const streamChatCompletion = async (payload, assistantMessage) => {
  const response = await fetch('/api/chat/completions', {
    method: 'POST',
    credentials: 'include',
    headers: {
      Authorization: `Bearer ${state.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let detail = `Chat failed: ${response.status}`;
    try {
      const json = await response.json();
      detail = json?.detail || detail;
    } catch {}
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() || '';

    for (const chunk of chunks) {
      const line = chunk
        .split('\n')
        .find((part) => part.startsWith('data: '));
      if (!line) continue;
      const data = line.slice(6).trim();
      if (!data || data === '[DONE]') continue;

      let json;
      try {
        json = JSON.parse(data);
      } catch {
        continue;
      }

      const delta = json?.choices?.[0]?.delta?.content;
      if (!delta) continue;
      assistantMessage.content += delta;
      renderMessages();
    }
  }
};

const submitPrompt = async (prompt) => {
  if (!prompt.trim()) return;
  if (!state.selectedModel) {
    showError(dom.chatError, '当前没有可用模型。');
    return;
  }

  if (!state.activeChat) {
    await createChat();
  }

  showError(dom.chatError, '');
  state.isSending = true;
  dom.sendButton.disabled = true;

  const chatPayload = state.activeChat.chat;
  chatPayload.models = [state.selectedModel.id];

  const parentId = chatPayload.history.currentId || null;
  const userMessage = {
    id: uuid(),
    parentId,
    childrenIds: [],
    role: 'user',
    content: prompt,
    timestamp: nowSeconds(),
    models: [state.selectedModel.id],
    done: true,
  };
  appendChildLink(chatPayload, parentId, userMessage.id);
  upsertMessage(chatPayload, userMessage);

  const assistantMessage = {
    id: uuid(),
    parentId: userMessage.id,
    childrenIds: [],
    role: 'assistant',
    content: '',
    timestamp: nowSeconds(),
    model: state.selectedModel.id,
    done: false,
  };
  appendChildLink(chatPayload, userMessage.id, assistantMessage.id);
  upsertMessage(chatPayload, assistantMessage);
  renderMessages();

  const chain = getMessageChain(state.activeChat)
    .filter((message) => message.id !== assistantMessage.id)
    .map((message) => ({ role: message.role, content: message.content }));

  const payload = {
    stream: true,
    model: state.selectedModel.id,
    messages: chain,
    features: {},
  };

  try {
    await streamChatCompletion(payload, assistantMessage);
    assistantMessage.done = true;
    assistantMessage.timestamp = nowSeconds();
    upsertMessage(chatPayload, assistantMessage);
    await persistActiveChat();
  } catch (error) {
    assistantMessage.content = `${assistantMessage.content}\n\n[Error] ${error.message}`.trim();
    assistantMessage.done = true;
    upsertMessage(chatPayload, assistantMessage);
    renderMessages();
    showError(dom.chatError, error.message);
  } finally {
    state.isSending = false;
    dom.sendButton.disabled = false;
  }
};

const showWorkspace = () => {
  dom.loginView.hidden = true;
  dom.loginView.style.display = 'none';
  dom.workspaceView.hidden = false;
  dom.workspaceView.style.display = 'grid';
};

const showLogin = () => {
  dom.workspaceView.hidden = true;
  dom.workspaceView.style.display = 'none';
  dom.loginView.hidden = false;
  dom.loginView.style.display = 'grid';
};

const initializeWorkspaceData = async () => {
  let firstError = null;

  try {
    await fetchModels();
  } catch (error) {
    firstError = firstError || error;
  }

  try {
    await fetchChats();
  } catch (error) {
    firstError = firstError || error;
  }

  try {
    await fetchWorkspaceFiles();
  } catch (error) {
    firstError = firstError || error;
  }

  if (firstError) {
    showError(dom.chatError, firstError.message || '工作台初始化失败');
  }
};

const bootstrapSession = async () => {
  if (bootstrapInFlight) return;
  if (!state.token) {
    showLogin();
    return;
  }

  bootstrapInFlight = true;
  try {
    const response = await api('/api/v1/auths/', { method: 'GET' });
    state.user = await response.json();
    showWorkspace();
    renderWorkspace();
    await initializeWorkspaceData();
  } catch {
    localStorage.removeItem('lite_token');
    state.token = '';
    showLogin();
  } finally {
    bootstrapInFlight = false;
  }
};

dom.loginForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (loginInFlight) return;
  showError(dom.loginError, '');
  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;

  try {
    setLoginPending(true);
    const response = await api('/api/v1/auths/signin', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    const json = await response.json();
    state.token = json.token;
    state.user = json;
    localStorage.setItem('lite_token', state.token);
    showWorkspace();
    state.activeChat = null;
    state.activeChatId = null;
    renderWorkspace();
    await initializeWorkspaceData();
  } catch (error) {
    const message = String(error.message || '登录失败');
    showError(
      dom.loginError,
      message.includes('429') ? '登录请求过于频繁，请等待几秒后重试。' : message
    );
  } finally {
    setLoginPending(false);
  }
});

dom.logoutButton.addEventListener('click', () => {
  state.token = '';
  state.user = null;
  localStorage.removeItem('lite_token');
  showLogin();
});

dom.newChatButton.addEventListener('click', () => {
  createChat().catch((error) => showError(dom.chatError, error.message));
});

dom.composerForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const prompt = dom.promptInput.value;
  dom.promptInput.value = '';
  await submitPrompt(prompt);
});

dom.promptInput.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault();
    dom.composerForm.requestSubmit();
  }
});

dom.refreshFilesButton.addEventListener('click', async () => {
  state.treeCache.clear();
  await fetchWorkspaceFiles().catch((error) => showError(dom.chatError, error.message));
});

bootstrapSession();
