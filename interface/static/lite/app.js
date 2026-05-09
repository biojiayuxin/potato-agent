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
  chatErrorTimer: null,
  authPollTimer: null,
  signupJobId: null,
  signupPollTimer: null,
  pendingApproval: null,
  approvalSubmitting: false,
  pendingSessionPromise: null,
  shouldAutoScrollMessages: true,
  liveSessionMessages: new Map(),
  sessionHistoryLoading: false,
  renamingSessionId: null,
  renamingTitleDraft: '',
  renamingTitleError: '',
};

const MODEL_RESPONSE_ERROR_MESSAGE = '模型响应失败，请稍后重试。';

const dom = {
  loginView: document.getElementById('login-view'),
  workspaceView: document.getElementById('workspace-view'),
  authHomeView: document.getElementById('auth-home-view'),
  authPanelHeader: document.getElementById('auth-panel-header'),
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
  authBackButton: document.getElementById('auth-back-button'),
  switchRegisterButton: document.getElementById('switch-register-button'),
  registerBackButton: document.getElementById('register-back-button'),
  switchLoginButton: document.getElementById('switch-login-button'),
  signinNavActions: document.getElementById('signin-nav-actions'),
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
  tuiBridgeStatus: document.getElementById('tui-bridge-status'),
  chatTitle: document.getElementById('chat-title'),
  modelName: document.getElementById('model-name'),
  modelSelect: document.getElementById('model-select'),
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
let tuiBridge = null;
let tuiBridgeConnectPromise = null;
let tuiBridgeConnectingSocket = null;
let tuiBridgeRequestCounter = 0;
const tuiBridgePending = new Map();
let activeTuiSessionId = '';
let activePersistentSessionId = '';
const liveTuiSessionsByPersistentId = new Map();
const liveTuiSessionAliasesByPersistentId = new Map();
const persistentIdsByLiveTuiSessionId = new Map();
const busySessionIds = new Set();
const sessionRunTransportById = new Map();
const sessionAbortControllersById = new Map();
const pendingApprovalsBySessionId = new Map();
const interruptingSessionIds = new Set();
const recoveringTuiSessionIds = new Set();
const tuiBridgeReconnectTimersBySessionId = new Map();
const tuiBridgeReconnectAttemptsBySessionId = new Map();
const intentionallyClosedTuiBridges = new WeakSet();

const SIDEBAR_WIDTH_KEY = 'lite_sidebar_width';
const FILES_WIDTH_KEY = 'lite_files_width';
const THEME_MODE_KEY = 'lite_theme_mode';
const MAX_ATTACHMENT_SIZE_BYTES = 20 * 1024 * 1024;
const AUTH_POLL_INTERVAL_MS = 60 * 1000;
const TUI_BRIDGE_RECONNECT_BASE_DELAY_MS = 1000;
const TUI_BRIDGE_RECONNECT_MAX_DELAY_MS = 15000;
const ATTACHMENT_BLOCK_START = '<potato-files>';
const ATTACHMENT_BLOCK_END = '</potato-files>';
const ATTACHMENT_HINT_LINE = 'Use the attachment local paths above if you need to inspect the files.';
const ICON_SEND_PATH = './static/lite/icons/send.png';
const ICON_STOP_PATH = './static/lite/icons/stop.png';
const ICON_COPY_PATH = './static/lite/icons/copy_button.png';
const ICON_COPIED_PATH = './static/lite/icons/copied.png';
const MESSAGE_AUTO_SCROLL_THRESHOLD_PX = 48;
const TUI_DEBUG_STATUS_STORAGE_KEY = 'lite_tui_bridge_debug';

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

const isTuiBridgeDebugStatusEnabled = () => localStorage.getItem(TUI_DEBUG_STATUS_STORAGE_KEY) === '1';

const setTuiBridgeStatus = (message = '', { hidden = false } = {}) => {
  if (!dom.tuiBridgeStatus) return;
  if (!isTuiBridgeDebugStatusEnabled()) {
    dom.tuiBridgeStatus.hidden = true;
    dom.tuiBridgeStatus.textContent = '';
    return;
  }
  if (hidden || !message) {
    dom.tuiBridgeStatus.hidden = true;
    dom.tuiBridgeStatus.textContent = '';
    return;
  }
  dom.tuiBridgeStatus.hidden = false;
  dom.tuiBridgeStatus.textContent = message;
};

const isDefaultChatTitle = (title) => {
  const normalized = String(title || '').trim();
  return !normalized || normalized === 'New chat';
};

const deriveDraftTitleFromUserMessage = ({ content = '', files = [] } = {}) => {
  const text = String(content || '').trim();
  if (text) {
    return text.slice(0, 10) || 'New chat';
  }

  const firstFile = Array.isArray(files) ? files[0] : null;
  if (firstFile?.name) {
    return String(firstFile.name).slice(0, 10) || 'New chat';
  }

  return 'New chat';
};

const getPersistentSessionIdForChat = (chat) => {
  if (!chat) return '';
  if (chat.isDraft) {
    return String(chat.persistentSessionId || '').trim();
  }
  return String(chat.persistentSessionId || chat.id || '').trim();
};

const getResumeSessionIdForChat = (chat) => {
  if (!chat || chat.isDraft) return '';
  return String(chat.resume_session_id || chat.resumeSessionId || chat.id || '').trim();
};

const getActivePersistentSessionId = () => {
  return String(getPersistentSessionIdForChat(state.activeSession) || state.activeSessionId || '').trim();
};

const getSessionTitleById = (sessionId) => {
  const normalizedSessionId = String(sessionId || '').trim();
  if (!normalizedSessionId) return '';
  if (state.activeSessionId === normalizedSessionId) {
    return String(state.activeSession?.title || '').trim();
  }
  const session = state.sessions.find((chat) => chat.id === normalizedSessionId);
  return String(session?.title || '').trim();
};

const resetSessionRenameState = () => {
  state.renamingSessionId = null;
  state.renamingTitleDraft = '';
  state.renamingTitleError = '';
};

const startSessionRename = (sessionId) => {
  const normalizedSessionId = String(sessionId || '').trim();
  if (!normalizedSessionId) return;
  state.renamingSessionId = normalizedSessionId;
  state.renamingTitleDraft = getSessionTitleById(normalizedSessionId) || 'New chat';
  state.renamingTitleError = '';
};

const setSessionRenameDraft = (value) => {
  state.renamingTitleDraft = String(value || '');
  state.renamingTitleError = '';
};

const validateSessionTitleDraft = (value) => {
  const trimmed = String(value || '').trim();
  if (!trimmed) {
    return 'Title cannot be empty.';
  }
  if (trimmed.length > 100) {
    return 'Title must be 100 characters or fewer.';
  }
  return '';
};

const isSessionBusy = (sessionId) => {
  const normalizedSessionId = String(sessionId || '').trim();
  return Boolean(normalizedSessionId) && busySessionIds.has(normalizedSessionId);
};

const sessionNeedsApproval = (sessionId) => {
  const normalizedSessionId = String(sessionId || '').trim();
  return Boolean(normalizedSessionId) && pendingApprovalsBySessionId.has(normalizedSessionId);
};

const getActiveSessionPendingApproval = () => {
  const activeSessionId = getActivePersistentSessionId();
  if (!activeSessionId) return null;
  return pendingApprovalsBySessionId.get(activeSessionId) || null;
};

const syncActiveSessionUiState = () => {
  state.pendingApproval = getActiveSessionPendingApproval();
  state.isSending = isSessionBusy(getActivePersistentSessionId());
};

const isAnySessionBusy = () => busySessionIds.size > 0 || pendingApprovalsBySessionId.size > 0;

const getModelDisplayName = (model) => {
  if (!model) return '';
  return String(model.name || model.model || model.id || '').trim();
};

const normalizeSessionSnapshot = (session) => {
  if (!session?.id) return null;

  const sessionId = String(session.id || '').trim();
  if (!sessionId) return null;

  const existingSession = state.sessions.find((chat) => chat.id === sessionId)
    || (state.activeSessionId === sessionId ? state.activeSession : null);

  const normalized = {
    ...(existingSession || {}),
    ...session,
    id: sessionId,
    persistentSessionId: String(session.persistentSessionId || existingSession?.persistentSessionId || sessionId),
    resume_session_id: String(
      session.resume_session_id
      || session.resumeSessionId
      || session.live?.tip_session_id
      || session.live?.tipSessionId
      || existingSession?.resume_session_id
      || existingSession?.resumeSessionId
      || sessionId
    ).trim(),
  };

  if (isDefaultChatTitle(normalized.title)) {
    const fallbackTitle = String(existingSession?.title || '').trim();
    if (fallbackTitle && !isDefaultChatTitle(fallbackTitle)) {
      normalized.title = fallbackTitle;
    }
  }

  return normalized;
};

const setLocalSessionTitle = (sessionId, title) => {
  const targetSessionId = String(sessionId || '').trim();
  const nextTitle = String(title || '').trim();
  if (!targetSessionId || !nextTitle || isDefaultChatTitle(nextTitle)) return;

  state.sessions = state.sessions.map((chat) => (
    chat.id === targetSessionId
      ? { ...chat, title: nextTitle, last_active: nowSeconds() }
      : chat
  ));

  if (state.activeSessionId === targetSessionId && state.activeSession) {
    state.activeSession = {
      ...state.activeSession,
      title: nextTitle,
      last_active: nowSeconds(),
      persistentSessionId: getPersistentSessionIdForChat(state.activeSession) || targetSessionId,
    };
  }
};

const applySessionSnapshot = (session) => {
  const normalizedSession = normalizeSessionSnapshot(session);
  if (!normalizedSession?.id) return null;

  let replaced = false;
  state.sessions = state.sessions.map((chat) => {
    if (chat.id !== normalizedSession.id) return chat;
    replaced = true;
    return normalizedSession;
  });
  if (!replaced) {
    state.sessions = [normalizedSession, ...state.sessions];
  }
  if (state.activeSessionId === normalizedSession.id) {
    state.activeSession = normalizedSession;
  }
  return normalizedSession;
};

const persistSessionDisplayState = async (sessionId, messages, { draftTitle = '' } = {}) => {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId) return null;

  const payloadMessages = normalizeSessionMessagesForDisplay(messages).map((message) => ({
    id: String(message?.id || ''),
    role: String(message?.role || 'assistant'),
    content: String(message?.content || ''),
    reasoningContent: String(message?.reasoningContent || ''),
    toolCalls: Array.isArray(message?.toolCalls)
      ? message.toolCalls.map((item) => normalizeToolCall(item))
      : [],
    progressLines: Array.isArray(message?.progressLines) ? [...message.progressLines] : [],
    files: Array.isArray(message?.files) ? [...message.files] : [],
    timestamp: Number(message?.timestamp || 0),
    done: Boolean(message?.done ?? true),
    source: String(message?.source || 'display_store'),
  }));

  const response = await api(`/api/sessions/${encodeURIComponent(persistentSessionId)}/display`, {
    method: 'PUT',
    body: JSON.stringify({
      messages: payloadMessages,
      draft_title: String(draftTitle || '').trim(),
    }),
  });
  return response.json();
};

