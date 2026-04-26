const state = {
  user: null,
  pendingWorkspaceUser: null,
  models: [],
  selectedModel: null,
  sessions: [],
  activeSessionId: null,
  activeSession: null,
  draftSession: null,
  messages: [],
  rootPath: '',
  workspaceRoot: '',
  currentPath: '',
  fileBrowserMode: 'home_only',
  homePath: '',
  expandedPaths: new Set(),
  treeCache: new Map(),
  streamingMessageIds: new Set(),
  pendingAttachments: [],
  isSending: false,
  currentAbortController: null,
  chatErrorTimer: null,
  authPollTimer: null,
  signupJobId: null,
  signupPollTimer: null,
  pendingApproval: null,
  approvalSubmitting: false,
};

const dom = {
  loginView: document.getElementById('login-view'),
  workspaceView: document.getElementById('workspace-view'),
  loginForm: document.getElementById('login-form'),
  loginError: document.getElementById('login-error'),
  authCard: document.getElementById('auth-card'),
  authCardLabel: document.getElementById('auth-card-label'),
  authCardTitle: document.getElementById('auth-card-title'),
  authCardCopy: document.getElementById('auth-card-copy'),
  registerForm: document.getElementById('register-form'),
  registerError: document.getElementById('register-error'),
  showRegisterButton: document.getElementById('show-register-button'),
  showLoginButton: document.getElementById('show-login-button'),
  registerNavActions: document.getElementById('register-nav-actions'),
  signupWaitView: document.getElementById('signup-wait-view'),
  signupWaitTitle: document.getElementById('signup-wait-title'),
  signupWaitCopy: document.getElementById('signup-wait-copy'),
  signupWaitError: document.getElementById('signup-wait-error'),
  signupWaitSteps: document.getElementById('signup-wait-steps'),
  signupGoLoginButton: document.getElementById('signup-go-login-button'),
  signupBackButton: document.getElementById('signup-back-button'),
  runtimeStartView: document.getElementById('runtime-start-view'),
  runtimeStartTitle: document.getElementById('runtime-start-title'),
  runtimeStartCopy: document.getElementById('runtime-start-copy'),
  runtimeStartError: document.getElementById('runtime-start-error'),
  runtimeStartRetryButton: document.getElementById('runtime-start-retry-button'),
  runtimeStartBackButton: document.getElementById('runtime-start-back-button'),
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
  fileOpenControls: document.getElementById('file-open-controls'),
  filePathInput: document.getElementById('file-path-input'),
  fileOpenButton: document.getElementById('file-open-button'),
  fileHomeButton: document.getElementById('file-home-button'),
  sidebarResizer: document.getElementById('sidebar-resizer'),
  filesResizer: document.getElementById('files-resizer'),
  chatItemTemplate: document.getElementById('chat-item-template'),
  messageTemplate: document.getElementById('message-template'),
  approvalModal: document.getElementById('approval-modal'),
  approvalDescription: document.getElementById('approval-description'),
  approvalCommand: document.getElementById('approval-command'),
  approvalError: document.getElementById('approval-error'),
  approvalAllowOnce: document.getElementById('approval-allow-once'),
  approvalAllowSession: document.getElementById('approval-allow-session'),
  approvalAllowAlways: document.getElementById('approval-allow-always'),
  approvalDeny: document.getElementById('approval-deny'),
};

let loginInFlight = false;
let bootstrapInFlight = false;
let signupInFlight = false;

const SIDEBAR_WIDTH_KEY = 'lite_sidebar_width';
const FILES_WIDTH_KEY = 'lite_files_width';
const THEME_MODE_KEY = 'lite_theme_mode';
const MAX_ATTACHMENT_SIZE_BYTES = 20 * 1024 * 1024;
const AUTH_POLL_INTERVAL_MS = 60 * 1000;
const ATTACHMENT_BLOCK_START = '<potato-files>';
const ATTACHMENT_BLOCK_END = '</potato-files>';
const ATTACHMENT_HINT_LINE = 'Use the attachment local paths above if you need to inspect the files.';

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
    dom.sendButton.ariaLabel = busy ? 'Stop response' : 'Send message';
    dom.sendButton.title = busy ? 'Stop response' : 'Send message';
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

