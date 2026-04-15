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
  streamingMessageIds: new Set(),
  pendingAttachments: [],
  isSending: false,
  currentAbortController: null,
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
  fileInput: document.getElementById('file-input'),
  attachButton: document.getElementById('attach-button'),
  attachmentList: document.getElementById('attachment-list'),
  sendButton: document.getElementById('send-button'),
  sendButtonIcon: document.getElementById('send-button-icon'),
  newChatButton: document.getElementById('new-chat-button'),
  logoutButton: document.getElementById('logout-button'),
  userEmail: document.getElementById('user-email'),
  fileTree: document.getElementById('file-tree'),
  cwdLabel: document.getElementById('cwd-label'),
  refreshFilesButton: document.getElementById('refresh-files-button'),
  sidebarResizer: document.getElementById('sidebar-resizer'),
  filesResizer: document.getElementById('files-resizer'),
  chatItemTemplate: document.getElementById('chat-item-template'),
  messageTemplate: document.getElementById('message-template'),
};

let loginInFlight = false;
let bootstrapInFlight = false;

const SIDEBAR_WIDTH_KEY = 'lite_sidebar_width';
const FILES_WIDTH_KEY = 'lite_files_width';
const THEME_MODE_KEY = 'lite_theme_mode';

const getSystemTheme = () =>
  window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';

const applyThemeMode = (mode) => {
  const normalizedMode = ['light', 'dark', 'system'].includes(mode) ? mode : 'system';
  const resolvedTheme = normalizedMode === 'system' ? getSystemTheme() : normalizedMode;
  document.documentElement.dataset.themeMode = normalizedMode;
  document.documentElement.dataset.theme = resolvedTheme;

  const themeSelect = document.getElementById('theme-select');
  if (themeSelect) {
    themeSelect.value = normalizedMode;
  }
};

const initThemeControls = () => {
  const themeSelect = document.getElementById('theme-select');
  const saved = localStorage.getItem(THEME_MODE_KEY) || 'system';
  applyThemeMode(saved);

  if (themeSelect) {
    themeSelect.addEventListener('change', (event) => {
      const mode = event.target.value;
      localStorage.setItem(THEME_MODE_KEY, mode);
      applyThemeMode(mode);
    });
  }

  const media = window.matchMedia?.('(prefers-color-scheme: dark)');
  if (!media) return;
  media.addEventListener('change', () => {
    const mode = document.documentElement.dataset.themeMode || 'system';
    if (mode === 'system') {
      applyThemeMode('system');
    }
  });
};

const setCssSize = (name, value) => {
  document.documentElement.style.setProperty(name, `${Math.round(value)}px`);
};

const loadPanelSizes = () => {
  const savedSidebarWidth = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY) || 300);
  const savedFilesWidth = Number(localStorage.getItem(FILES_WIDTH_KEY) || 340);
  setCssSize('--sidebar-width', Math.min(Math.max(savedSidebarWidth, 240), 420));
  setCssSize('--files-width', Math.min(Math.max(savedFilesWidth, 260), 520));
};