const rememberLiveTuiSession = (persistentSessionId, liveSessionId, { primary = true } = {}) => {
  const persistentId = String(persistentSessionId || '').trim();
  const liveId = String(liveSessionId || '').trim();
  if (!persistentId || !liveId) return;
  if (primary) {
    liveTuiSessionsByPersistentId.set(persistentId, liveId);
  } else if (!liveTuiSessionsByPersistentId.has(persistentId)) {
    liveTuiSessionsByPersistentId.set(persistentId, liveId);
  }
  const aliases = liveTuiSessionAliasesByPersistentId.get(persistentId) || new Set();
  aliases.add(liveId);
  liveTuiSessionAliasesByPersistentId.set(persistentId, aliases);
  persistentIdsByLiveTuiSessionId.set(liveId, persistentId);
};

const ACTIVE_LIVE_SESSION_STATUSES = new Set(['queued', 'starting', 'running', 'awaiting_approval']);

const normalizeDisplayMessageId = (messageId) => {
  const normalized = String(messageId || '').trim();
  if (!normalized) return '';
  return normalized.startsWith('msg-') ? normalized : `msg-${normalized}`;
};

const forgetLiveTuiSession = (persistentSessionId) => {
  const persistentId = String(persistentSessionId || '').trim();
  if (!persistentId) return;
  const liveIds = new Set(liveTuiSessionAliasesByPersistentId.get(persistentId) || []);
  const primaryLiveId = liveTuiSessionsByPersistentId.get(persistentId) || '';
  if (primaryLiveId) {
    liveIds.add(primaryLiveId);
  }
  liveTuiSessionsByPersistentId.delete(persistentId);
  liveTuiSessionAliasesByPersistentId.delete(persistentId);
  for (const liveId of liveIds) {
    persistentIdsByLiveTuiSessionId.delete(liveId);
  }
};

const getPersistentSessionIdForLiveTuiSession = (liveSessionId) => {
  const liveId = String(liveSessionId || '').trim();
  if (!liveId) return '';
  return String(persistentIdsByLiveTuiSessionId.get(liveId) || '');
};

const syncStreamingAssistantStateForSession = (
  sessionId,
  {
    liveState = null,
    nextMessages = [],
    previousMessages = [],
  } = {},
) => {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId) return;

  const relatedMessages = [
    ...(Array.isArray(previousMessages) ? previousMessages : []),
    ...(Array.isArray(nextMessages) ? nextMessages : []),
  ];
  for (const message of relatedMessages) {
    if (String(message?.role || '') !== 'assistant') continue;
    const messageId = String(message?.id || '').trim();
    if (!messageId) continue;
    state.streamingMessageIds.delete(messageId);
  }

  const normalizedLiveState = liveState && typeof liveState === 'object' ? liveState : null;
  const status = String(normalizedLiveState?.status || '').trim();
  if (!ACTIVE_LIVE_SESSION_STATUSES.has(status)) {
    return;
  }

  const assistantMessageId = normalizeDisplayMessageId(
    normalizedLiveState?.assistant_message_id || normalizedLiveState?.assistantMessageId || ''
  );
  if (!assistantMessageId) return;

  const targetMessages = Array.isArray(nextMessages) ? nextMessages : [];
  const hasTargetAssistant = targetMessages.some((message) => (
    String(message?.role || '') === 'assistant' && String(message?.id || '').trim() === assistantMessageId
  ));
  if (hasTargetAssistant) {
    state.streamingMessageIds.add(assistantMessageId);
  }
};

const applyLiveStateToSession = (sessionId, live) => {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId) return;

  const liveState = live && typeof live === 'object' ? live : null;
  const liveSessionId = String(liveState?.live_session_id || liveState?.liveSessionId || '').trim();
  const tipSessionId = String(liveState?.tip_session_id || liveState?.tipSessionId || '').trim();
  const status = String(liveState?.status || '').trim();
  const pendingApproval = liveState?.pending_approval || liveState?.pendingApproval || null;

  const applyLiveSnapshot = (session) => (
    session
      ? {
          ...session,
          live: liveState,
          resume_session_id: tipSessionId || session.resume_session_id || session.resumeSessionId || session.id,
          resumeSessionId: tipSessionId || session.resumeSessionId || session.resume_session_id || session.id,
        }
      : session
  );

  if (ACTIVE_LIVE_SESSION_STATUSES.has(status) && liveSessionId) {
    rememberLiveTuiSession(persistentSessionId, liveSessionId);
    if (tipSessionId && tipSessionId !== liveSessionId) {
      rememberLiveTuiSession(persistentSessionId, tipSessionId, { primary: false });
    }
  } else if (tipSessionId && ACTIVE_LIVE_SESSION_STATUSES.has(status)) {
    rememberLiveTuiSession(persistentSessionId, tipSessionId);
  } else {
    forgetLiveTuiSession(persistentSessionId);
  }

  state.sessions = state.sessions.map((chat) => (
    chat.id === persistentSessionId ? applyLiveSnapshot(chat) : chat
  ));
  if (state.activeSessionId === persistentSessionId && state.activeSession) {
    state.activeSession = applyLiveSnapshot(state.activeSession);
  }

  if (ACTIVE_LIVE_SESSION_STATUSES.has(status)) {
    setSessionBusy(persistentSessionId, true, { transport: 'tui' });
  } else {
    resetTuiBridgeReconnectState(persistentSessionId);
    recoveringTuiSessionIds.delete(persistentSessionId);
    const targetMessages = getSessionMessagesBuffer(persistentSessionId) || [];
    for (const message of targetMessages) {
      if (String(message?.role || '') !== 'assistant') continue;
      const messageId = String(message?.id || '').trim();
      if (messageId && state.streamingMessageIds.has(messageId)) {
        state.streamingMessageIds.delete(messageId);
      }
    }
    setSessionBusy(persistentSessionId, false);
  }

  if (pendingApproval && status === 'awaiting_approval') {
    setSessionPendingApproval(persistentSessionId, {
      approvalId: '',
      command: String(pendingApproval.command || ''),
      description: String(pendingApproval.description || 'Hermes needs approval to continue.'),
      patternKey: '',
      patternKeys: [],
      options: [],
    });
  } else {
    clearSessionPendingApproval(persistentSessionId);
  }
};

const getSessionMessagesBuffer = (persistentSessionId) => {
  const sessionId = String(persistentSessionId || '').trim();
  if (!sessionId) return null;
  const liveMessages = getLiveSessionMessages(sessionId);
  if (liveMessages) return liveMessages;
  if (state.activeSessionId === sessionId) return state.messages;
  return null;
};

const getCachedSessionMessages = (sessionId) => {
  const normalizedSessionId = String(sessionId || '').trim();
  if (!normalizedSessionId) return null;
  return getLiveSessionMessages(normalizedSessionId);
};

const setSessionMessagesBuffer = (persistentSessionId, messages) => {
  const sessionId = String(persistentSessionId || '').trim();
  if (!sessionId || !Array.isArray(messages)) return;
  setLiveSessionMessages(sessionId, messages);
  if (isViewingSession(sessionId)) {
    state.messages = messages;
  }
};

const updateActiveTuiSessionMapping = ({ liveSessionId = '', persistentSessionId = '' } = {}) => {
  activeTuiSessionId = String(liveSessionId || activeTuiSessionId || '');
  activePersistentSessionId = String(persistentSessionId || activePersistentSessionId || '');
  rememberLiveTuiSession(activePersistentSessionId, activeTuiSessionId);
  if (!activePersistentSessionId && state.activeSession) {
    activePersistentSessionId = getPersistentSessionIdForChat(state.activeSession);
  }
  if (state.activeSession && activePersistentSessionId) {
    state.activeSession.persistentSessionId = activePersistentSessionId;
    if (!state.activeSession.isDraft) {
      state.activeSession.id = activePersistentSessionId;
    }
  }
};