const nowSeconds = () => Math.floor(Date.now() / 1000);
const uuid = () => {
  if (crypto?.randomUUID) return crypto.randomUUID();
  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const createDraftSession = () => ({
  id: `ui-${uuid()}`,
  title: 'New chat',
  preview: '',
  started_at: nowSeconds(),
  last_active: nowSeconds(),
  message_count: 0,
  isDraft: true,
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

const getAttachmentTooLargeMessage = () => 'Upload file too large (> 20 MB).';

const escapeHtml = (text) =>
  String(text ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

const showError = (element, message) => {
  if (!message) {
    element.hidden = true;
    element.textContent = '';
    return;
  }
  element.hidden = false;
  element.textContent = message;
};

const showChatError = (message) => {
  if (state.chatErrorTimer) {
    window.clearTimeout(state.chatErrorTimer);
    state.chatErrorTimer = null;
  }

  showError(dom.chatError, message);

  if (!message) return;

  state.chatErrorTimer = window.setTimeout(() => {
    if (dom.chatError?.textContent === message) {
      showError(dom.chatError, '');
    }
    state.chatErrorTimer = null;
  }, 10000);
};

const setApprovalSubmitting = (submitting) => {
  state.approvalSubmitting = submitting;
  const buttons = [
    dom.approvalAllowOnce,
    dom.approvalAllowSession,
    dom.approvalAllowAlways,
    dom.approvalDeny,
  ].filter(Boolean);
  for (const button of buttons) {
    button.disabled = submitting;
  }
};

const renderApprovalModal = () => {
  const approval = state.pendingApproval;
  if (!approval) {
    if (dom.approvalModal) {
      dom.approvalModal.hidden = true;
    }
    showError(dom.approvalError, '');
    setApprovalSubmitting(false);
    return;
  }

  if (dom.approvalModal) {
    dom.approvalModal.hidden = false;
  }
  if (dom.approvalDescription) {
    dom.approvalDescription.textContent = approval.description || 'Hermes marked this command as dangerous and is waiting for your decision.';
  }
  if (dom.approvalCommand) {
    dom.approvalCommand.textContent = approval.command || '';
  }
};

const clearPendingApproval = () => {
  state.pendingApproval = null;
  showError(dom.approvalError, '');
  renderApprovalModal();
};

const submitApprovalDecision = async (choice) => {
  const approval = state.pendingApproval;
  if (!approval?.approvalId || state.approvalSubmitting) return;
  showError(dom.approvalError, '');
  setApprovalSubmitting(true);
  try {
    await api(`/api/chat/approvals/${encodeURIComponent(approval.approvalId)}`, {
      method: 'POST',
      body: JSON.stringify({ choice }),
    });
    clearPendingApproval();
  } catch (error) {
    showError(dom.approvalError, String(error.message || 'Approval request failed'));
    setApprovalSubmitting(false);
  }
};

const stopAuthPolling = () => {
  if (state.authPollTimer) {
    window.clearTimeout(state.authPollTimer);
    state.authPollTimer = null;
  }
};

const handleSessionExpired = (message) => {
  stopAuthPolling();
  state.user = null;
  state.pendingWorkspaceUser = null;
  resetWorkspaceState();
  showLogin();
  showError(dom.loginError, message || 'Workspace slept after 30 minutes of inactivity. Please sign in again.');
};

const setLoginPending = (pending) => {
  loginInFlight = pending;
  const submitButton = dom.loginForm.querySelector('button[type="submit"]');
  submitButton.disabled = pending;
  submitButton.textContent = pending ? 'Signing in...' : 'Sign in';
};

const setSignupPending = (pending) => {
  signupInFlight = pending;
  const submitButton = dom.registerForm.querySelector('button[type="submit"]');
  submitButton.disabled = pending;
  submitButton.textContent = pending ? 'Creating account...' : 'Create account';
};

const stopSignupPolling = () => {
  if (state.signupPollTimer) {
    window.clearTimeout(state.signupPollTimer);
    state.signupPollTimer = null;
  }
};

const setAuthViewMode = (mode) => {
  const showSignin = mode === 'signin';
  const showRegister = mode === 'register';
  const showWait = mode === 'signup-wait';
  const showRuntimeStart = mode === 'runtime-start';

  if (showSignin) {
    dom.authCardLabel.textContent = 'Sign in';
    dom.authCardTitle.textContent = 'Enter Potato Agent';
    dom.authCardCopy.textContent = 'Use the account already mapped to your Hermes runtime.';
  }

  if (showRegister) {
    dom.authCardLabel.textContent = 'Register';
    dom.authCardTitle.textContent = 'Create your workspace';
    dom.authCardCopy.textContent = 'A dedicated Linux user and Hermes runtime will be provisioned for your account.';
  }

  if (showWait) {
    dom.authCardLabel.textContent = 'Provisioning';
    dom.authCardTitle.textContent = 'Creating your workspace';
    dom.authCardCopy.textContent = 'Please wait while your dedicated runtime is being provisioned.';
  }

  if (showRuntimeStart) {
    dom.authCardLabel.textContent = 'Starting runtime';
    dom.authCardTitle.textContent = 'Waking your workspace';
    dom.authCardCopy.textContent = 'We are starting the Hermes service bound to your account before entering the workspace.';
  }

  dom.loginForm.hidden = !showSignin;
  dom.showRegisterButton.hidden = !showSignin;
  dom.registerForm.hidden = !showRegister;
  dom.registerNavActions.hidden = !showRegister;
  dom.signupWaitView.hidden = !showWait;
  dom.runtimeStartView.hidden = !showRuntimeStart;
  if (showSignin) {
    showError(dom.loginError, '');
  }
  if (showRegister) {
    showError(dom.registerError, '');
  }
};

const resetWorkspaceState = () => {
  state.sessions = [];
  state.activeSession = null;
  state.activeSessionId = null;
  state.draftSession = null;
  state.messages = [];
  state.models = [];
  state.selectedModel = null;
  state.rootPath = '';
  state.workspaceRoot = '';
  state.currentPath = '';
  state.fileBrowserMode = 'home_only';
  state.homePath = '';
  state.expandedPaths = new Set();
  state.treeCache.clear();
  state.streamingMessageIds.clear();
  state.pendingAttachments = [];
  state.currentAbortController = null;
  state.isSending = false;
  state.pendingApproval = null;
  state.approvalSubmitting = false;
  renderApprovalModal();
};

const setRuntimeStartState = ({ title, copy, error, canRetry, canBack }) => {
  if (title) dom.runtimeStartTitle.textContent = title;
  if (copy) dom.runtimeStartCopy.textContent = copy;
  showError(dom.runtimeStartError, error || '');
  dom.runtimeStartRetryButton.hidden = !canRetry;
  dom.runtimeStartBackButton.hidden = !canBack;
};

const showRuntimeStartView = ({ title, copy, error = '', canRetry = false, canBack = false }) => {
  showLogin();
  setAuthViewMode('runtime-start');
  setRuntimeStartState({ title, copy, error, canRetry, canBack });
};

const startWorkspaceRuntime = async (user, { allowRetry = true, source = 'signin' } = {}) => {
  state.pendingWorkspaceUser = user || null;
  showRuntimeStartView({
    title: 'Starting your Hermes runtime',
    copy: 'We are waking the dedicated Hermes service bound to your account. This usually takes a few seconds.',
    error: '',
    canRetry: false,
    canBack: false,
  });

  try {
    const response = await api('/api/runtime/start', { method: 'POST' });
    const json = await response.json();
    state.user = json?.user || user;
    state.pendingWorkspaceUser = null;
    resetWorkspaceState();
    showWorkspace();
    renderWorkspace();
    await initializeWorkspaceData();
    startAuthPolling();
  } catch (error) {
    state.user = null;
    resetWorkspaceState();
    const message = String(error.message || 'Failed to start Hermes runtime');
    showRuntimeStartView({
      title: 'Failed to start your Hermes runtime',
      copy: source === 'restore'
        ? 'The previous session is valid, but the Hermes runtime could not be started. Review the error below before retrying.'
        : 'Sign-in succeeded, but the Hermes runtime could not be started. Review the error below before retrying.',
      error: message,
      canRetry: allowRetry,
      canBack: true,
    });
  }
};

const pollAuthSession = async () => {
  stopAuthPolling();
  if (!state.user) return;

  try {
    const response = await fetch('/api/auth/session', {
      method: 'GET',
      credentials: 'include',
    });
    const json = await response.json();
    if (!json?.authenticated) {
      handleSessionExpired(
        json?.message || 'Workspace slept after 30 minutes of inactivity. Please sign in again.'
      );
      return;
    }
  } catch {
    // Ignore transient polling failures and try again later.
  }

  state.authPollTimer = window.setTimeout(pollAuthSession, AUTH_POLL_INTERVAL_MS);
};

const startAuthPolling = () => {
  stopAuthPolling();
  if (!state.user) return;
  state.authPollTimer = window.setTimeout(pollAuthSession, AUTH_POLL_INTERVAL_MS);
};

const applySignupJobState = (job) => {
  const status = job?.status || 'pending';
  const titleMap = {
    pending: 'Request queued',
    provisioning: 'Creating your workspace',
    completed: 'Workspace ready',
    failed: 'Setup failed',
  };
  const copyMap = {
    pending: 'Your request has been accepted and is waiting to start.',
    provisioning: 'We are creating a dedicated Linux user and Hermes runtime for your account.',
    completed: 'Your workspace is ready. Return to the sign-in page and use the account you just created.',
    failed: 'The workspace could not be created. You can go back and try again.',
  };
  dom.signupWaitTitle.textContent = titleMap[status] || 'Creating your workspace';
  dom.signupWaitCopy.textContent = copyMap[status] || copyMap.pending;
  showError(dom.signupWaitError, status === 'failed' ? (job?.error_message || 'Setup failed') : '');
  dom.signupGoLoginButton.hidden = status !== 'completed';
  dom.signupBackButton.hidden = status !== 'failed';

  for (const step of dom.signupWaitSteps.querySelectorAll('.signup-step')) {
    const stepName = step.dataset.step;
    step.classList.remove('active', 'completed');
    if (status === 'pending' && stepName === 'pending') step.classList.add('active');
    if (status === 'provisioning') {
      if (stepName === 'pending') step.classList.add('completed');
      if (stepName === 'provisioning') step.classList.add('active');
    }
    if (status === 'completed') {
      if (stepName === 'pending' || stepName === 'provisioning') step.classList.add('completed');
      if (stepName === 'completed') step.classList.add('active');
    }
    if (status === 'failed') {
      if (stepName === 'pending') step.classList.add('completed');
      if (stepName === 'provisioning') step.classList.add('active');
    }
  }
};

const pollSignupJob = async () => {
  if (!state.signupJobId) return;
  try {
    const response = await api(`/api/auth/signup/${encodeURIComponent(state.signupJobId)}`, { method: 'GET' });
    const json = await response.json();
    const job = json?.job || null;
    applySignupJobState(job);
    const status = job?.status || 'pending';
    if (status === 'completed' || status === 'failed') {
      stopSignupPolling();
      return;
    }
  } catch (error) {
    showError(dom.signupWaitError, String(error.message || 'Failed to check provisioning status'));
  }
  state.signupPollTimer = window.setTimeout(pollSignupJob, 2000);
};

const api = async (path, options = {}) => {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData) && !headers.has('Content-Type') && options.body) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(path, {
    credentials: 'include',
    ...options,
    headers,
  });

  if (!response.ok) {
    let detail = `Request failed: ${response.status}`;
    let payload = null;
    try {
      payload = await response.json();
      detail = payload?.detail || payload?.message || payload?.error?.message || payload?.error || detail;
    } catch {
      const text = await response.text().catch(() => '');
      if (text) detail = text;
    }
    if ((response.status === 401 || response.status === 403) && (payload?.reason === 'idle_timeout' || /Workspace slept/i.test(detail) || /Please sign in again/i.test(detail))) {
      handleSessionExpired(payload?.message || detail);
    }
    throw new Error(detail);
  }

  return response;
};

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
    mangle: false,
  });

  return sanitizeRenderedHtml(rendered);
};