const attachHorizontalResizer = (resizer, { getNextSize, setSize, storageKey }) => {
  if (!resizer) return;

  const handlePointerDown = (event) => {
    if (window.innerWidth <= 800) return;
    event.preventDefault();
    resizer.classList.add('dragging');

    const move = (moveEvent) => {
      const next = getNextSize(moveEvent.clientX);
      setSize(next);
      localStorage.setItem(storageKey, String(Math.round(next)));
    };

    const up = () => {
      resizer.classList.remove('dragging');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };

    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  resizer.addEventListener('pointerdown', handlePointerDown);
};

const setComposerBusyState = (busy) => {
  state.isSending = busy;
  if (dom.attachButton) {
    dom.attachButton.disabled = busy;
  }
  if (dom.sendButton) {
    dom.sendButton.type = busy ? 'button' : 'submit';
    dom.sendButton.ariaLabel = busy ? '停止响应' : '发送消息';
    dom.sendButton.title = busy ? '停止响应' : '发送消息';
  }
  if (dom.sendButtonIcon) {
    dom.sendButtonIcon.src = busy ? '/static/lite/icons/stop.png' : '/static/lite/icons/send.png';
  }
};

const autoResizePromptInput = () => {
  if (!dom.promptInput) return;

  const computed = window.getComputedStyle(dom.promptInput);
  const lineHeight = parseFloat(computed.lineHeight || '24') || 24;
  const paddingTop = parseFloat(computed.paddingTop || '0');
  const paddingBottom = parseFloat(computed.paddingBottom || '0');
  const borderTop = parseFloat(computed.borderTopWidth || '0');
  const borderBottom = parseFloat(computed.borderBottomWidth || '0');
  const minHeight = lineHeight + paddingTop + paddingBottom + borderTop + borderBottom;
  const maxHeight = lineHeight * 8 + paddingTop + paddingBottom;

  dom.promptInput.style.height = `${minHeight}px`;
  const nextHeight = Math.min(dom.promptInput.scrollHeight, maxHeight);
  dom.promptInput.style.height = `${nextHeight}px`;
  dom.promptInput.style.overflowY = dom.promptInput.scrollHeight > maxHeight ? 'auto' : 'hidden';
};

const createAttachmentItem = (file) => ({
  itemId: uuid(),
  type: file.type?.startsWith('image/') ? 'image' : 'file',
  name: file.name,
  size: file.size,
  content_type: file.type || '',
  status: 'uploading',
  id: null,
  file: null,
  url: '',
  localPath: '',
  error: '',
});

const formatFileSize = (size) => {
  const bytes = Number(size || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const digits = value >= 10 || unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unitIndex]}`;
};

const getAttachmentStatusLabel = (item) => {
  if (item.status === 'uploading') return '上传中';
  if (item.status === 'error') return item.error || '上传失败';
  return formatFileSize(item.size);
};

const getAttachmentLocalPath = (item) => item?.localPath || item?.file?.path || '';

const buildHermesMessageContent = (message) => {
  const attachments = Array.isArray(message?.files) ? message.files : [];
  if (!attachments.length) {
    return message?.content || '';
  }

  const notes = [];
  for (const item of attachments) {
    const localPath = getAttachmentLocalPath(item);
    if (!localPath) continue;

    const name = item?.name || item?.file?.filename || 'attachment';
    notes.push(
      `[The user sent an attachment: '${name}'. The file is saved at: ${localPath}. Use tools as needed to inspect it and answer the user's request.]`
    );
  }

  const text = typeof message?.content === 'string' ? message.content : '';
  return [...notes, text].filter(Boolean).join('\n\n');
};

const renderAttachments = () => {
  if (!dom.attachmentList) return;
  dom.attachmentList.innerHTML = '';

  if (!state.pendingAttachments.length) {
    dom.attachmentList.hidden = true;
    return;
  }

  dom.attachmentList.hidden = false;

  for (const item of state.pendingAttachments) {
    const chip = document.createElement('div');
    chip.className = 'attachment-chip';
    if (item.status === 'uploading') chip.classList.add('uploading');
    if (item.status === 'error') chip.classList.add('error');

    const body = document.createElement('div');
    body.className = 'attachment-chip-body';

    const name = document.createElement('div');
    name.className = 'attachment-chip-name';
    name.textContent = item.name || '未命名文件';

    const meta = document.createElement('div');
    meta.className = 'attachment-chip-meta';
    meta.textContent = getAttachmentStatusLabel(item);

    body.append(name, meta);

    const removeButton = document.createElement('button');
    removeButton.type = 'button';
    removeButton.className = 'attachment-chip-remove';
    removeButton.textContent = '×';
    removeButton.ariaLabel = `移除附件 ${item.name || ''}`.trim();
    removeButton.title = '移除附件';
    removeButton.disabled = state.isSending;
    removeButton.addEventListener('click', () => {
      state.pendingAttachments = state.pendingAttachments.filter((entry) => entry.itemId !== item.itemId);
      renderAttachments();
    });

    chip.append(body, removeButton);
    dom.attachmentList.append(chip);
  }
};