const appendTuiAssistantDelta = (sessionId, text) => {
  const targetSessionId = String(sessionId || '').trim();
  if (!targetSessionId || !text) return;
  const targetMessages = getSessionMessagesBuffer(targetSessionId);
  if (!targetMessages) return;
  const assistantMessage = targetMessages.find((message) => state.streamingMessageIds.has(message.id));
  if (!assistantMessage) return;
  assistantMessage.content += text;
  const progressLines = extractDisplayProgressLines(text);
  if (progressLines.length > 0) {
    appendProgressEntries(assistantMessage, progressLines);
  }
  setSessionMessagesBuffer(targetSessionId, targetMessages);
};

const getStreamingAssistantMessage = (sessionId) =>
  (getSessionMessagesBuffer(String(sessionId || '').trim()) || [])
    .find((message) => state.streamingMessageIds.has(message.id)) || null;

const finalizeStreamingAssistantMessage = (sessionId, { text = '', reasoning = '', status = 'complete' } = {}) => {
  const assistantMessage = getStreamingAssistantMessage(sessionId);
  if (!assistantMessage) return;
  const normalizedStatus = String(status || 'complete').trim().toLowerCase();
  if (text && !assistantMessage.content.trim()) {
    assistantMessage.content = text;
  }
  if (
    normalizedStatus === 'error' &&
    !assistantMessage.content.includes(MODEL_RESPONSE_ERROR_MESSAGE)
  ) {
    const currentContent = assistantMessage.content.trim();
    assistantMessage.content = currentContent
      ? `${currentContent}\n\n${MODEL_RESPONSE_ERROR_MESSAGE}`
      : MODEL_RESPONSE_ERROR_MESSAGE;
  }
  if (reasoning) {
    assistantMessage.reasoningContent = reasoning;
  }
  assistantMessage.done = true;
  assistantMessage.timestamp = nowSeconds();
  state.streamingMessageIds.delete(assistantMessage.id);
};

const markSessionInterruptedLocally = (sessionId, liveState = null) => {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId) return;

  const targetMessages = getSessionMessagesBuffer(persistentSessionId) || [];
  for (const message of targetMessages) {
    if (String(message?.role || '') !== 'assistant') continue;
    const messageId = String(message?.id || '').trim();
    if (!messageId || !state.streamingMessageIds.has(messageId)) continue;
    message.done = true;
    message.timestamp = nowSeconds();
    state.streamingMessageIds.delete(messageId);
  }
  if (targetMessages.length > 0) {
    setSessionMessagesBuffer(persistentSessionId, targetMessages);
  }

  const normalizedLiveState = liveState && typeof liveState === 'object'
    ? liveState
    : { status: 'interrupted' };
  state.sessions = state.sessions.map((chat) => (
    chat.id === persistentSessionId
      ? {
          ...chat,
          live: normalizedLiveState,
          is_running: false,
          has_pending_approval: false,
          last_active: nowSeconds(),
        }
      : chat
  ));
  if (state.activeSessionId === persistentSessionId && state.activeSession) {
    state.activeSession = {
      ...state.activeSession,
      live: normalizedLiveState,
      is_running: false,
      has_pending_approval: false,
      last_active: nowSeconds(),
    };
  }

  applyLiveStateToSession(
    persistentSessionId,
    normalizedLiveState
  );
  setSessionBusy(persistentSessionId, false);
  clearSessionPendingApproval(persistentSessionId);
};

const shouldRecoverTuiSession = (sessionId) => {
  if (!state.user) return false;
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId) return false;
  if (isSessionBusy(persistentSessionId)) return true;
  return sessionHasStreamingMessages(persistentSessionId);
};

const clearTuiBridgeReconnectTimer = (sessionId = '') => {
  const persistentSessionId = String(sessionId || '').trim();
  if (persistentSessionId) {
    const timer = tuiBridgeReconnectTimersBySessionId.get(persistentSessionId);
    if (timer) {
      window.clearTimeout(timer);
      tuiBridgeReconnectTimersBySessionId.delete(persistentSessionId);
    }
    return;
  }
  for (const timer of tuiBridgeReconnectTimersBySessionId.values()) {
    window.clearTimeout(timer);
  }
  tuiBridgeReconnectTimersBySessionId.clear();
};

const resetTuiBridgeReconnectState = (sessionId = '') => {
  const persistentSessionId = String(sessionId || '').trim();
  clearTuiBridgeReconnectTimer(persistentSessionId);
  if (persistentSessionId) {
    tuiBridgeReconnectAttemptsBySessionId.delete(persistentSessionId);
    return;
  }
  tuiBridgeReconnectAttemptsBySessionId.clear();
};

const getTuiBridgeReconnectDelay = (sessionId) => {
  const persistentSessionId = String(sessionId || '').trim();
  const attempts = tuiBridgeReconnectAttemptsBySessionId.get(persistentSessionId) || 0;
  const delay = TUI_BRIDGE_RECONNECT_BASE_DELAY_MS * (2 ** Math.max(attempts, 0));
  return Math.min(delay, TUI_BRIDGE_RECONNECT_MAX_DELAY_MS);
};

const scheduleTuiBridgeRecovery = (sessionId) => {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId || !state.user) return;
  const attempts = tuiBridgeReconnectAttemptsBySessionId.get(persistentSessionId) || 0;
  const delay = getTuiBridgeReconnectDelay(persistentSessionId);
  tuiBridgeReconnectAttemptsBySessionId.set(persistentSessionId, attempts + 1);
  clearTuiBridgeReconnectTimer(persistentSessionId);
  const timer = window.setTimeout(() => {
    tuiBridgeReconnectTimersBySessionId.delete(persistentSessionId);
    recoverTuiBridgeAfterDisconnect(persistentSessionId).catch(() => {});
  }, delay);
  tuiBridgeReconnectTimersBySessionId.set(persistentSessionId, timer);
};

const recoverTuiBridgeAfterDisconnect = async (sessionId) => {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId || !state.user || recoveringTuiSessionIds.has(persistentSessionId)) return;

  recoveringTuiSessionIds.add(persistentSessionId);
  try {
    let bridgeReadyBeforeSnapshot = false;
    try {
      await ensureTuiBridge();
      bridgeReadyBeforeSnapshot = true;
    } catch {
      // The snapshot fetch below still tells us whether the run already finished.
    }
    let session = await updateSessionSnapshot(persistentSessionId, {
      forceReplaceLiveMessages: true,
    });
    let liveState = session?.live || (isViewingSession(persistentSessionId) ? state.activeSession?.live : null);
    let status = String(liveState?.status || '').trim();
    if (!ACTIVE_LIVE_SESSION_STATUSES.has(status)) {
      resetTuiBridgeReconnectState(persistentSessionId);
      if (isViewingSession(persistentSessionId)) {
        renderWorkspace();
      } else {
        renderChatList();
      }
      return;
    }

    try {
      await ensureTuiBridge();
      if (!bridgeReadyBeforeSnapshot) {
        session = await updateSessionSnapshot(persistentSessionId, {
          forceReplaceLiveMessages: true,
        });
        liveState = session?.live || (isViewingSession(persistentSessionId) ? state.activeSession?.live : null);
        status = String(liveState?.status || '').trim();
        if (!ACTIVE_LIVE_SESSION_STATUSES.has(status)) {
          resetTuiBridgeReconnectState(persistentSessionId);
          if (isViewingSession(persistentSessionId)) {
            renderWorkspace();
          } else {
            renderChatList();
          }
          return;
        }
      }
      resetTuiBridgeReconnectState(persistentSessionId);
      if (isViewingSession(persistentSessionId)) {
        renderWorkspace();
      } else {
        renderChatList();
      }
    } catch {
      scheduleTuiBridgeRecovery(persistentSessionId);
    }
  } catch {
    scheduleTuiBridgeRecovery(persistentSessionId);
  } finally {
    recoveringTuiSessionIds.delete(persistentSessionId);
  }
};

const handleTuiBridgeClosed = (closedBridge = null) => {
  if (closedBridge && tuiBridge && tuiBridge !== closedBridge) {
    return;
  }
  if (closedBridge && tuiBridgeConnectingSocket && tuiBridgeConnectingSocket !== closedBridge) {
    return;
  }
  if (closedBridge && intentionallyClosedTuiBridges.has(closedBridge)) {
    return;
  }
  const sessionsToRecover = new Set();
  const activeSessionId = getActivePersistentSessionId();
  if (shouldRecoverTuiSession(activeSessionId)) {
    sessionsToRecover.add(activeSessionId);
  }
  for (const sessionId of busySessionIds) {
    if (sessionRunTransportById.get(sessionId) === 'tui' || sessionHasStreamingMessages(sessionId)) {
      sessionsToRecover.add(sessionId);
    }
  }

  closeTuiBridge({ suppressRecovery: false });

  for (const sessionId of sessionsToRecover) {
    recoverTuiBridgeAfterDisconnect(sessionId).catch(() => {});
  }
};

const nextTuiBridgeRequestId = () => `tui-${++tuiBridgeRequestCounter}`;