const formatTimestamp = (ts) => {
  if (!ts) return '';
  const date = new Date(Number(ts) * 1000);
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const normalizeClipboardFiles = (clipboardData) => {
  if (!clipboardData?.items?.length) return [];

  const files = [];
  for (const item of clipboardData.items) {
    if (item.kind !== 'file') continue;

    const file = item.getAsFile();
    if (!file) continue;

    const normalized = file.name
      ? file
      : new File([file], `pasted_${new Date().toISOString().replace(/[-:]/g, '').replace('T', '_').slice(0, 15)}.bin`, {
          type: file.type || 'application/octet-stream',
          lastModified: Date.now(),
        });

    files.push(normalized);
  }

  return files;
};

const normalizeUploadResult = (json, file) => ({
  itemId: uuid(),
  type: file.type?.startsWith('image/') ? 'image' : 'file',
  id: json?.id || '',
  localPath: json?.path || '',
  name: json?.name || file.name,
  size: Number(json?.size || file.size || 0),
  content_type: json?.content_type || file.type || '',
  status: 'uploaded',
  error: '',
});

const createAttachmentItem = (file) => ({
  itemId: uuid(),
  type: file.type?.startsWith('image/') ? 'image' : 'file',
  name: file.name,
  size: file.size,
  content_type: file.type || '',
  status: 'uploading',
  id: null,
  localPath: '',
  error: '',
});

const getAttachmentStatusLabel = (item) => {
  if (item.status === 'uploading') return 'Uploading';
  if (item.status === 'error') return item.error || 'Upload failed';
  return formatFileSize(item.size);
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
    name.textContent = item.name || 'Untitled file';

    const meta = document.createElement('div');
    meta.className = 'attachment-chip-meta';
    meta.textContent = getAttachmentStatusLabel(item);

    body.append(name, meta);

    const removeButton = document.createElement('button');
    removeButton.type = 'button';
    removeButton.className = 'attachment-chip-remove';
    removeButton.textContent = '×';
    removeButton.ariaLabel = `Remove attachment ${item.name || ''}`.trim();
    removeButton.title = 'Remove attachment';
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
  const response = await api('/api/files/upload', { method: 'POST', body: formData });
  return response.json();
};

const handleSelectedFiles = async (files) => {
  const incoming = Array.from(files || []);
  if (!incoming.length) return;

  for (const file of incoming) {
    if (!file || !file.size) {
      showChatError('Cannot upload an empty file.');
      continue;
    }

    if (file.size > MAX_ATTACHMENT_SIZE_BYTES) {
      showChatError(getAttachmentTooLargeMessage(file));
      continue;
    }

    const attachment = createAttachmentItem(file);
    state.pendingAttachments = [...state.pendingAttachments, attachment];
    renderAttachments();

    try {
      const uploadedJson = await uploadAttachment(file);
      const uploaded = normalizeUploadResult(uploadedJson, file);
      state.pendingAttachments = state.pendingAttachments.map((item) =>
        item.itemId === attachment.itemId ? { ...item, ...uploaded } : item
      );
    } catch (error) {
      state.pendingAttachments = state.pendingAttachments.map((item) =>
        item.itemId === attachment.itemId
          ? {
              ...item,
              status: 'error',
              error: String(error.message || 'Upload failed'),
            }
          : item
      );
      showChatError(String(error.message || 'Upload failed'));
    }

    renderAttachments();
  }

  if (dom.fileInput) {
    dom.fileInput.value = '';
  }
};

const serializeAttachmentsForHermes = (attachments) =>
  attachments.map((item) => ({
    name: item?.name || 'attachment',
    path: item?.localPath || '',
    content_type: item?.content_type || '',
    size: Number(item?.size || 0),
  }));

const buildHermesUserContent = (text, attachments) => {
  const normalizedText = String(text || '').trim();
  const files = serializeAttachmentsForHermes(attachments).filter((item) => item.path);
  if (!files.length) {
    return normalizedText;
  }
  const block = JSON.stringify(files, null, 2);
  return `${ATTACHMENT_BLOCK_START}\n${block}\n${ATTACHMENT_BLOCK_END}\n\n${ATTACHMENT_HINT_LINE}\n\n${normalizedText}`.trim();
};

const parseStoredUserContent = (content) => {
  const source = typeof content === 'string' ? content : String(content ?? '');
  const start = source.indexOf(ATTACHMENT_BLOCK_START);
  const end = source.indexOf(ATTACHMENT_BLOCK_END);

  if (start !== 0 || end === -1) {
    return { content: source, files: [] };
  }

  const jsonText = source.slice(ATTACHMENT_BLOCK_START.length, end).trim();
  const remainder = source.slice(end + ATTACHMENT_BLOCK_END.length).trimStart();
  let visible = remainder;
  if (visible.startsWith(ATTACHMENT_HINT_LINE)) {
    visible = visible.slice(ATTACHMENT_HINT_LINE.length).trimStart();
  }

  let parsedFiles = [];
  try {
    parsedFiles = JSON.parse(jsonText);
  } catch {
    parsedFiles = [];
  }

  const files = Array.isArray(parsedFiles)
    ? parsedFiles.map((item) => ({
        type: String(item?.content_type || '').startsWith('image/') ? 'image' : 'file',
        name: item?.name || 'attachment',
        localPath: item?.path || '',
        content_type: item?.content_type || '',
        size: Number(item?.size || 0),
      }))
    : [];

  return { content: visible, files };
};

const extractDisplayProgressLines = (text) => {
  if (!text) return [];
  const matches = text.match(/`(?:💻|🔍|🧠|📁|🌐|📝|⚙️|🛠️)[^`]*`/g) || [];
  return matches;
};

const normalizeMessageForDisplay = (message) => {
  const role = String(message?.role || 'assistant');
  const hasDisplayShape =
    'reasoningContent' in (message || {}) ||
    'toolCalls' in (message || {}) ||
    'progressLines' in (message || {}) ||
    'done' in (message || {});
  const normalizedContent = typeof message?.content === 'string' ? message.content : String(message?.content ?? '');
  const base = {
    id: `msg-${message?.id ?? uuid()}`,
    role,
    content: '',
    reasoningContent: hasDisplayShape
      ? String(message?.reasoningContent ?? '')
      : (typeof message?.reasoning === 'string' ? message.reasoning : ''),
    toolCalls: hasDisplayShape
      ? (Array.isArray(message?.toolCalls) ? message.toolCalls.map((item) => normalizeToolCall(item)) : [])
      : (Array.isArray(message?.tool_calls) ? message.tool_calls.map((item) => normalizeToolCall(item)) : []),
    progressLines: hasDisplayShape
      ? (Array.isArray(message?.progressLines) ? [...message.progressLines] : [])
      : (role === 'assistant' ? extractDisplayProgressLines(normalizedContent) : []),
    timestamp: Number(message?.timestamp || 0),
    files: hasDisplayShape && Array.isArray(message?.files) ? [...message.files] : [],
    done: hasDisplayShape ? Boolean(message?.done ?? true) : true,
  };

  if (role === 'user') {
    if (hasDisplayShape) {
      base.content = normalizedContent;
      if ((!base.files || base.files.length === 0) && normalizedContent.includes(ATTACHMENT_BLOCK_START)) {
        const parsed = parseStoredUserContent(normalizedContent);
        base.content = parsed.content;
        base.files = parsed.files;
      }
    } else {
      const parsed = parseStoredUserContent(message?.content);
      base.content = parsed.content;
      base.files = parsed.files;
    }
  } else {
    base.content = normalizedContent;
  }

  return base;
};

const hasDisplayContent = (message) =>
  Boolean(
    String(message?.content ?? '').trim() ||
    (typeof message?.reasoningContent === 'string' && message.reasoningContent.trim()) ||
    (Array.isArray(message?.toolCalls) && message.toolCalls.length > 0) ||
    (Array.isArray(message?.progressLines) && message.progressLines.length > 0) ||
    (Array.isArray(message?.files) && message.files.length > 0)
  );

const getCurrentChatEntries = () => {
  if (state.draftSession) {
    return [state.draftSession, ...state.sessions];
  }
  return state.sessions;
};

const deriveTitleFromMessages = (messages) => {
  const firstUserMessage = (messages || []).find((message) => message.role === 'user');
  if (!firstUserMessage) return 'New chat';

  const text = String(firstUserMessage.content || '').trim();
  if (text) {
    return text.slice(0, 32) || 'New chat';
  }

  const firstFile = Array.isArray(firstUserMessage.files) ? firstUserMessage.files[0] : null;
  if (firstFile?.name) {
    return String(firstFile.name).slice(0, 32);
  }

  return 'New chat';
};

const getActiveChatTitle = () => {
  if (state.activeSession?.title && state.activeSession.title !== 'New chat') {
    return state.activeSession.title;
  }
  return deriveTitleFromMessages(state.messages);
};

const getRawMessageText = (message) => {
  const blocks = [];

  const files = Array.isArray(message?.files) ? message.files : [];
  if (files.length > 0) {
    const fileLines = files.map((file) => `- ${file.name}${file.localPath ? ` (${file.localPath})` : ''}`);
    blocks.push(`[Attachments]\n${fileLines.join('\n')}`);
  }

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

const mergeDisplayToolCalls = (existingToolCalls, nextToolCalls) => {
  const merged = Array.isArray(existingToolCalls)
    ? existingToolCalls.map((item) => normalizeToolCall(item))
    : [];
  const seen = new Set(
    merged.map((item, index) => item.id || `${item.function.name}:${item.function.arguments}:${index}`)
  );

  for (const toolCall of nextToolCalls || []) {
    const normalizedToolCall = normalizeToolCall(toolCall);
    const key = normalizedToolCall.id || `${normalizedToolCall.function.name}:${normalizedToolCall.function.arguments}`;
    if (seen.has(key)) continue;
    merged.push(normalizedToolCall);
    seen.add(key);
  }

  return merged;
};

const mergeAssistantDisplayMessage = (target, source) => {
  const merged = {
    ...target,
    files: Array.isArray(target?.files) ? [...target.files] : [],
    toolCalls: Array.isArray(target?.toolCalls) ? target.toolCalls.map((item) => normalizeToolCall(item)) : [],
    progressLines: Array.isArray(target?.progressLines) ? [...target.progressLines] : [],
  };

  const sourceContent = String(source?.content ?? '').trim();
  if (sourceContent) {
    merged.content = merged.content ? `${merged.content}\n\n${source.content}` : source.content;
  }

  const sourceReasoning = String(source?.reasoningContent ?? '').trim();
  if (sourceReasoning) {
    merged.reasoningContent = merged.reasoningContent
      ? `${merged.reasoningContent}\n\n${source.reasoningContent}`
      : source.reasoningContent;
  }

  if (Array.isArray(source?.toolCalls) && source.toolCalls.length > 0) {
    merged.toolCalls = mergeDisplayToolCalls(merged.toolCalls, source.toolCalls);
  }

  if (Array.isArray(source?.progressLines) && source.progressLines.length > 0) {
    appendProgressEntries(merged, source.progressLines);
  }

  if (Array.isArray(source?.files) && source.files.length > 0) {
    merged.files = [...merged.files, ...source.files];
  }

  if (Number(source?.timestamp || 0) >= Number(merged.timestamp || 0)) {
    merged.timestamp = Number(source?.timestamp || merged.timestamp || 0);
    if (sourceContent || sourceReasoning) {
      merged.id = source.id;
    }
  }

  merged.done = source?.done ?? merged.done;
  return merged;
};

const normalizeSessionMessagesForDisplay = (messages) => {
  const normalizedMessages = [];
  let pendingAssistant = null;

  const flushPendingAssistant = () => {
    if (!pendingAssistant) return;
    if (hasDisplayContent(pendingAssistant)) {
      normalizedMessages.push(pendingAssistant);
    }
    pendingAssistant = null;
  };

  for (const rawMessage of messages || []) {
    const normalizedMessage = normalizeMessageForDisplay(rawMessage);

    if (normalizedMessage.role === 'user') {
      flushPendingAssistant();
      normalizedMessages.push(normalizedMessage);
      continue;
    }

    if (normalizedMessage.role === 'tool') {
      continue;
    }

    if (normalizedMessage.role !== 'assistant') {
      flushPendingAssistant();
      if (hasDisplayContent(normalizedMessage)) {
        normalizedMessages.push(normalizedMessage);
      }
      continue;
    }

    const hasToolContext =
      (Array.isArray(normalizedMessage.toolCalls) && normalizedMessage.toolCalls.length > 0) ||
      (Array.isArray(normalizedMessage.progressLines) && normalizedMessage.progressLines.length > 0);
    const hasTextualContent = Boolean(
      String(normalizedMessage.content ?? '').trim() ||
      String(normalizedMessage.reasoningContent ?? '').trim()
    );

    if (pendingAssistant) {
      pendingAssistant = mergeAssistantDisplayMessage(pendingAssistant, normalizedMessage);
      if (hasTextualContent) {
        flushPendingAssistant();
      }
      continue;
    }

    if (hasToolContext && !hasTextualContent) {
      pendingAssistant = normalizedMessage;
      continue;
    }

    if (hasDisplayContent(normalizedMessage)) {
      normalizedMessages.push(normalizedMessage);
    }
  }

  flushPendingAssistant();
  return normalizedMessages;
};

const mergeToolCallDelta = (existingToolCalls, deltaToolCalls) => {
  const merged = Array.isArray(existingToolCalls)
    ? existingToolCalls.map((item) => normalizeToolCall(item))
    : [];

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

const appendProgressEntries = (message, entries) => {
  const nextEntries = Array.isArray(message.progressLines) ? [...message.progressLines] : [];
  for (const entry of entries || []) {
    if (!entry) continue;
    if (nextEntries[nextEntries.length - 1] === entry) continue;
    nextEntries.push(entry);
  }
  message.progressLines = nextEntries;
};

const createMetaSection = (title, bodyHtml, className = '', open = true) => {
  const section = document.createElement('details');
  section.className = `message-meta ${className}`.trim();
  section.open = open;

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
      createMetaSection('Reasoning', renderMarkdown(message.reasoningContent), 'message-meta-reasoning')
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

    container.append(createMetaSection('Tool Calls', items, 'message-meta-tools'));
  }

  if (Array.isArray(message?.progressLines) && message.progressLines.length > 0) {
    const lines = message.progressLines
      .map((line) => `<div class="progress-line">${escapeHtml(line)}</div>`)
      .join('');
    container.append(
      createMetaSection(
        'Execution Progress',
        lines,
        'message-meta-progress',
        !Boolean(message?.done)
      )
    );
  }
};

const getAttachmentKindLabel = (file) => {
  if ((file?.content_type || '').startsWith('image/')) return 'Image';
  return 'Attachment';
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
    name.textContent = file?.name || 'Attachment';

    const meta = document.createElement('div');
    meta.className = 'message-file-meta';
    const pieces = [getAttachmentKindLabel(file)];
    if (file?.size) {
      pieces.push(formatFileSize(file.size));
    }
    if (file?.localPath) {
      pieces.push(file.localPath);
    }
    meta.textContent = pieces.join(' · ');

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

const getRenderableMessages = () => state.messages.filter((message) => message.role !== 'tool');

const renderMessages = () => {
  dom.messages.innerHTML = '';
  const visibleMessages = getRenderableMessages();

  if (visibleMessages.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent = 'Start a new conversation.';
    dom.messages.append(empty);
    return;
  }

  for (const message of visibleMessages) {
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
    role.textContent = message.role === 'user' ? 'You' : 'Hermes';
    renderMessageMetaSections(metaSections, message);
    renderMessageFiles(fileSection, message);
    content.classList.remove('streaming-placeholder');

    const hasVisibleContent = hasDisplayContent(message);

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
      } catch {
        showChatError('Copy failed. Please try again.');
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

const getChatDisplayTitle = (chat) => {
  if (!chat) return 'New chat';
  if (chat.isDraft && state.messages.length > 0) {
    return deriveTitleFromMessages(state.messages);
  }
  return chat.title || chat.preview || 'New chat';
};

const renderChatList = () => {
  dom.chatList.innerHTML = '';
  const chats = getCurrentChatEntries();

  if (chats.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent = 'No chats yet.';
    dom.chatList.append(empty);
    return;
  }

  for (const chat of chats) {
    const fragment = dom.chatItemTemplate.content.cloneNode(true);
    const button = fragment.querySelector('.chat-item');
    const title = fragment.querySelector('.chat-item-title');
    const meta = fragment.querySelector('.chat-item-meta');
    const deleteButton = fragment.querySelector('.chat-delete-button');

    title.textContent = getChatDisplayTitle(chat);
    meta.textContent = formatTimestamp(chat.last_active || chat.started_at);

    if (chat.id === state.activeSessionId) {
      button.classList.add('active');
    }

    button.addEventListener('click', () => {
      if (chat.isDraft) {
        state.activeSession = chat;
        state.activeSessionId = chat.id;
        renderWorkspace();
        return;
      }
      openSession(chat.id).catch((error) => showChatError(error.message));
    });

    deleteButton.addEventListener('click', async (event) => {
      event.stopPropagation();
      const confirmed = window.confirm(`Delete chat "${getChatDisplayTitle(chat)}"?`);
      if (!confirmed) return;
      await deleteChat(chat.id, chat.isDraft).catch((error) => showChatError(error.message));
    });

    dom.chatList.append(fragment);
  }
};

const renderWorkspaceHeader = () => {
  dom.userEmail.textContent = state.user?.email || '';
  dom.chatTitle.textContent = getActiveChatTitle();
  dom.modelName.textContent = state.selectedModel
    ? `Model: ${state.selectedModel.name || state.selectedModel.id}`
    : 'No model selected';
};

const renderFileBrowserControls = () => {
  if (!dom.fileOpenControls) return;
  dom.fileOpenControls.hidden = state.fileBrowserMode !== 'user_readable';
  if (state.fileBrowserMode === 'user_readable' && dom.filePathInput && !dom.filePathInput.value.trim()) {
    dom.filePathInput.value = state.homePath || state.workspaceRoot || state.user?.workspace_root || '~';
  }
};

const renderWorkspace = () => {
  renderChatList();
  renderWorkspaceHeader();
  renderMessages();
  renderAttachments();
  renderApprovalModal();
  renderFileBrowserControls();
};

const normalizeDirectory = (path) => {
  const normalized = String(path || '/').replace(/\\/g, '/');
  return normalized.endsWith('/') ? normalized : `${normalized}/`;
};

const getDisplayDirectoryPath = () => {
  const workspaceRoot = String(state.workspaceRoot || state.user?.workspace_root || '').trim();
  if (!workspaceRoot) {
    return state.rootPath || state.currentPath || '/';
  }
  return workspaceRoot;
};

const setFileTreeRoot = async ({ root, path = '', entries = null }) => {
  state.workspaceRoot = String(root || state.workspaceRoot || state.user?.workspace_root || '').trim();
  state.rootPath = normalizeDirectory(path || '/');
  state.currentPath = state.rootPath;
  state.expandedPaths = new Set([state.rootPath]);
  state.treeCache.clear();
  if (Array.isArray(entries)) {
    state.treeCache.set(state.rootPath, entries);
  } else {
    await listDirectory(state.rootPath, true);
  }
  await renderFileTree();
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
  const query = new URLSearchParams();
  query.set('path', relativePath);
  if (state.workspaceRoot) {
    query.set('root', state.workspaceRoot);
  }
  const json = await api(`/api/files/tree?${query.toString()}`, { method: 'GET' }).then((res) => res.json());
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
  const query = new URLSearchParams();
  query.set('path', relativePath);
  if (state.workspaceRoot) {
    query.set('root', state.workspaceRoot);
  }
  const response = await api(`/api/files/download?${query.toString()}`, { method: 'GET' });
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
          renderFileTree();
        } else {
          try {
            await listDirectory(nodePath);
            state.expandedPaths.add(nodePath);
            renderFileTree();
          } catch (error) {
            showChatError(error.message);
          }
        }
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
        try {
          await listDirectory(nodePath);
          state.currentPath = normalizeDirectory(nodePath);
          state.expandedPaths.add(normalizeDirectory(nodePath));
          renderFileTree();
        } catch (error) {
          showChatError(error.message);
        }
        return;
      }
      downloadFile(nodePath).catch((error) => showChatError(error.message));
    });

    row.append(toggleOrSpacer, label);

    if (entry.type === 'file') {
      const download = document.createElement('button');
      download.type = 'button';
      download.className = 'tree-download';
      download.textContent = '↓';
      download.title = 'Download';
      download.addEventListener('click', () => {
        downloadFile(nodePath).catch((error) => showChatError(error.message));
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
    dom.fileTree.innerHTML = '<div class="empty-state">No workspace directory is available for this user.</div>';
    dom.cwdLabel.textContent = '';
    return;
  }

  dom.cwdLabel.textContent = getDisplayDirectoryPath();
  const root = normalizeDirectory(state.rootPath || state.currentPath);
  try {
    const tree = await renderTreeNode(root);
    if (!tree.children.length) {
      dom.fileTree.innerHTML = '<div class="empty-state">This directory is empty.</div>';
      return;
    }
    dom.fileTree.append(tree);
  } catch (error) {
    dom.fileTree.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  }
};

const fetchWorkspaceFiles = async () => {
  const [configJson, treeJson] = await Promise.all([
    api('/api/files/config', { method: 'GET' }).then((res) => res.json()),
    api('/api/files/tree', { method: 'GET' }).then((res) => res.json()),
  ]);
  state.fileBrowserMode = String(configJson?.mode || 'home_only');
  state.homePath = String(configJson?.home || treeJson?.root || state.user?.workspace_root || '').trim();
  renderFileBrowserControls();
  await setFileTreeRoot({
    root: treeJson?.root || state.user?.workspace_root || '/',
    path: treeJson?.path || '/',
    entries: Array.isArray(treeJson?.entries) ? treeJson.entries : [],
  });
};

const openDirectory = async (rawPath) => {
  const requestedPath = String(rawPath || '').trim();
  if (!requestedPath) {
    showChatError('Enter a directory path to open.');
    return;
  }
  const json = await api(`/api/files/open?path=${encodeURIComponent(requestedPath)}`, { method: 'GET' }).then((res) => res.json());
  await setFileTreeRoot({
    root: json?.root || requestedPath,
    path: json?.path || '/',
    entries: Array.isArray(json?.entries) ? json.entries : [],
  });
  if (dom.filePathInput) {
    dom.filePathInput.value = String(json?.opened_path || requestedPath);
  }
};

const fetchModels = async () => {
  const response = await api('/api/models', { method: 'GET' });
  const json = await response.json();
  state.models = Array.isArray(json?.data) ? json.data : [];
  state.selectedModel = state.models[0] || null;
  renderWorkspaceHeader();
};

const refreshSessions = async () => {
  const response = await api('/api/sessions', { method: 'GET' });
  const json = await response.json();
  const sessions = Array.isArray(json?.sessions) ? json.sessions : [];
  sessions.sort((left, right) => (right.last_active || right.started_at || 0) - (left.last_active || left.started_at || 0));
  state.sessions = sessions;
  renderChatList();
  renderWorkspaceHeader();
};

const openSession = async (sessionId) => {
  if (!sessionId) {
    state.activeSession = state.draftSession;
    state.activeSessionId = state.draftSession?.id || null;
    state.messages = [];
    renderWorkspace();
    return;
  }

  const response = await api(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'GET' });
  const json = await response.json();
  state.draftSession = null;
  state.activeSession = json?.session || null;
  state.activeSessionId = state.activeSession?.id || null;
  state.messages = Array.isArray(json?.messages)
    ? json.messages.map(normalizeMessageForDisplay)
    : [];
  renderWorkspace();
};

const syncActiveSessionFromSessions = (sessionId) => {
  if (!sessionId) return;

  const matchedSession = state.sessions.find((session) => session.id === sessionId);
  if (matchedSession) {
    state.draftSession = null;
    state.activeSession = matchedSession;
    state.activeSessionId = matchedSession.id;
    renderWorkspaceHeader();
    return;
  }

  if (state.activeSession?.isDraft || state.activeSessionId === sessionId) {
    state.draftSession = null;
    state.activeSession = {
      ...(state.activeSession || {}),
      id: sessionId,
      title: getActiveChatTitle(),
      isDraft: false,
    };
    state.activeSessionId = sessionId;
    renderWorkspaceHeader();
  }
};

const deleteChat = async (chatId, isDraft = false) => {
  if (isDraft) {
    state.draftSession = null;
    state.activeSession = null;
    state.activeSessionId = null;
    state.messages = [];

    if (state.sessions.length > 0) {
      await openSession(state.sessions[0].id);
      return;
    }

    startNewChat();
    return;
  }

  await api(`/api/sessions/${encodeURIComponent(chatId)}`, { method: 'DELETE' });
  state.sessions = state.sessions.filter((chat) => chat.id !== chatId);

  if (chatId !== state.activeSessionId) {
    renderChatList();
    return;
  }

  state.activeSession = null;
  state.activeSessionId = null;
  state.messages = [];
  if (state.sessions.length > 0) {
    await openSession(state.sessions[0].id);
    return;
  }
  startNewChat();
};

const startNewChat = () => {
  state.pendingAttachments = [];
  renderAttachments();
  state.draftSession = createDraftSession();
  state.activeSession = state.draftSession;
  state.activeSessionId = state.draftSession.id;
  state.messages = [];
  renderWorkspace();
  dom.promptInput?.focus();
};

const streamChatCompletion = async (payload, assistantMessage, abortController, streamState) => {
  const response = await fetch('/api/chat/completions', {
    method: 'POST',
    credentials: 'include',
    signal: abortController.signal,
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let detail = `Chat failed: ${response.status}`;
    try {
      const json = await response.json();
      detail = json?.detail || json?.error?.message || detail;
    } catch {}
    throw new Error(detail);
  }

  streamState.sessionId = response.headers.get('X-Hermes-Session-Id') || streamState.sessionId || payload.session_id || '';

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

      if (eventName === 'hermes.approval.required') {
        state.pendingApproval = {
          approvalId: String(json?.approval_id || ''),
          sessionId: String(json?.session_id || streamState.sessionId || ''),
          command: String(json?.command || ''),
          description: String(json?.description || 'Dangerous command requires approval.'),
          patternKey: String(json?.pattern_key || ''),
          patternKeys: Array.isArray(json?.pattern_keys) ? json.pattern_keys.map((item) => String(item)) : [],
          options: Array.isArray(json?.options) ? json.options.map((item) => String(item)) : [],
        };
        renderApprovalModal();
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
        const progressLines = extractDisplayProgressLines(delta.content);
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
  const oversizedAttachment = state.pendingAttachments.find((item) => Number(item?.size || 0) > MAX_ATTACHMENT_SIZE_BYTES);

  if (!trimmedPrompt && uploadedAttachments.length === 0) return;
  if (!state.selectedModel) {
    showChatError('No model is currently available.');
    return;
  }
  if (hasUploadingFiles) {
    showChatError('Files are still uploading. Please wait before sending.');
    return;
  }
  if (hasFailedUploads) {
    showChatError('Some attachments failed to upload. Remove them and try again.');
    return;
  }
  if (oversizedAttachment) {
    showChatError(getAttachmentTooLargeMessage(oversizedAttachment));
    return;
  }

  if (!state.activeSession) {
    startNewChat();
  }

  showChatError('');
  const abortController = new AbortController();
  const streamState = {
    sessionId: state.activeSession?.isDraft ? '' : (state.activeSession?.id || ''),
  };
  state.currentAbortController = abortController;
  setComposerBusyState(true);

  const userMessage = {
    id: uuid(),
    role: 'user',
    content: trimmedPrompt,
    files: uploadedAttachments.map((item) => ({
      type: item.type,
      id: item.id,
      name: item.name,
      size: item.size,
      content_type: item.content_type,
      localPath: item.localPath,
    })),
    timestamp: nowSeconds(),
    done: true,
    reasoningContent: '',
    toolCalls: [],
    progressLines: [],
  };

  const assistantMessage = {
    id: uuid(),
    role: 'assistant',
    content: '',
    reasoningContent: '',
    toolCalls: [],
    progressLines: [],
    timestamp: nowSeconds(),
    done: false,
    files: [],
  };

  state.messages = [...state.messages, userMessage, assistantMessage];
  state.streamingMessageIds.add(assistantMessage.id);
  state.pendingAttachments = [];

  if (state.draftSession) {
    state.draftSession.title = deriveTitleFromMessages(state.messages);
    state.draftSession.last_active = nowSeconds();
  }

  renderWorkspace();

  const payload = {
    stream: true,
    model: state.selectedModel.id,
    session_id: streamState.sessionId,
    messages: [
      {
        role: 'user',
        content: buildHermesUserContent(trimmedPrompt, uploadedAttachments),
      },
    ],
  };

  try {
    await streamChatCompletion(payload, assistantMessage, abortController, streamState);
    assistantMessage.done = true;
    assistantMessage.timestamp = nowSeconds();
    state.streamingMessageIds.delete(assistantMessage.id);
    renderMessages();

    await refreshSessions();
    syncActiveSessionFromSessions(streamState.sessionId);
    renderWorkspace();
  } catch (error) {
    state.streamingMessageIds.delete(assistantMessage.id);
    assistantMessage.done = true;
    assistantMessage.timestamp = nowSeconds();
    clearPendingApproval();

    if (error.name === 'AbortError') {
      if (!assistantMessage.content.trim()) {
        assistantMessage.content = '[Response stopped]';
      }
      renderMessages();
      if (streamState.sessionId) {
        await refreshSessions().catch(() => {});
        syncActiveSessionFromSessions(streamState.sessionId);
        renderWorkspace();
      }
    } else {
      assistantMessage.content = `${assistantMessage.content}\n\n[Error] ${error.message}`.trim();
      renderMessages();
      showChatError(error.message);
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
  setAuthViewMode('signin');
};

const initializeWorkspaceData = async () => {
  let firstError = null;

  try {
    await fetchModels();
  } catch (error) {
    firstError = firstError || error;
  }

  try {
    await refreshSessions();
    if (state.sessions.length > 0) {
      await openSession(state.sessions[0].id);
    } else {
      startNewChat();
    }
  } catch (error) {
    firstError = firstError || error;
  }

  try {
    await fetchWorkspaceFiles();
  } catch (error) {
    firstError = firstError || error;
  }

  if (firstError) {
    showChatError(firstError.message || 'Workspace initialization failed');
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
  bootstrapInFlight = true;
  try {
    const response = await fetch('/api/auth/session', {
      method: 'GET',
      credentials: 'include',
    });
    const json = await response.json();
    if (!json?.authenticated || !json?.user) {
      throw new Error('Not authenticated');
    }
    await startWorkspaceRuntime(json.user, { allowRetry: true, source: 'restore' });
    if (state.user) {
      return;
    }
  } catch {
    stopAuthPolling();
    state.user = null;
    state.pendingWorkspaceUser = null;
    resetWorkspaceState();
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
    const response = await api('/api/auth/signin', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    const user = await response.json();
    await startWorkspaceRuntime(user, { allowRetry: true, source: 'signin' });
  } catch (error) {
    const message = String(error.message || 'Sign-in failed');
    showError(
      dom.loginError,
      message.includes('429') ? 'Too many sign-in attempts. Please wait a few seconds and try again.' : message
    );
  } finally {
    setLoginPending(false);
  }
});

dom.registerForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (signupInFlight) return;
  showError(dom.registerError, '');

  const username = document.getElementById('register-username').value.trim();
  const displayName = document.getElementById('register-display-name').value.trim();
  const email = document.getElementById('register-email').value.trim();
  const password = document.getElementById('register-password').value;
  const passwordConfirm = document.getElementById('register-password-confirm').value;

  if (password !== passwordConfirm) {
    showError(dom.registerError, 'Passwords do not match.');
    return;
  }

  try {
    setSignupPending(true);
    const response = await api('/api/auth/signup', {
      method: 'POST',
      body: JSON.stringify({
        username,
        display_name: displayName,
        email,
        password,
      }),
    });
    const json = await response.json();
    state.signupJobId = json?.job_id || null;
    setAuthViewMode('signup-wait');
    applySignupJobState({ status: json?.status || 'pending' });
    stopSignupPolling();
    pollSignupJob();
  } catch (error) {
    showError(dom.registerError, String(error.message || 'Registration failed'));
  } finally {
    setSignupPending(false);
  }
});

dom.showRegisterButton.addEventListener('click', () => {
  setAuthViewMode('register');
});

dom.showLoginButton.addEventListener('click', () => {
  setAuthViewMode('signin');
});

dom.signupGoLoginButton.addEventListener('click', () => {
  state.signupJobId = null;
  stopSignupPolling();
  setAuthViewMode('signin');
});

dom.signupBackButton.addEventListener('click', () => {
  state.signupJobId = null;
  stopSignupPolling();
  setAuthViewMode('register');
});

dom.logoutButton.addEventListener('click', async () => {
  state.pendingAttachments = [];
  stopAuthPolling();
  if (state.chatErrorTimer) {
    window.clearTimeout(state.chatErrorTimer);
    state.chatErrorTimer = null;
  }
  showChatError('');
  try {
    await api('/api/auth/signout', { method: 'POST' });
  } catch {
    // Ignore logout transport failures and still clear the UI state.
  }
  state.user = null;
  state.pendingWorkspaceUser = null;
  resetWorkspaceState();
  showLogin();
});

dom.approvalAllowOnce?.addEventListener('click', () => {
  submitApprovalDecision('once');
});

dom.approvalAllowSession?.addEventListener('click', () => {
  submitApprovalDecision('session');
});

dom.approvalAllowAlways?.addEventListener('click', () => {
  submitApprovalDecision('always');
});

dom.approvalDeny?.addEventListener('click', () => {
  submitApprovalDecision('deny');
});

dom.runtimeStartRetryButton.addEventListener('click', async () => {
  if (loginInFlight || bootstrapInFlight) return;
  const pendingUser = state.pendingWorkspaceUser;
  if (!pendingUser) {
    showLogin();
    return;
  }

  try {
    setLoginPending(true);
    await startWorkspaceRuntime(pendingUser, { allowRetry: true, source: 'retry' });
  } catch {
    // Error already rendered inside startWorkspaceRuntime.
  } finally {
    setLoginPending(false);
  }
});

dom.runtimeStartBackButton.addEventListener('click', async () => {
  stopAuthPolling();
  try {
    await api('/api/auth/signout', { method: 'POST' });
  } catch {
    // Ignore logout transport failures and still clear UI state.
  }
  state.user = null;
  state.pendingWorkspaceUser = null;
  resetWorkspaceState();
  showLogin();
});

dom.newChatButton.addEventListener('click', () => {
  startNewChat();
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

dom.promptInput.addEventListener('paste', async (event) => {
  if (state.isSending) return;

  const files = normalizeClipboardFiles(event.clipboardData);
  if (!files.length) return;

  event.preventDefault();
  await handleSelectedFiles(files);
});

dom.sendButton.addEventListener('click', async (event) => {
  if (!state.isSending) return;
  event.preventDefault();
  event.stopPropagation();
  state.currentAbortController?.abort();
});

dom.refreshFilesButton.addEventListener('click', async () => {
  state.treeCache.clear();
  await fetchWorkspaceFiles().catch((error) => showChatError(error.message));
});

dom.fileOpenButton?.addEventListener('click', async () => {
  await openDirectory(dom.filePathInput?.value || '').catch((error) => showChatError(error.message));
});

dom.fileHomeButton?.addEventListener('click', async () => {
  const homePath = state.homePath || state.user?.workspace_root || '~';
  await openDirectory(homePath).catch((error) => showChatError(error.message));
});

dom.filePathInput?.addEventListener('keydown', async (event) => {
  if (event.key !== 'Enter' || event.isComposing) return;
  event.preventDefault();
  await openDirectory(dom.filePathInput?.value || '').catch((error) => showChatError(error.message));
});

initResizablePanels();
initThemeControls();
autoResizePromptInput();
bootstrapSession();