const uploadAttachment = async (file) => {
  const formData = new FormData();
  formData.append('file', file);

  const uploadResponse = await api('/api/v1/files/?process=false', {
    method: 'POST',
    body: formData,
  });

  const uploadedFile = await uploadResponse.json();
  if (!uploadedFile?.id) {
    throw new Error('文件上传失败');
  }

  const fileDetailsResponse = await api(`/api/v1/files/${uploadedFile.id}`, { method: 'GET' });
  const fileDetails = await fileDetailsResponse.json();
  const resolvedFile = fileDetails?.id ? fileDetails : uploadedFile;

  const localInfoResponse = await api(`/api/lite/files/uploaded/${resolvedFile.id}`, { method: 'GET' });
  const localInfo = await localInfoResponse.json();
  const localPath = localInfo?.path || '';

  if (!localPath) {
    throw new Error('文件已上传，但无法获取本地路径');
  }

  return {
    type: file.type?.startsWith('image/') ? 'image' : 'file',
    id: resolvedFile.id,
    file: resolvedFile,
    url: resolvedFile.id,
    localPath,
    name: resolvedFile.filename || resolvedFile.meta?.name || file.name,
    size: resolvedFile.meta?.size || file.size,
    content_type: resolvedFile.meta?.content_type || file.type || '',
    status: 'uploaded',
    error: resolvedFile.error || '',
  };
};

const handleSelectedFiles = async (files) => {
  const incoming = Array.from(files || []);
  if (!incoming.length) return;

  for (const file of incoming) {
    if (!file || !file.size) {
      showError(dom.chatError, '不能上传空文件。');
      continue;
    }

    const attachment = createAttachmentItem(file);
    state.pendingAttachments = [...state.pendingAttachments, attachment];
    renderAttachments();

    try {
      const uploaded = await uploadAttachment(file);
      state.pendingAttachments = state.pendingAttachments.map((item) =>
        item.itemId === attachment.itemId ? { ...item, ...uploaded } : item
      );
    } catch (error) {
      state.pendingAttachments = state.pendingAttachments.map((item) =>
        item.itemId === attachment.itemId
          ? {
              ...item,
              status: 'error',
              error: String(error.message || '上传失败'),
            }
          : item
      );
      showError(dom.chatError, String(error.message || '上传失败'));
    }

    renderAttachments();
  }

  if (dom.fileInput) {
    dom.fileInput.value = '';
  }
};

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

const sanitizeRenderedHtml = (html) => {
  const template = document.createElement('template');
  template.innerHTML = html;

  const blockedTags = new Set(['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta']);
  const allowedTags = new Set([
    'a',
    'blockquote',
    'br',
    'code',
    'del',
    'em',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'hr',
    'li',
    'ol',
    'p',
    'pre',
    'strong',
    'table',
    'tbody',
    'td',
    'th',
    'thead',
    'tr',
    'ul'
  ]);

  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_ELEMENT);
  const elements = [];
  while (walker.nextNode()) {
    elements.push(walker.currentNode);
  }

  for (const element of elements) {
    const tag = element.tagName.toLowerCase();

    if (blockedTags.has(tag)) {
      element.remove();
      continue;
    }

    if (!allowedTags.has(tag)) {
      const textNode = document.createTextNode(element.textContent || '');
      element.replaceWith(textNode);
      continue;
    }

    for (const attr of [...element.attributes]) {
      const name = attr.name.toLowerCase();
      const value = attr.value || '';

      if (name.startsWith('on')) {
        element.removeAttribute(attr.name);
        continue;
      }

      if (tag === 'a' && name === 'href') {
        if (!/^https?:\/\//i.test(value) && !/^mailto:/i.test(value)) {
          element.removeAttribute(attr.name);
        }
        continue;
      }

      if (tag === 'a' && (name === 'target' || name === 'rel')) {
        continue;
      }

      element.removeAttribute(attr.name);
    }

    if (tag === 'a' && element.getAttribute('href')) {
      element.setAttribute('target', '_blank');
      element.setAttribute('rel', 'noopener noreferrer');
    }
  }

  return template.innerHTML;
};