const closeTuiBridge = ({ suppressRecovery = true } = {}) => {
  const bridgeToClose = tuiBridge;
  const connectingBridgeToClose = (
    tuiBridgeConnectingSocket && tuiBridgeConnectingSocket !== bridgeToClose
      ? tuiBridgeConnectingSocket
      : null
  );
  const bridgesToClose = [bridgeToClose, connectingBridgeToClose].filter(Boolean);
  for (const bridge of bridgesToClose) {
    if (suppressRecovery && bridge.readyState !== WebSocket.CLOSED) {
      intentionallyClosedTuiBridges.add(bridge);
    }
    try {
      bridge.close();
    } catch {}
  }
  tuiBridge = null;
  tuiBridgeConnectPromise = null;
  tuiBridgeConnectingSocket = null;
  activeTuiSessionId = '';
  for (const pending of tuiBridgePending.values()) {
    pending.reject(new Error('TUI bridge disconnected'));
  }
  tuiBridgePending.clear();
  refreshComposerBusyState();
  renderApprovalModal();
  setTuiBridgeStatus('', { hidden: true });
};

const getPersistentSessionIdFromTuiEvent = (message) => {
  const persistentSessionId = String(
    message?.persistent_session_id || message?.persistentSessionId || ''
  ).trim();
  if (persistentSessionId) {
    const liveSessionId = String(message?.session_id || '').trim();
    if (liveSessionId) {
      rememberLiveTuiSession(persistentSessionId, liveSessionId);
    }
    return persistentSessionId;
  }
  const liveSessionId = String(message?.session_id || '').trim();
  if (!liveSessionId) return '';
  return String(getPersistentSessionIdForLiveTuiSession(liveSessionId) || '').trim();
};

const handleTuiBridgeEvent = (message) => {
  const type = String(message?.type || '');
  if (!type || type === 'rpc.result' || type === 'rpc.error') return;

  if (type === 'gateway.ready') {
    setTuiBridgeStatus('TUI gateway connected');
    return;
  }

  if (type === 'gateway.stderr') {
    const line = String(message?.payload?.line || '').trim();
    if (line) {
      setTuiBridgeStatus(`TUI gateway: ${line}`);
    }
    return;
  }

  if (type === 'gateway.exit') {
    for (const [sessionId, transport] of sessionRunTransportById.entries()) {
      if (transport === 'tui') {
        busySessionIds.delete(sessionId);
        sessionRunTransportById.delete(sessionId);
        sessionAbortControllersById.delete(sessionId);
        pendingApprovalsBySessionId.delete(sessionId);
      }
    }
    refreshComposerBusyState();
    renderChatList();
    renderApprovalModal();
    setTuiBridgeStatus('TUI gateway exited');
    return;
  }

  if (type === 'message.start') {
    const liveSessionId = String(message?.session_id || '').trim();
    const persistentSessionId = getPersistentSessionIdFromTuiEvent(message);
    if (liveSessionId && persistentSessionId) {
      rememberLiveTuiSession(persistentSessionId, liveSessionId);
      if (isViewingSession(persistentSessionId)) {
        updateActiveTuiSessionMapping({
          liveSessionId,
          persistentSessionId,
        });
      }
      setSessionBusy(persistentSessionId, true, { transport: 'tui' });
    }
    setTuiBridgeStatus('TUI bridge session started');
    return;
  }

  if (type === 'message.delta') {
    const persistentSessionId = getPersistentSessionIdFromTuiEvent(message);
    const text = String(message?.payload?.text || '');
    appendTuiAssistantDelta(persistentSessionId, text);
    if (isViewingSession(persistentSessionId)) {
      renderMessages();
    }
    if (text) {
      setTuiBridgeStatus(`TUI delta: ${text.slice(0, 120)}`);
    }
    return;
  }

  if (type === 'message.complete') {
    const text = String(message?.payload?.text || '');
    const status = String(message?.payload?.status || 'complete');
    const completedSessionId = getPersistentSessionIdFromTuiEvent(message);
    finalizeStreamingAssistantMessage(completedSessionId, {
      text,
      reasoning: String(message?.payload?.reasoning || ''),
      status,
    });
    forgetLiveTuiSession(completedSessionId);
    setSessionBusy(completedSessionId, false);
    clearSessionPendingApproval(completedSessionId);
    if (isViewingSession(completedSessionId)) {
      renderMessages();
      renderWorkspace();
    } else {
      renderChatList();
    }
    setTuiBridgeStatus(
      status.trim().toLowerCase() === 'error'
        ? MODEL_RESPONSE_ERROR_MESSAGE
        : (text ? `TUI complete: ${text.slice(0, 120)}` : 'TUI request completed')
    );
    if (completedSessionId) {
      window.setTimeout(async () => {
        try {
          await updateSessionSnapshot(completedSessionId, { preserveLiveMessages: true });
          if (isViewingSession(completedSessionId)) {
            syncActiveSessionFromSessions(completedSessionId);
            renderWorkspace();
          } else {
            renderChatList();
          }
        } catch {}
      }, 1200);
    }
    return;
  }

  if (type === 'approval.request') {
    const persistentSessionId = getPersistentSessionIdFromTuiEvent(message);
    setSessionPendingApproval(persistentSessionId, {
      approvalId: '',
      command: String(message?.payload?.command || ''),
      description: String(message?.payload?.description || 'Hermes needs approval to continue.'),
      patternKey: '',
      patternKeys: [],
      options: [],
    });
    setTuiBridgeStatus('TUI gateway requested approval');
    return;
  }

  if (type === 'tool.progress') {
    const persistentSessionId = getPersistentSessionIdFromTuiEvent(message);
    const preview = String(message?.payload?.preview || message?.payload?.name || 'tool');
    const assistantMessage = getStreamingAssistantMessage(persistentSessionId);
    if (assistantMessage) {
      appendProgressEntries(assistantMessage, [preview]);
      if (isViewingSession(persistentSessionId)) {
        renderMessages();
      }
    }
    setTuiBridgeStatus(`TUI tool progress: ${preview.slice(0, 120)}`);
    return;
  }

  if (type === 'tool.start') {
    const persistentSessionId = getPersistentSessionIdFromTuiEvent(message);
    const preview = String(message?.payload?.context || message?.payload?.name || 'tool');
    const assistantMessage = getStreamingAssistantMessage(persistentSessionId);
    if (assistantMessage && preview) {
      appendProgressEntries(assistantMessage, [preview]);
      if (isViewingSession(persistentSessionId)) {
        renderMessages();
      }
    }
    if (preview) {
      setTuiBridgeStatus(`TUI tool started: ${preview.slice(0, 120)}`);
    }
    return;
  }

  if (type === 'tool.complete') {
    const persistentSessionId = getPersistentSessionIdFromTuiEvent(message);
    const summary = String(message?.payload?.summary || message?.payload?.name || 'tool completed');
    const assistantMessage = getStreamingAssistantMessage(persistentSessionId);
    if (assistantMessage && summary) {
      appendProgressEntries(assistantMessage, [summary]);
      if (isViewingSession(persistentSessionId)) {
        renderMessages();
      }
    }
    if (summary) {
      setTuiBridgeStatus(`TUI tool completed: ${summary.slice(0, 120)}`);
    }
    return;
  }

  if (type === 'background.complete') {
    setTuiBridgeStatus('TUI background task completed');
  }
};

const handleTuiBridgeMessage = (event) => {
  let message;
  try {
    message = JSON.parse(event.data);
  } catch {
    return;
  }

  const id = String(message?.id || '');
  if (message?.type === 'rpc.result' && id && tuiBridgePending.has(id)) {
    const pending = tuiBridgePending.get(id);
    tuiBridgePending.delete(id);
    pending.resolve(message.payload || {});
    return;
  }

  if (message?.type === 'rpc.error' && id && tuiBridgePending.has(id)) {
    const pending = tuiBridgePending.get(id);
    tuiBridgePending.delete(id);
    pending.reject(new Error(String(message?.payload?.message || 'TUI bridge error')));
    return;
  }

  handleTuiBridgeEvent(message);
};

const ensureTuiBridge = async () => {
  if (tuiBridge && tuiBridge.readyState === WebSocket.OPEN) {
    return tuiBridge;
  }
  if (tuiBridgeConnectPromise) {
    return tuiBridgeConnectPromise;
  }

  setTuiBridgeStatus('Connecting to TUI gateway...');
  tuiBridgeConnectPromise = new Promise((resolve, reject) => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/tui/ws`);
    tuiBridgeConnectingSocket = ws;

    const cleanup = () => {
      ws.removeEventListener('open', onOpen);
      ws.removeEventListener('error', onError);
      ws.removeEventListener('close', onCloseBeforeOpen);
      if (tuiBridgeConnectingSocket === ws) {
        tuiBridgeConnectingSocket = null;
      }
    };

    const onOpen = () => {
      cleanup();
      tuiBridge = ws;
      tuiBridgeConnectPromise = null;
      ws.addEventListener('message', handleTuiBridgeMessage);
      ws.addEventListener('close', () => {
        handleTuiBridgeClosed(ws);
      }, { once: true });
      setTuiBridgeStatus('TUI gateway connected');
      resolve(ws);
    };

    const onError = () => {
      cleanup();
      tuiBridgeConnectPromise = null;
      reject(new Error('Failed to connect to TUI gateway'));
    };

    const onCloseBeforeOpen = () => {
      cleanup();
      tuiBridgeConnectPromise = null;
      reject(new Error('TUI gateway closed before connection was established'));
    };

    ws.addEventListener('open', onOpen);
    ws.addEventListener('error', onError);
    ws.addEventListener('close', onCloseBeforeOpen);
  });

  return tuiBridgeConnectPromise;
};

const tuiBridgeRpc = async (method, params = {}) => {
  const socket = await ensureTuiBridge();
  const id = nextTuiBridgeRequestId();
  const payload = { id, method, params };
  const result = await new Promise((resolve, reject) => {
    tuiBridgePending.set(id, { resolve, reject });
    try {
      socket.send(JSON.stringify(payload));
    } catch (error) {
      tuiBridgePending.delete(id);
      reject(error instanceof Error ? error : new Error(String(error || 'send failed')));
    }
  });
  return result;
};

const ensureTuiChatSession = async () => {
  const activePersistentId = getPersistentSessionIdForChat(state.activeSession);
  const activeResumeId = getResumeSessionIdForChat(state.activeSession) || activePersistentId;
  const rememberedLiveId = liveTuiSessionsByPersistentId.get(activePersistentId) || '';
  if (rememberedLiveId && activePersistentId) {
    updateActiveTuiSessionMapping({
      liveSessionId: rememberedLiveId,
      persistentSessionId: activePersistentId,
    });
    return {
      liveSessionId: rememberedLiveId,
      persistentSessionId: activePersistentId,
      resumed: true,
    };
  }

  if (activeTuiSessionId && activePersistentSessionId && activePersistentSessionId === activePersistentId) {
    return {
      liveSessionId: activeTuiSessionId,
      persistentSessionId: activePersistentSessionId,
      resumed: true,
    };
  }

  if (state.activeSession?.isDraft || !activePersistentId) {
    if (state.pendingSessionPromise) {
      await state.pendingSessionPromise;
      return ensureTuiChatSession();
    }

    const createdSession = await createSessionForCurrentMode();
    if (!createdSession?.id) {
      throw new Error('Failed to create TUI gateway session');
    }

    activatePersistedSession(createdSession, { clearMessages: false });

    return {
      liveSessionId: liveTuiSessionsByPersistentId.get(createdSession.id) || activeTuiSessionId,
      persistentSessionId: createdSession.id,
      resumed: false,
    };
  }

  const resumed = await tuiBridgeRpc('session.resume', {
    cols: 100,
    session_id: activeResumeId,
  });
  const liveSessionId = String(resumed?.session_id || '');
  if (!liveSessionId) {
    throw new Error('TUI gateway did not return a live session id on resume');
  }
  const resumedPersistentId = String(activePersistentId || '').trim();
  const resumedSessionKey = String(resumed?.resumed || activeResumeId || resumedPersistentId).trim();
  if (!resumedPersistentId) {
    throw new Error('TUI gateway resume lost the logical session id');
  }
  if (resumedSessionKey) {
    rememberLiveTuiSession(resumedSessionKey, liveSessionId);
  }
  updateActiveTuiSessionMapping({
    liveSessionId,
    persistentSessionId: resumedPersistentId,
  });
  return {
    liveSessionId,
    persistentSessionId: resumedPersistentId,
    resumed: true,
  };
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

const refreshComposerBusyState = () => {
  syncActiveSessionUiState();
  const busy = state.isSending;
  if (dom.attachButton) {
    dom.attachButton.disabled = busy;
  }
  if (dom.sendButton) {
    dom.sendButton.type = busy ? 'button' : 'submit';
    dom.sendButton.ariaLabel = busy ? 'Stop response' : 'Send message';
    dom.sendButton.title = busy ? 'Stop response' : 'Send message';
  }
  if (dom.sendButtonIcon) {
    dom.sendButtonIcon.src = busy ? ICON_STOP_PATH : ICON_SEND_PATH;
  }
  if (dom.modelSelect) {
    dom.modelSelect.disabled = isAnySessionBusy() || state.models.length <= 1;
  }
};

const setSessionBusy = (sessionId, busy, { transport = '', abortController = null } = {}) => {
  const normalizedSessionId = String(sessionId || '').trim();
  if (!normalizedSessionId) return;

  if (busy) {
    busySessionIds.add(normalizedSessionId);
    if (transport) {
      sessionRunTransportById.set(normalizedSessionId, transport);
    }
    if (abortController) {
      sessionAbortControllersById.set(normalizedSessionId, abortController);
    }
  } else {
    busySessionIds.delete(normalizedSessionId);
    sessionRunTransportById.delete(normalizedSessionId);
    sessionAbortControllersById.delete(normalizedSessionId);
  }

  refreshComposerBusyState();
  renderChatList();
  renderAttachments();
};

const setSessionPendingApproval = (sessionId, approval) => {
  const normalizedSessionId = String(sessionId || '').trim();
  if (!normalizedSessionId || !approval) return;
  pendingApprovalsBySessionId.set(normalizedSessionId, {
    ...approval,
    sessionId: normalizedSessionId,
  });
  syncActiveSessionUiState();
  renderApprovalModal();
};

const clearSessionPendingApproval = (sessionId = '') => {
  const normalizedSessionId = String(sessionId || '').trim()
    || String(state.pendingApproval?.sessionId || '').trim();
  if (!normalizedSessionId) {
    state.pendingApproval = null;
    showError(dom.approvalError, '');
    renderApprovalModal();
    return;
  }
  pendingApprovalsBySessionId.delete(normalizedSessionId);
  syncActiveSessionUiState();
  showError(dom.approvalError, '');
  renderApprovalModal();
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

const activatePersistedSession = (session, { clearMessages = true } = {}) => {
  const normalizedSession = normalizeSessionSnapshot(session);
  if (!normalizedSession?.id) {
    throw new Error('Failed to activate session');
  }

  state.draftSession = null;
  state.activeSession = normalizedSession;
  state.activeSessionId = normalizedSession.id;
  state.sessions = [
    normalizedSession,
    ...state.sessions.filter((chat) => chat.id !== normalizedSession.id),
  ];
  if (clearMessages) {
    state.messages = [];
  }
};

const showDraftChat = () => {
  resetSessionRenameState();
  activeTuiSessionId = '';
  activePersistentSessionId = '';
  state.sessionHistoryLoading = false;
  state.pendingAttachments = [];
  renderAttachments();
  state.draftSession = createDraftSession();
  state.activeSession = state.draftSession;
  state.activeSessionId = state.draftSession.id;
  state.messages = [];
  state.shouldAutoScrollMessages = true;
  renderWorkspace();
  dom.promptInput?.focus();
};

const setLiveSessionMessages = (sessionId, messages) => {
  if (!sessionId || !Array.isArray(messages)) return;
  state.liveSessionMessages.set(sessionId, messages);
};

const clearLiveSessionMessages = (sessionId) => {
  if (!sessionId) return;
  state.liveSessionMessages.delete(sessionId);
};

const getLiveSessionMessages = (sessionId) => {
  if (!sessionId) return null;
  return state.liveSessionMessages.get(sessionId) || null;
};

const sessionHasStreamingMessages = (sessionId) => Boolean(
  getLiveSessionMessages(sessionId)?.some((message) => state.streamingMessageIds.has(message.id))
);

const isScrolledNearBottom = (element) => {
  if (!element) return true;
  const remaining = element.scrollHeight - element.clientHeight - element.scrollTop;
  return remaining <= MESSAGE_AUTO_SCROLL_THRESHOLD_PX;
};

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
  clearSessionPendingApproval();
};

const submitApprovalDecision = async (choice) => {
  const approval = state.pendingApproval;
  if (!approval?.sessionId || state.approvalSubmitting) return;
  showError(dom.approvalError, '');
  setApprovalSubmitting(true);

  const approvalSessionId = String(approval.sessionId || '').trim();

  try {
    const response = await api(`/api/sessions/${encodeURIComponent(approvalSessionId)}/approval`, {
      method: 'POST',
      body: JSON.stringify({ choice }),
    });
    const json = await response.json();
    applyLiveStateToSession(approvalSessionId, json?.live || null);
    clearSessionPendingApproval(approvalSessionId);
    setApprovalSubmitting(false);
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
  setAuthViewMode('signin');
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
  const showHome = mode === 'home';
  const showSignin = mode === 'signin';
  const showRegister = mode === 'register';
  const showWait = mode === 'signup-wait';
  const showRuntimeStart = mode === 'runtime-start';

  if (dom.authCard) {
    dom.authCard.dataset.authMode = mode;
  }

  if (showHome) {
    showError(dom.loginError, '');
    showError(dom.registerError, '');
  }

  if (showSignin) {
    dom.authCardLabel.textContent = 'Sign in';
    dom.authCardTitle.textContent = 'Enter Potato Agent';
    dom.authCardCopy.textContent = 'Use your account to open the isolated Potato Agent workspace assigned to you.';
  }

  if (showRegister) {
    dom.authCardLabel.textContent = 'Register';
    dom.authCardTitle.textContent = 'Create your workspace';
    dom.authCardCopy.textContent = 'A dedicated Linux user and Potato Agent runtime will be provisioned for your account.';
  }

  if (showWait) {
    dom.authCardLabel.textContent = 'Provisioning';
    dom.authCardTitle.textContent = 'Creating your workspace';
    dom.authCardCopy.textContent = 'Please wait while your dedicated Potato Agent workspace is being provisioned.';
  }

  if (showRuntimeStart) {
    dom.authCardLabel.textContent = 'Starting runtime';
    dom.authCardTitle.textContent = 'Waking your workspace';
    dom.authCardCopy.textContent = 'We are starting the Potato Agent service bound to your account before entering the workspace.';
  }

  dom.authHomeView.hidden = !showHome;
  dom.authPanelHeader.hidden = showHome;
  dom.loginForm.hidden = !showSignin;
  dom.signinNavActions.hidden = !showSignin;
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
  resetTuiBridgeReconnectState();
  closeTuiBridge();
  recoveringTuiSessionIds.clear();
  liveTuiSessionsByPersistentId.clear();
  liveTuiSessionAliasesByPersistentId.clear();
  persistentIdsByLiveTuiSessionId.clear();
  activeTuiSessionId = '';
  activePersistentSessionId = '';
  state.sessions = [];
  state.activeSession = null;
  state.activeSessionId = null;
  state.draftSession = null;
  resetSessionRenameState();
  state.pendingSessionPromise = null;
  state.shouldAutoScrollMessages = true;
  state.liveSessionMessages.clear();
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
  busySessionIds.clear();
  sessionRunTransportById.clear();
  sessionAbortControllersById.clear();
  pendingApprovalsBySessionId.clear();
  interruptingSessionIds.clear();
  state.pendingAttachments = [];
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
    title: 'Starting your Potato Agent runtime',
    copy: 'We are waking the dedicated Potato Agent service bound to your account. This usually takes a few seconds.',
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
    const message = String(error.message || 'Failed to start Potato Agent runtime');
    showRuntimeStartView({
      title: 'Failed to start your Potato Agent runtime',
      copy: source === 'restore'
        ? 'The previous session is valid, but the Potato Agent runtime could not be started. Review the error below before retrying.'
        : 'Sign-in succeeded, but the Potato Agent runtime could not be started. Review the error below before retrying.',
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
    provisioning: 'We are creating a dedicated Linux user and Potato Agent runtime for your account.',
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
      const payloadDetail = payload?.detail;
      detail = (
        (payloadDetail && typeof payloadDetail === 'object' ? payloadDetail.message : payloadDetail)
        || payload?.message
        || payload?.error?.message
        || payload?.error
        || detail
      );
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
    id: normalizeDisplayMessageId(message?.id ?? uuid()),
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
    (Array.isArray(message?.progressLines) && message.progressLines.length > 0) ||
    (Array.isArray(message?.files) && message.files.length > 0)
  );

const getCurrentChatEntries = () => {
  if (state.draftSession) {
    return [state.draftSession, ...state.sessions];
  }
  return state.sessions;
};

const getActiveChatTitle = () => {
  return state.activeSession?.title || 'New chat';
};

const renameSession = async (sessionId, nextTitle) => {
  const normalizedSessionId = String(sessionId || '').trim();
  const validationMessage = validateSessionTitleDraft(nextTitle);
  if (validationMessage) {
    state.renamingTitleError = validationMessage;
    renderChatList();
    return null;
  }

  const response = await api(`/api/sessions/${encodeURIComponent(normalizedSessionId)}/title`, {
    method: 'PUT',
    body: JSON.stringify({ title: String(nextTitle || '') }),
  });
  const json = await response.json();
  const normalizedSession = applySessionSnapshot(
    json?.session
      ? { ...json.session, persistentSessionId: json.session.id }
      : null
  );
  if (normalizedSession?.id) {
    applyLiveStateToSession(normalizedSession.id, json?.live || normalizedSession.live || null);
  }
  if (
    normalizedSession?.id
    && state.activeSessionId === normalizedSession.id
    && Array.isArray(json?.messages)
    && !sessionHasStreamingMessages(normalizedSession.id)
  ) {
    const normalizedMessages = json.messages.map(normalizeMessageForDisplay);
    setLiveSessionMessages(normalizedSession.id, normalizedMessages);
    state.messages = normalizedMessages;
  }
  resetSessionRenameState();
  renderWorkspace();
  return normalizedSession;
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

  return blocks.join('\n\n').trim();
};

const getRenderedMessageContentHtml = (message) => {
  const source = String(message?.content ?? '');
  if (
    message
    && typeof message === 'object'
    && message._renderedContentSource === source
    && typeof message._renderedContentHtml === 'string'
  ) {
    return message._renderedContentHtml;
  }

  const rendered = renderMarkdown(source);
  if (message && typeof message === 'object') {
    message._renderedContentSource = source;
    message._renderedContentHtml = rendered;
  }
  return rendered;
};

const formatMessageTimestamp = (timestamp) => {
  const seconds = Number(timestamp || 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return '';

  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return '';

  const pad = (value) => String(value).padStart(2, '0');
  const hoursMinutes = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const isOlderThan24Hours = Date.now() - date.getTime() > 24 * 60 * 60 * 1000;
  if (!isOlderThan24Hours) return hoursMinutes;

  const year = date.getFullYear();
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  return `${year}-${month}-${day} ${hoursMinutes}`;
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
      (Array.isArray(normalizedMessage.progressLines) && normalizedMessage.progressLines.length > 0);
    const hasTextualContent = Boolean(
      String(normalizedMessage.content ?? '').trim()
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
  const shouldStickToBottom = state.shouldAutoScrollMessages || isScrolledNearBottom(dom.messages);
  dom.messages.innerHTML = '';
  const visibleMessages = getRenderableMessages();

  if (state.sessionHistoryLoading) {
    const empty = document.createElement('div');
    empty.className = 'empty-state loading-history';
    empty.textContent = 'Loading chat history...';
    dom.messages.append(empty);
    return;
  }

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
    const timestamp = fragment.querySelector('.message-timestamp');
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
    const timestampText = !isStreaming ? formatMessageTimestamp(message.timestamp) : '';
    timestamp.textContent = timestampText;
    timestamp.hidden = !timestampText;
    content.classList.remove('streaming-placeholder');

    const hasVisibleContent = hasDisplayContent(message);

    if (isStreaming && !hasVisibleContent) {
      content.classList.add('streaming-placeholder');
      content.innerHTML = '<span class="message-inline-streaming"><span></span><span></span><span></span></span>';
      streamingIndicator.hidden = true;
      streamingIndicator.classList.remove('inline', 'footer');
    } else {
      content.innerHTML = getRenderedMessageContentHtml(message);
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
        copyIcon.src = ICON_COPIED_PATH;
      } catch {
        showChatError('Copy failed. Please try again.');
      } finally {
        window.setTimeout(() => {
          copyIcon.src = ICON_COPY_PATH;
        }, 3000);
      }
    });

    dom.messages.append(fragment);
  }

  state.shouldAutoScrollMessages = shouldStickToBottom;
  if (shouldStickToBottom) {
    dom.messages.scrollTop = dom.messages.scrollHeight;
  }
};

const getChatDisplayTitle = (chat) => {
  if (!chat) return 'New chat';
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
    const shell = fragment.querySelector('.chat-item-shell');
    const button = fragment.querySelector('.chat-item');
    const title = fragment.querySelector('.chat-item-title');
    const meta = fragment.querySelector('.chat-item-meta');
    const actions = fragment.querySelector('.chat-item-actions');
    const renameButton = fragment.querySelector('.chat-rename-button');
    const deleteButton = fragment.querySelector('.chat-delete-button');
    const chatSessionId = getPersistentSessionIdForChat(chat) || chat.id;
    const busyLabel = isSessionBusy(chatSessionId) ? 'Responding…' : '';
    const approvalLabel = sessionNeedsApproval(chatSessionId) ? 'Needs approval' : '';
    const isRenaming = !chat.isDraft && state.renamingSessionId === chat.id;

    if (isRenaming) {
      shell?.classList.add('renaming');
      const replacement = document.createElement('div');
      replacement.className = `${button.className} chat-item-editing`;
      if (chat.id === state.activeSessionId) {
        replacement.classList.add('active');
      }
      replacement.setAttribute('aria-current', chat.id === state.activeSessionId ? 'page' : 'false');
      const titleWrap = document.createElement('div');
      titleWrap.className = 'chat-item-title-wrap';

      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'chat-item-title-input';
      input.value = state.renamingTitleDraft;
      input.setAttribute('aria-label', 'Chat title');

      const error = document.createElement('div');
      error.className = 'chat-item-title-error';
      error.hidden = !state.renamingTitleError;
      error.textContent = state.renamingTitleError || '';

      const cancelRename = () => {
        resetSessionRenameState();
        renderChatList();
      };

      const submitRename = async () => {
        try {
          await renameSession(chat.id, state.renamingTitleDraft);
        } catch (renameError) {
          state.renamingTitleError = String(renameError.message || 'Failed to rename chat.');
          renderChatList();
        }
      };

      input.addEventListener('click', (event) => event.stopPropagation());
      input.addEventListener('input', (event) => {
        setSessionRenameDraft(event.target.value);
        if (!error.hidden) {
          error.hidden = true;
          error.textContent = '';
        }
      });
      input.addEventListener('keydown', (event) => {
        event.stopPropagation();
        if (event.key === 'Enter') {
          event.preventDefault();
          submitRename();
          return;
        }
        if (event.key === 'Escape') {
          event.preventDefault();
          cancelRename();
        }
      });
      input.addEventListener('blur', () => {
        window.setTimeout(() => {
          if (state.renamingSessionId === chat.id) {
            cancelRename();
          }
        }, 0);
      });

      titleWrap.append(input);
      titleWrap.append(error);
      replacement.append(titleWrap);
      replacement.append(meta);
      button.replaceWith(replacement);

      window.requestAnimationFrame(() => {
        input.focus();
        input.select();
      });
    } else {
      title.textContent = getChatDisplayTitle(chat);
      meta.textContent = [formatTimestamp(chat.last_active || chat.started_at), busyLabel, approvalLabel]
        .filter(Boolean)
        .join(' · ');
    }

    if (!isRenaming && chat.id === state.activeSessionId) {
      button.classList.add('active');
    }

    if (!isRenaming) {
      button.addEventListener('click', () => {
        if (chat.isDraft) {
          state.activeSession = chat;
          state.activeSessionId = chat.id;
          renderWorkspace();
          return;
        }
        resetSessionRenameState();
        openSession(chat.id).catch((error) => showChatError(error.message));
      });
    }

    if (renameButton) {
      if (chat.isDraft) {
        renameButton.hidden = true;
      } else {
        renameButton.hidden = false;
        renameButton.addEventListener('click', (event) => {
          event.stopPropagation();
          if (state.renamingSessionId === chat.id) return;
          startSessionRename(chat.id);
          renderChatList();
        });
      }
    }

    deleteButton.addEventListener('click', async (event) => {
      event.stopPropagation();
      resetSessionRenameState();
      const confirmed = window.confirm(`Delete chat "${getChatDisplayTitle(chat)}"?`);
      if (!confirmed) return;
      await deleteChat(chat.id, chat.isDraft).catch((error) => showChatError(error.message));
    });

    if (isRenaming && actions) {
      actions.querySelectorAll('button').forEach((actionButton) => {
        if (actionButton !== renameButton) {
          actionButton.tabIndex = -1;
        }
      });
    }

    dom.chatList.append(fragment);
  }
};

const renderWorkspaceHeader = () => {
  dom.userEmail.textContent = state.user?.email || '';
  dom.chatTitle.textContent = getActiveChatTitle();
  if (!dom.modelSelect) return;
  const selectedId = String(state.selectedModel?.id || '').trim();
  dom.modelSelect.innerHTML = '';
  if (state.models.length === 0) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No model selected';
    dom.modelSelect.append(option);
  } else {
    for (const model of state.models) {
      const option = document.createElement('option');
      option.value = String(model.id || '').trim();
      option.textContent = getModelDisplayName(model);
      dom.modelSelect.append(option);
    }
  }
  dom.modelSelect.value = selectedId;
  dom.modelSelect.disabled = isAnySessionBusy() || state.models.length <= 1;
};

const renderFileBrowserControls = () => {
  if (!dom.fileOpenControls) return;
  dom.fileOpenControls.hidden = state.fileBrowserMode !== 'user_readable';
  if (state.fileBrowserMode === 'user_readable' && dom.filePathInput && !dom.filePathInput.value.trim()) {
    dom.filePathInput.value = state.homePath || state.workspaceRoot || state.user?.workspace_root || '~';
  }
};

const renderWorkspace = () => {
  syncActiveSessionUiState();
  renderChatList();
  renderWorkspaceHeader();
  renderMessages();
  renderAttachments();
  renderApprovalModal();
  renderFileBrowserControls();
  refreshComposerBusyState();
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
  const activeId = String(json?.active_id || json?.activeId || '').trim();
  state.selectedModel = state.models.find((model) => String(model.id || '').trim() === activeId)
    || state.models.find((model) => Boolean(model.is_active || model.isActive))
    || state.models[0]
    || null;
  renderWorkspaceHeader();
};

const switchActiveModel = async (modelId) => {
  const requestedId = String(modelId || '').trim();
  if (!requestedId || requestedId === String(state.selectedModel?.id || '').trim()) {
    renderWorkspaceHeader();
    return;
  }
  if (isAnySessionBusy()) {
    showChatError('Cannot switch models while a response or approval is active.');
    renderWorkspaceHeader();
    return;
  }

  const previousModel = state.selectedModel;
  const nextModel = state.models.find((model) => String(model.id || '').trim() === requestedId) || null;
  if (!nextModel) {
    renderWorkspaceHeader();
    return;
  }

  try {
    state.selectedModel = nextModel;
    renderWorkspaceHeader();
    const response = await api('/api/models/active', {
      method: 'PUT',
      body: JSON.stringify({ id: requestedId }),
    });
    const json = await response.json();
    const activeModel = json?.model || nextModel;
    state.models = state.models.map((model) => ({
      ...model,
      is_active: String(model.id || '').trim() === String(activeModel.id || requestedId).trim(),
      isActive: String(model.id || '').trim() === String(activeModel.id || requestedId).trim(),
    }));
    state.selectedModel = {
      ...nextModel,
      ...activeModel,
      is_active: true,
      isActive: true,
    };
    resetTuiBridgeReconnectState();
    closeTuiBridge();
    showChatError('');
    renderWorkspaceHeader();
  } catch (error) {
    state.selectedModel = previousModel;
    renderWorkspaceHeader();
    showChatError(String(error.message || 'Failed to switch model'));
  }
};

const refreshSessions = async () => {
  const response = await api('/api/sessions', { method: 'GET' });
  const json = await response.json();
  const sessions = Array.isArray(json?.sessions) ? json.sessions : [];
  sessions.sort((left, right) => (right.last_active || right.started_at || 0) - (left.last_active || left.started_at || 0));
  state.sessions = sessions
    .map((session) => normalizeSessionSnapshot({
      ...session,
      persistentSessionId: session.id,
    }))
    .filter(Boolean);
  for (const session of sessions) {
    applyLiveStateToSession(session?.id, session?.live || null);
  }
  if (state.renamingSessionId && !state.sessions.some((session) => session.id === state.renamingSessionId)) {
    resetSessionRenameState();
  }
  renderChatList();
  renderWorkspaceHeader();
};

const openSession = async (sessionId) => {
  resetSessionRenameState();
  if (!sessionId) {
    state.activeSession = state.draftSession;
    state.activeSessionId = state.draftSession?.id || null;
    state.messages = [];
    state.shouldAutoScrollMessages = true;
    renderWorkspace();
    return;
  }

  if (state.activeSessionId === sessionId && state.messages.length > 0) {
    return;
  }

  const matchedSession = state.sessions.find((session) => session.id === sessionId) || null;
  state.draftSession = null;
  state.activeSession = matchedSession
    ? { ...matchedSession, persistentSessionId: getPersistentSessionIdForChat(matchedSession) || matchedSession.id }
    : { id: sessionId, persistentSessionId: sessionId, title: 'New chat', isDraft: false };
  state.activeSessionId = sessionId;
  activePersistentSessionId = String(sessionId || '');
  activeTuiSessionId = liveTuiSessionsByPersistentId.get(activePersistentSessionId) || '';
  state.sessionHistoryLoading = true;
  state.messages = [];
  state.shouldAutoScrollMessages = true;
  renderWorkspace();

  const cachedMessages = getCachedSessionMessages(sessionId);
  if (cachedMessages) {
    const cachedSession = state.sessions.find((session) => session.id === sessionId) || state.activeSession || null;
    const liveState = cachedSession?.live || null;
    applyLiveStateToSession(sessionId, liveState);
    syncStreamingAssistantStateForSession(sessionId, {
      liveState,
      nextMessages: cachedMessages,
      previousMessages: cachedMessages,
    });
    state.sessionHistoryLoading = false;
    state.messages = cachedMessages;
    state.shouldAutoScrollMessages = true;
    renderWorkspace();
    if (ACTIVE_LIVE_SESSION_STATUSES.has(String(liveState?.status || ''))) {
      recoverTuiBridgeAfterDisconnect(sessionId).catch(() => {});
    }
    return;
  }

  try {
    const response = await api(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'GET' });
    const json = await response.json();
    const normalizedMessages = Array.isArray(json?.messages)
      ? json.messages.map(normalizeMessageForDisplay)
      : [];
    const liveState = json?.live || json?.session?.live || null;
    const previousMessages = getCachedSessionMessages(sessionId) || state.messages;
    setLiveSessionMessages(sessionId, normalizedMessages);
    state.activeSession = normalizeSessionSnapshot(
      json?.session
        ? { ...json.session, persistentSessionId: json.session.id }
        : null
    );
    applyLiveStateToSession(sessionId, liveState);
    syncStreamingAssistantStateForSession(sessionId, {
      liveState,
      nextMessages: normalizedMessages,
      previousMessages,
    });
    state.activeSessionId = state.activeSession?.id || null;
    state.sessionHistoryLoading = false;
    state.messages = normalizedMessages;
    state.shouldAutoScrollMessages = true;
    renderWorkspace();
    if (ACTIVE_LIVE_SESSION_STATUSES.has(String(liveState?.status || ''))) {
      recoverTuiBridgeAfterDisconnect(sessionId).catch(() => {});
    }
  } catch (error) {
    state.sessionHistoryLoading = false;
    renderWorkspace();
    throw error;
  }
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

const createSessionForCurrentMode = async () => {
  const session = await tuiBridgeRpc('session.create', { cols: 100 });
  const liveSessionId = String(session?.session_id || '');
  if (!liveSessionId) {
    throw new Error('Failed to create TUI gateway session');
  }
  const titleInfo = await tuiBridgeRpc('session.title', { session_id: liveSessionId });
  const persistentSessionId = String(titleInfo?.session_key || liveSessionId);
  rememberLiveTuiSession(persistentSessionId, liveSessionId);
  updateActiveTuiSessionMapping({
    liveSessionId,
    persistentSessionId,
  });

  const created = {
    id: persistentSessionId,
    persistentSessionId,
    title: 'New chat',
    preview: '',
    started_at: nowSeconds(),
    last_active: nowSeconds(),
    message_count: 0,
    source: 'tui',
  };
  state.sessions = [created, ...state.sessions.filter((chat) => chat.id !== created.id)];
  return created;
};

const updateSessionSnapshot = async (
  sessionId,
  { preserveLiveMessages = false, forceReplaceLiveMessages = false } = {},
) => {
  if (!sessionId) return null;

  const response = await api(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'GET' });
  const json = await response.json();
  const session = normalizeSessionSnapshot(json?.session || null);
  const normalizedMessages = Array.isArray(json?.messages)
    ? json.messages.map(normalizeMessageForDisplay)
    : null;
  const liveState = json?.live || json?.session?.live || null;
  const previousMessages = getCachedSessionMessages(sessionId);
  if (!session?.id) {
    return null;
  }

  const sessionWithLive = {
    ...session,
    live: liveState,
  };
  state.sessions = [sessionWithLive, ...state.sessions.filter((chat) => chat.id !== session.id)];
  applyLiveStateToSession(session.id, liveState);
  const shouldReplaceMessages = forceReplaceLiveMessages
    || !preserveLiveMessages
    || !sessionHasStreamingMessages(sessionId);
  if (Array.isArray(normalizedMessages) && shouldReplaceMessages) {
    setLiveSessionMessages(sessionId, normalizedMessages);
    syncStreamingAssistantStateForSession(sessionId, {
      liveState,
      nextMessages: normalizedMessages,
      previousMessages,
    });
  }
  if (state.activeSessionId === session.id) {
    state.activeSession = sessionWithLive;
    applyLiveStateToSession(session.id, liveState);
    if (Array.isArray(normalizedMessages) && shouldReplaceMessages) {
      state.messages = normalizedMessages;
    }
  }
  return state.sessions.find((chat) => chat.id === session.id) || sessionWithLive;
};

const isViewingSession = (sessionId) => Boolean(sessionId) && state.activeSessionId === sessionId;

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

    showDraftChat();
    return;
  }

  forgetLiveTuiSession(chatId);

  await api(`/api/sessions/${encodeURIComponent(chatId)}`, { method: 'DELETE' });
  clearLiveSessionMessages(chatId);
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
  showDraftChat();
};

const startNewChat = async () => {
  showDraftChat();
  dom.promptInput?.focus();
  return state.draftSession;
};

const submitPromptViaTuiBridge = async (prompt) => {
  const trimmedPrompt = prompt.trim();
  const uploadedAttachments = state.pendingAttachments.filter((item) => item.status === 'uploaded' && item.id);
  const hasFailedUploads = state.pendingAttachments.some((item) => item.status === 'error');
  const hasUploadingFiles = state.pendingAttachments.some((item) => item.status === 'uploading');
  const oversizedAttachment = state.pendingAttachments.find((item) => Number(item?.size || 0) > MAX_ATTACHMENT_SIZE_BYTES);

  if (!trimmedPrompt && uploadedAttachments.length === 0) return;
  if (!state.selectedModel) {
    try {
      await fetchModels();
    } catch (error) {
      showChatError(String(error.message || 'No model is currently available.'));
      return;
    }
    if (!state.selectedModel) {
      showChatError('No model is currently available.');
      return;
    }
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
    showDraftChat();
  }

  const optimisticSessionId = getActivePersistentSessionId();
  const draftSessionId = String(state.activeSession?.id || '').trim();
  const currentSessionId = optimisticSessionId || draftSessionId;
  if (currentSessionId && isSessionBusy(currentSessionId)) {
    showChatError('This conversation is already responding.');
    return;
  }

  showChatError('');

  const userMessage = {
    id: normalizeDisplayMessageId(uuid()),
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
    id: normalizeDisplayMessageId(uuid()),
    role: 'assistant',
    content: '',
    reasoningContent: '',
    toolCalls: [],
    progressLines: [],
    timestamp: nowSeconds(),
    done: false,
    files: [],
  };

  const targetMessages = [...state.messages, userMessage, assistantMessage];
  state.messages = targetMessages;
  state.streamingMessageIds.add(assistantMessage.id);
  state.pendingAttachments = [];
  setLiveSessionMessages(currentSessionId, targetMessages);
  setSessionBusy(currentSessionId, true, { transport: 'tui' });
  const bridgeReadyPromise = ensureTuiBridge().catch(() => null);
  renderWorkspace();

  let persistentSessionId = optimisticSessionId;
  try {
    const requestedSessionId = (!state.activeSession || state.activeSession?.isDraft || !optimisticSessionId)
      ? 'draft'
      : optimisticSessionId;
    const response = await api(`/api/sessions/${encodeURIComponent(requestedSessionId)}/turns`, {
      method: 'POST',
      body: JSON.stringify({
        prompt: trimmedPrompt,
        attachments: uploadedAttachments.map((item) => ({
          type: item.type,
          id: item.id,
          name: item.name,
          size: item.size,
          content_type: item.content_type,
          localPath: item.localPath,
        })),
        draft_title: deriveDraftTitleFromUserMessage({
          content: trimmedPrompt,
          files: uploadedAttachments,
        }),
      }),
    });
    const json = await response.json();
    const nextSession = normalizeSessionSnapshot(
      json?.session
        ? { ...json.session, persistentSessionId: json.session.id }
        : null
    );
    const nextMessages = Array.isArray(json?.messages)
      ? json.messages.map(normalizeMessageForDisplay)
      : targetMessages;

    if (!nextSession?.id) {
      throw new Error('Failed to persist session state');
    }

    persistentSessionId = String(nextSession.id || '').trim();
    if (draftSessionId && persistentSessionId && draftSessionId !== persistentSessionId) {
      clearLiveSessionMessages(draftSessionId);
      sessionAbortControllersById.delete(draftSessionId);
      sessionRunTransportById.delete(draftSessionId);
      busySessionIds.delete(draftSessionId);
    }

    state.sessions = [nextSession, ...state.sessions.filter((chat) => chat.id !== nextSession.id)];
    state.draftSession = null;
    state.activeSession = nextSession;
    state.activeSessionId = nextSession.id;
    activePersistentSessionId = nextSession.id;
    setLiveSessionMessages(nextSession.id, nextMessages);
    state.messages = nextMessages;

    const liveState = json?.live || json?.session?.live || null;
    applyLiveStateToSession(nextSession.id, liveState);
    syncStreamingAssistantStateForSession(nextSession.id, {
      liveState,
      nextMessages,
      previousMessages: targetMessages,
    });

    const liveSessionId = String(liveState?.live_session_id || '').trim();
    if (liveSessionId) {
      updateActiveTuiSessionMapping({
        liveSessionId,
        persistentSessionId: nextSession.id,
      });
      bridgeReadyPromise.catch(() => {});
    }

    renderWorkspace();
  } catch (error) {
    state.streamingMessageIds.delete(assistantMessage.id);
    assistantMessage.done = true;
    assistantMessage.timestamp = nowSeconds();
    assistantMessage.content = `[Error] ${String(error.message || error)}`;
    setLiveSessionMessages(persistentSessionId || currentSessionId, targetMessages);
    setSessionBusy(persistentSessionId || currentSessionId, false);
    if (draftSessionId && persistentSessionId && draftSessionId !== persistentSessionId) {
      setSessionBusy(draftSessionId, false);
    }
    renderWorkspace();
    throw error;
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
  setAuthViewMode('home');
};

const initializeWorkspaceData = async () => {
  let firstError = null;

  const modelsPromise = fetchModels().catch((error) => {
    firstError = firstError || error;
  });

  const sessionsPromise = (async () => {
    try {
      await refreshSessions();
      if (state.sessions.length > 0) {
        await openSession(state.sessions[0].id);
      } else {
        state.sessionHistoryLoading = false;
        showDraftChat();
      }
    } catch (error) {
      state.sessionHistoryLoading = false;
      firstError = firstError || error;
    }
  })();

  const filesPromise = fetchWorkspaceFiles().catch((error) => {
    firstError = firstError || error;
  });

  await Promise.all([modelsPromise, sessionsPromise, filesPromise]);

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

dom.authBackButton?.addEventListener('click', () => {
  setAuthViewMode('home');
});

dom.switchRegisterButton?.addEventListener('click', () => {
  setAuthViewMode('register');
});

dom.registerBackButton?.addEventListener('click', () => {
  setAuthViewMode('home');
});

dom.switchLoginButton?.addEventListener('click', () => {
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
  resetTuiBridgeReconnectState();
  closeTuiBridge();
  activeTuiSessionId = '';
  activePersistentSessionId = '';
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
  setAuthViewMode('signin');
});

dom.newChatButton.addEventListener('click', () => {
  activeTuiSessionId = '';
  activePersistentSessionId = '';
  startNewChat().catch((error) => showChatError(error.message));
});

dom.modelSelect?.addEventListener('change', (event) => {
  switchActiveModel(event.target.value).catch((error) => showChatError(error.message));
});

dom.composerForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.isSending) return;
  const prompt = dom.promptInput.value;
  dom.promptInput.value = '';
  autoResizePromptInput();
  await submitPromptViaTuiBridge(prompt);
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

dom.messages?.addEventListener('scroll', () => {
  state.shouldAutoScrollMessages = isScrolledNearBottom(dom.messages);
});

dom.sendButton.addEventListener('click', async (event) => {
  if (!state.isSending) return;
  event.preventDefault();
  event.stopPropagation();
  const activeSessionId = getActivePersistentSessionId();
  if (!activeSessionId) return;
  if (interruptingSessionIds.has(activeSessionId)) return;
  interruptingSessionIds.add(activeSessionId);
  try {
    const response = await api(`/api/sessions/${encodeURIComponent(activeSessionId)}/interrupt`, { method: 'POST' });
    const json = await response.json().catch(() => ({}));
    markSessionInterruptedLocally(activeSessionId, json?.live || null);
    renderWorkspace();
  } catch (error) {
    showChatError(String(error.message || 'Failed to interrupt TUI session'));
  } finally {
    interruptingSessionIds.delete(activeSessionId);
  }
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