const renderMarkdown = (text) => {
  const source = String(text ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n');
  const markedApi = globalThis.marked;

  if (!markedApi?.parse) {
    return escapeHtml(source).replaceAll('\n', '<br>');
  }

  const rendered = markedApi.parse(source, {
    gfm: true,
    breaks: true,
    headerIds: false,
    mangle: false
  });

  return sanitizeRenderedHtml(rendered);
};

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

const getRawMessageText = (message) => {
  const blocks = [];

  const content = message?.content;
  if (typeof content === 'string' && content.trim()) {
    blocks.push(content);
  } else if (content != null && typeof content !== 'string') {
    try {
      blocks.push(JSON.stringify(content, null, 2));
    } catch {
      blocks.push(String(content));
    }
  }

  if (typeof message?.reasoningContent === 'string' && message.reasoningContent.trim()) {
    blocks.push(`[Reasoning]\n${message.reasoningContent}`);
  }

  if (Array.isArray(message?.toolCalls) && message.toolCalls.length > 0) {
    blocks.push(`[Tool Calls]\n${JSON.stringify(message.toolCalls, null, 2)}`);
  }

  return blocks.join('\n\n').trim();
};

const normalizeToolCall = (toolCall) => {
  const normalized = {
    id: toolCall?.id || '',
    index: toolCall?.index ?? 0,
    function: {
      name: toolCall?.function?.name || '',
      arguments: toolCall?.function?.arguments || '',
    },
  };

  if (toolCall?.type) {
    normalized.type = toolCall.type;
  }

  return normalized;
};

const mergeToolCallDelta = (existingToolCalls, deltaToolCalls) => {
  const merged = Array.isArray(existingToolCalls) ? existingToolCalls.map((item) => normalizeToolCall(item)) : [];

  for (const deltaToolCall of deltaToolCalls || []) {
    const index = deltaToolCall?.index ?? merged.length;
    while (merged.length <= index) {
      merged.push(normalizeToolCall({ index: merged.length }));
    }

    const current = merged[index];

    if (deltaToolCall?.id) {
      current.id = deltaToolCall.id;
    }

    if (deltaToolCall?.type) {
      current.type = deltaToolCall.type;
    }

    const nameDelta = deltaToolCall?.function?.name;
    if (nameDelta) {
      current.function.name = nameDelta;
    }

    const argsDelta = deltaToolCall?.function?.arguments;
    if (argsDelta) {
      current.function.arguments += argsDelta;
    }
  }

  return merged;
};

const extractInlineProgressLines = (text) => {
  if (!text) return [];
  const matches = text.match(/`(?:💻|🔍|🧠|📁|🌐|📝|⚙️|🛠️)[^`]*`/g) || [];
  return matches;
};

const appendProgressEntries = (message, entries) => {
  const nextEntries = Array.isArray(message.progressLines) ? [...message.progressLines] : [];
  for (const entry of entries || []) {
    if (!entry) continue;
    if (nextEntries[nextEntries.length - 1] === entry) continue;
    nextEntries.push(entry);
  }
  message.progressLines = nextEntries;
};

const createMetaSection = (title, bodyHtml, className = '') => {
  const section = document.createElement('details');
  section.className = `message-meta ${className}`.trim();
  section.open = true;

  const summary = document.createElement('summary');
  summary.textContent = title;
  section.append(summary);

  const body = document.createElement('div');
  body.className = 'message-meta-body';
  body.innerHTML = bodyHtml;
  section.append(body);

  return section;
};

const renderMessageMetaSections = (container, message) => {
  container.innerHTML = '';

  if (typeof message?.reasoningContent === 'string' && message.reasoningContent.trim()) {
    container.append(
      createMetaSection('推理过程', renderMarkdown(message.reasoningContent), 'message-meta-reasoning')
    );
  }

  if (Array.isArray(message?.toolCalls) && message.toolCalls.length > 0) {
    const items = message.toolCalls
      .map((toolCall) => {
        const name = escapeHtml(toolCall?.function?.name || 'unknown_tool');
        const args = escapeHtml(toolCall?.function?.arguments || '{}');
        return `<div class="tool-call-item"><div class="tool-call-name">${name}</div><pre><code>${args}</code></pre></div>`;
      })
      .join('');

    container.append(createMetaSection('工具调用', items, 'message-meta-tools'));
  }

  if (Array.isArray(message?.progressLines) && message.progressLines.length > 0) {
    const lines = message.progressLines
      .map((line) => `<div class="progress-line">${escapeHtml(line)}</div>`)
      .join('');
    container.append(createMetaSection('执行进度', lines, 'message-meta-progress'));
  }
};

const getAttachmentKindLabel = (file) => {
  if ((file?.content_type || '').startsWith('image/')) return '图片';
  return '附件';
};

const renderMessageFiles = (container, message) => {
  container.innerHTML = '';
  const files = Array.isArray(message?.files) ? message.files : [];

  if (!files.length) {
    container.hidden = true;
    return;
  }

  container.hidden = false;

  for (const file of files) {
    const chip = document.createElement('div');
    chip.className = 'message-file-chip';

    const icon = document.createElement('span');
    icon.className = 'message-file-icon';
    icon.textContent = (file?.content_type || '').startsWith('image/') ? 'IMG' : 'FILE';

    const body = document.createElement('div');
    body.className = 'message-file-body';

    const name = document.createElement('div');
    name.className = 'message-file-name';
    name.textContent = file?.name || file?.file?.filename || file?.id || '附件';

    const meta = document.createElement('div');
    meta.className = 'message-file-meta';
    meta.textContent = `${getAttachmentKindLabel(file)}${file?.size ? ` · ${formatFileSize(file.size)}` : ''}`;

    body.append(name, meta);
    chip.append(icon, body);
    container.append(chip);
  }
};

const copyTextToClipboard = async (text) => {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'absolute';
  textarea.style.left = '-9999px';
  document.body.append(textarea);
  textarea.select();
  document.execCommand('copy');
  textarea.remove();
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
    const metaSections = fragment.querySelector('.message-meta-sections');
    const fileSection = fragment.querySelector('.message-files');
    const content = fragment.querySelector('.message-content');
    const streamingIndicator = fragment.querySelector('.message-streaming-indicator');
    const copyButton = fragment.querySelector('.message-copy-button');
    const copyIcon = fragment.querySelector('.message-copy-icon');
    const rawContent = getRawMessageText(message);
    const isStreaming = message.role === 'assistant' && state.streamingMessageIds.has(message.id);
    article.classList.add(message.role === 'user' ? 'user' : 'assistant');
    role.textContent = message.role === 'user' ? '你' : 'Hermes';
    renderMessageMetaSections(metaSections, message);
    renderMessageFiles(fileSection, message);
    content.classList.remove('streaming-placeholder');

    const hasVisibleContent = Boolean(
      String(message.content ?? '').trim() ||
      (typeof message?.reasoningContent === 'string' && message.reasoningContent.trim()) ||
      (Array.isArray(message?.toolCalls) && message.toolCalls.length > 0) ||
      (Array.isArray(message?.progressLines) && message.progressLines.length > 0)
    );

    if (isStreaming && !hasVisibleContent) {
      content.classList.add('streaming-placeholder');
      content.innerHTML = '<span class="message-inline-streaming"><span></span><span></span><span></span></span>';
      streamingIndicator.hidden = true;
      streamingIndicator.classList.remove('inline', 'footer');
    } else {
      content.innerHTML = renderMarkdown(String(message.content ?? ''));
      if (isStreaming && hasVisibleContent) {
        streamingIndicator.hidden = false;
        streamingIndicator.classList.remove('inline');
        streamingIndicator.classList.add('footer');
      } else {
        streamingIndicator.hidden = true;
        streamingIndicator.classList.remove('inline', 'footer');
      }
    }

    copyButton.hidden = !rawContent;

    copyButton.addEventListener('click', async () => {
      if (!rawContent) return;

      try {
        await copyTextToClipboard(rawContent);
        copyIcon.src = '/static/lite/icons/copied.png';
      } catch (error) {
        showError(dom.chatError, '复制失败，请重试。');
      } finally {
        window.setTimeout(() => {
          copyIcon.src = '/static/lite/icons/copy_button.png';
        }, 3000);
      }
    });
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
    const deleteButton = fragment.querySelector('.chat-delete-button');

    title.textContent = getChatDisplayTitle(chat);
    meta.textContent = formatTimestamp(chat.updated_at || chat.created_at);
    if (chat.id === state.activeChatId) {
      button.classList.add('active');
    }

    button.addEventListener('click', () => openChat(chat.id));
    deleteButton.addEventListener('click', async (event) => {
      event.stopPropagation();
      const confirmed = window.confirm(`确定删除聊天“${getChatDisplayTitle(chat)}”吗？`);
      if (!confirmed) return;
      await deleteChat(chat.id);
    });
    dom.chatList.append(fragment);
  }
};

const deleteChat = async (chatId) => {
  await api(`/api/v1/chats/${chatId}`, { method: 'DELETE' });

  const deletingActive = chatId === state.activeChatId;
  state.chats = state.chats.filter((chat) => chat.id !== chatId);

  if (!deletingActive) {
    renderChatList();
    return;
  }

  state.activeChat = null;
  state.activeChatId = null;
  renderWorkspace();

  if (state.chats.length > 0) {
    await openChat(state.chats[0].id);
    return;
  }

  await createChat();
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
  renderAttachments();
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
  state.streamingMessageIds.clear();
  state.pendingAttachments = [];
  const response = await api(`/api/v1/chats/${chatId}`, { method: 'GET' });
  state.activeChat = await response.json();
  state.activeChatId = state.activeChat.id;
  renderWorkspace();
};

const createChat = async () => {
  state.streamingMessageIds.clear();
  state.pendingAttachments = [];
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

const streamChatCompletion = async (payload, assistantMessage, abortController) => {
  const response = await fetch('/api/chat/completions', {
    method: 'POST',
    credentials: 'include',
    signal: abortController.signal,
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
      const lines = chunk.split('\n');
      const eventLine = lines.find((part) => part.startsWith('event: '));
      const eventName = eventLine ? eventLine.slice(7).trim() : '';
      const data = lines
        .filter((part) => part.startsWith('data: '))
        .map((part) => part.slice(6))
        .join('\n')
        .trim();

      if (!data || data === '[DONE]') continue;

      let json;
      try {
        json = JSON.parse(data);
      } catch {
        continue;
      }

      if (eventName === 'hermes.tool.progress') {
        const emoji = json?.emoji || '🛠️';
        const label = json?.label || json?.tool || 'tool';
        appendProgressEntries(assistantMessage, [`${emoji} ${label}`]);
        renderMessages();
        continue;
      }

      const delta = json?.choices?.[0]?.delta || {};

      if (typeof delta.reasoning_content === 'string' && delta.reasoning_content) {
        assistantMessage.reasoningContent = `${assistantMessage.reasoningContent || ''}${delta.reasoning_content}`;
      }

      if (Array.isArray(delta.tool_calls) && delta.tool_calls.length > 0) {
        assistantMessage.toolCalls = mergeToolCallDelta(assistantMessage.toolCalls, delta.tool_calls);
      }

      if (typeof delta.content === 'string' && delta.content) {
        assistantMessage.content += delta.content;

        const progressLines = extractInlineProgressLines(delta.content);
        if (progressLines.length > 0) {
          appendProgressEntries(assistantMessage, progressLines);
        }
      }

      renderMessages();
    }
  }
};

const submitPrompt = async (prompt) => {
  const trimmedPrompt = prompt.trim();
  const uploadedAttachments = state.pendingAttachments.filter((item) => item.status === 'uploaded' && item.id);
  const hasFailedUploads = state.pendingAttachments.some((item) => item.status === 'error');
  const hasUploadingFiles = state.pendingAttachments.some((item) => item.status === 'uploading');

  if (!trimmedPrompt && uploadedAttachments.length === 0) return;
  if (!state.selectedModel) {
    showError(dom.chatError, '当前没有可用模型。');
    return;
  }
  if (hasUploadingFiles) {
    showError(dom.chatError, '文件仍在上传处理中，请稍后发送。');
    return;
  }
  if (hasFailedUploads) {
    showError(dom.chatError, '存在上传失败的文件，请移除后重试。');
    return;
  }

  if (!state.activeChat) {
    await createChat();
  }

  showError(dom.chatError, '');
  const abortController = new AbortController();
  state.currentAbortController = abortController;
  setComposerBusyState(true);

  const chatPayload = state.activeChat.chat;
  chatPayload.models = [state.selectedModel.id];

  const parentId = chatPayload.history.currentId || null;
  const userMessage = {
    id: uuid(),
    parentId,
    childrenIds: [],
    role: 'user',
    content: trimmedPrompt,
    timestamp: nowSeconds(),
    models: [state.selectedModel.id],
    done: true,
    ...(uploadedAttachments.length ? { files: uploadedAttachments } : {}),
  };
  appendChildLink(chatPayload, parentId, userMessage.id);
  upsertMessage(chatPayload, userMessage);

  const assistantMessage = {
    id: uuid(),
    parentId: userMessage.id,
    childrenIds: [],
    role: 'assistant',
    content: '',
    reasoningContent: '',
    toolCalls: [],
    progressLines: [],
    timestamp: nowSeconds(),
    model: state.selectedModel.id,
    done: false,
  };
  state.streamingMessageIds.add(assistantMessage.id);
  appendChildLink(chatPayload, userMessage.id, assistantMessage.id);
  upsertMessage(chatPayload, assistantMessage);
  renderMessages();

  const chain = getMessageChain(state.activeChat)
    .filter((message) => message.id !== assistantMessage.id)
    .map((message) => ({
      role: message.role,
      content: buildHermesMessageContent(message),
    }));

  const payload = {
    stream: true,
    model: state.selectedModel.id,
    messages: chain,
    features: {},
    chat_id: state.activeChat.id,
    id: assistantMessage.id,
    parent_id: userMessage.id,
    parent_message: userMessage,
  };

  try {
    await streamChatCompletion(payload, assistantMessage, abortController);
    assistantMessage.done = true;
    state.streamingMessageIds.delete(assistantMessage.id);
    assistantMessage.timestamp = nowSeconds();
    upsertMessage(chatPayload, assistantMessage);
    state.pendingAttachments = [];
    renderMessages();
    renderAttachments();
    await persistActiveChat();
  } catch (error) {
    if (error.name === 'AbortError') {
      assistantMessage.done = true;
      state.streamingMessageIds.delete(assistantMessage.id);
      assistantMessage.timestamp = nowSeconds();

      if (!assistantMessage.content.trim()) {
        assistantMessage.content = '[已停止响应]';
      }

      upsertMessage(chatPayload, assistantMessage);
      renderMessages();
      await persistActiveChat();
    } else {
      assistantMessage.content = `${assistantMessage.content}\n\n[Error] ${error.message}`.trim();
      assistantMessage.done = true;
      state.streamingMessageIds.delete(assistantMessage.id);
      upsertMessage(chatPayload, assistantMessage);
      renderMessages();
      showError(dom.chatError, error.message);
    }
  } finally {
    state.currentAbortController = null;
    setComposerBusyState(false);
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

const initResizablePanels = () => {
  loadPanelSizes();

  attachHorizontalResizer(dom.sidebarResizer, {
    storageKey: SIDEBAR_WIDTH_KEY,
    getNextSize: (clientX) => Math.min(Math.max(clientX, 240), 420),
    setSize: (value) => setCssSize('--sidebar-width', value),
  });

  attachHorizontalResizer(dom.filesResizer, {
    storageKey: FILES_WIDTH_KEY,
    getNextSize: (clientX) => {
      const viewportWidth = window.innerWidth;
      const desired = viewportWidth - clientX;
      return Math.min(Math.max(desired, 260), 520);
    },
    setSize: (value) => setCssSize('--files-width', value),
  });
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
  state.pendingAttachments = [];
  localStorage.removeItem('lite_token');
  showLogin();
});

dom.newChatButton.addEventListener('click', () => {
  state.pendingAttachments = [];
  renderAttachments();
  createChat().catch((error) => showError(dom.chatError, error.message));
});

dom.composerForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.isSending) return;
  const prompt = dom.promptInput.value;
  dom.promptInput.value = '';
  autoResizePromptInput();
  await submitPrompt(prompt);
});

dom.attachButton.addEventListener('click', () => {
  if (state.isSending) return;
  dom.fileInput?.click();
});

dom.fileInput.addEventListener('change', async (event) => {
  const files = event.target?.files;
  await handleSelectedFiles(files);
});

dom.promptInput.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || event.isComposing) return;
  if (event.ctrlKey || event.metaKey || event.shiftKey) return;

  event.preventDefault();
  dom.composerForm.requestSubmit();
});

dom.promptInput.addEventListener('input', () => {
  autoResizePromptInput();
});

dom.sendButton.addEventListener('click', async (event) => {
  if (!state.isSending) return;
  event.preventDefault();
  event.stopPropagation();
  state.currentAbortController?.abort();
});

dom.refreshFilesButton.addEventListener('click', async () => {
  state.treeCache.clear();
  await fetchWorkspaceFiles().catch((error) => showError(dom.chatError, error.message));
});

initResizablePanels();
initThemeControls();
autoResizePromptInput();
bootstrapSession();
