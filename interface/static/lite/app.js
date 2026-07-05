const state = {
  user: null,
  pendingWorkspaceUser: null,
  models: [],
  selectedModel: null,
  sessions: [],
  sessionsNextOffset: 0,
  sessionsHasMore: false,
  sessionsLoadingMore: false,
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
  activeWorkspaceTab: 'chat',
  filePreviewTabs: [],
  streamingMessageIds: new Set(),
  pendingAttachments: [],
  composerMode: 'chat',
  isSending: false,
  chatErrorTimer: null,
  authPollTimer: null,
  signupJobId: null,
  signupPollTimer: null,
  emailVerificationId: '',
  emailVerificationEmail: '',
  emailVerificationExpiresAt: 0,
  emailVerificationResendAt: 0,
  emailVerificationTimer: null,
  emailVerificationSending: false,
  passwordResetVerificationId: '',
  passwordResetEmail: '',
  passwordResetExpiresAt: 0,
  passwordResetResendAt: 0,
  passwordResetTimer: null,
  passwordResetSending: false,
  passwordResetSubmitting: false,
  pendingApproval: null,
  approvalSubmitting: false,
  updateNotes: null,
  pendingSessionPromise: null,
  shouldAutoScrollMessages: true,
  liveSessionMessages: new Map(),
  sessionScrollPositions: new Map(),
  pendingMessageScrollRestore: null,
  sessionHistoryLoading: false,
  renamingSessionId: null,
  renamingTitleDraft: '',
  renamingTitleError: '',
  mobileOverlayPanel: null,
  passwordChangeSubmitting: false,
};

const MODEL_RESPONSE_ERROR_MESSAGE = '模型响应失败，请稍后重试。';
const SESSION_EXPIRED_MESSAGE = 'Workspace slept after inactivity. Please sign in again.';
const COMMON_PASSWORD_SYMBOLS = '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~';
const PASSWORD_COMPLEXITY_MESSAGE = (
  'Password must be at least 8 characters and include uppercase letters, lowercase letters, numbers, and common symbols.'
);

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
  forgotPasswordButton: document.getElementById('forgot-password-button'),
  passwordResetForm: document.getElementById('password-reset-form'),
  passwordResetEmail: document.getElementById('password-reset-email'),
  passwordResetEmailCode: document.getElementById('password-reset-email-code'),
  sendPasswordResetCodeButton: document.getElementById('send-password-reset-code-button'),
  passwordResetCodeStatus: document.getElementById('password-reset-code-status'),
  passwordResetNewPassword: document.getElementById('password-reset-new-password'),
  passwordResetConfirmPassword: document.getElementById('password-reset-confirm-password'),
  passwordResetSuccess: document.getElementById('password-reset-success'),
  passwordResetError: document.getElementById('password-reset-error'),
  passwordResetNavActions: document.getElementById('password-reset-nav-actions'),
  passwordResetBackButton: document.getElementById('password-reset-back-button'),
  passwordResetLoginButton: document.getElementById('password-reset-login-button'),
  registerForm: document.getElementById('register-form'),
  registerError: document.getElementById('register-error'),
  registerEmail: document.getElementById('register-email'),
  registerEmailCode: document.getElementById('register-email-code'),
  sendEmailCodeButton: document.getElementById('send-email-code-button'),
  registerCodeStatus: document.getElementById('register-code-status'),
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
  workspaceTabs: document.getElementById('workspace-tabs'),
  workspaceTabPanels: document.getElementById('workspace-tab-panels'),
  chatTabPanel: document.getElementById('chat-tab-panel'),
  filePreviewPanels: document.getElementById('file-preview-panels'),
  messages: document.getElementById('messages'),
  tuiBridgeStatus: document.getElementById('tui-bridge-status'),
  chatTitle: document.getElementById('chat-title'),
  mobileChatsButton: document.getElementById('mobile-chats-button'),
  mobileFilesButton: document.getElementById('mobile-files-button'),
  mobilePanelBackdrop: document.getElementById('mobile-panel-backdrop'),
  modelName: document.getElementById('model-name'),
  modelSelect: document.getElementById('model-select'),
  composerForm: document.getElementById('composer-form'),
  promptInput: document.getElementById('prompt-input'),
  fileInput: document.getElementById('file-input'),
  attachButton: document.getElementById('attach-button'),
  planButton: document.getElementById('plan-button'),
  attachmentList: document.getElementById('attachment-list'),
  sendButton: document.getElementById('send-button'),
  sendButtonIcon: document.getElementById('send-button-icon'),
  newChatButton: document.getElementById('new-chat-button'),
  changePasswordButton: document.getElementById('change-password-button'),
  logoutButton: document.getElementById('logout-button'),
  userName: document.getElementById('user-name'),
  userEmail: document.getElementById('user-email'),
  updateNotesButton: document.getElementById('update-notes-button'),
  updateNotesBadge: document.getElementById('update-notes-badge'),
  updateNotesBackdrop: document.getElementById('update-notes-backdrop'),
  updateNotesPanel: document.getElementById('update-notes-panel'),
  updateNotesTitle: document.getElementById('update-notes-title'),
  updateNotesDate: document.getElementById('update-notes-date'),
  updateNotesSummary: document.getElementById('update-notes-summary'),
  updateNotesList: document.getElementById('update-notes-list'),
  updateNotesClose: document.getElementById('update-notes-close'),
  sidebarSettingsButton: document.querySelector('.sidebar-settings-button'),
  sidebarSettingsMenu: document.getElementById('sidebar-settings-menu'),
  sidebarChangePasswordButton: document.getElementById('sidebar-change-password-button'),
  sidebarSignOutButton: document.getElementById('sidebar-sign-out-button'),
  chatSidebar: document.getElementById('chat-sidebar'),
  filesPanel: document.getElementById('workspace-files-panel'),
  fileTree: document.getElementById('file-tree'),
  cwdLabel: document.getElementById('cwd-label'),
  refreshFilesButton: document.getElementById('refresh-files-button'),
  fileOpenControls: document.getElementById('file-open-controls'),
  filePathDisplay: document.getElementById('file-path-display'),
  filePathPrefix: document.getElementById('file-path-prefix'),
  filePathTail: document.getElementById('file-path-tail'),
  filePathInput: document.getElementById('file-path-input'),
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
  passwordModal: document.getElementById('password-modal'),
  passwordBackdrop: document.getElementById('password-backdrop'),
  passwordForm: document.getElementById('password-form'),
  currentPassword: document.getElementById('current-password'),
  newPassword: document.getElementById('new-password'),
  confirmNewPassword: document.getElementById('confirm-new-password'),
  passwordSuccess: document.getElementById('password-success'),
  passwordError: document.getElementById('password-error'),
  passwordCancelButton: document.getElementById('password-cancel-button'),
  passwordSaveButton: document.getElementById('password-save-button'),
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
const liveSessionPollingSessionIds = new Set();
const liveSessionPollTimersBySessionId = new Map();
const liveSessionPollInFlightBySessionId = new Set();
const liveSessionPollFailuresBySessionId = new Map();
const liveSessionPollGenerationBySessionId = new Map();
let liveSessionPollGenerationCounter = 0;
let fileTreeRefreshTimer = null;
let fileTreeRefreshInFlight = false;
let fileTreeRefreshPending = false;
let fileTreeLastFocusRefreshAt = 0;

const SIDEBAR_WIDTH_KEY = 'lite_sidebar_width';
const FILES_WIDTH_KEY = 'lite_files_width';
const THEME_MODE_KEY = 'lite_theme_mode';
const UPDATE_NOTES_PATH = './static/lite/update-notes.json';
const UPDATE_NOTES_SEEN_KEY = 'lite_update_notes_seen_version';
const UPDATE_NOTES_VISIBLE_LIMIT = 5;
const CHAT_TAB_ID = 'chat';
const MAX_TOTAL_ATTACHMENT_SIZE_BYTES = 200 * 1024 * 1024;
const AUTH_POLL_INTERVAL_MS = 60 * 1000;
const TUI_BRIDGE_RECONNECT_BASE_DELAY_MS = 1000;
const TUI_BRIDGE_RECONNECT_MAX_DELAY_MS = 15000;
const LIVE_SESSION_POLL_INTERVAL_MS = 2000;
const LIVE_SESSION_POLL_FAILURE_DELAY_MS = 5000;
const FILE_TREE_REFRESH_DEBOUNCE_MS = 800;
const FILE_TREE_FOCUS_REFRESH_MIN_INTERVAL_MS = 5000;
const FILE_TREE_REFRESH_MAX_CONCURRENCY = 3;
const ATTACHMENT_BLOCK_START = '<potato-files>';
const ATTACHMENT_BLOCK_END = '</potato-files>';
const ATTACHMENT_HINT_LINE = 'Use the attachment local paths above if you need to inspect the files.';
const ICON_SEND_PATH = './static/lite/icons/send.png';
const ICON_STOP_PATH = './static/lite/icons/stop.png';
const ICON_COPY_PATH = './static/lite/icons/copy_button.png';
const ICON_COPIED_PATH = './static/lite/icons/copied.png';
const MESSAGE_AUTO_SCROLL_THRESHOLD_PX = 48;
const INITIAL_SESSION_PAGE_SIZE = 50;
const SESSION_LOAD_MORE_PAGE_SIZE = 10;
const TUI_DEBUG_STATUS_STORAGE_KEY = 'lite_tui_bridge_debug';
const EMAIL_VERIFICATION_COUNTDOWN_INTERVAL_MS = 1000;
const MOBILE_PANEL_MEDIA_QUERY = '(max-width: 1180px)';
const mobilePanelMediaQuery = typeof window.matchMedia === 'function'
  ? window.matchMedia(MOBILE_PANEL_MEDIA_QUERY)
  : { matches: false };

const applyThemeMode = () => {
  document.documentElement.dataset.themeMode = 'light';
  document.documentElement.dataset.theme = 'light';
};

const initThemeControls = () => {
  localStorage.removeItem(THEME_MODE_KEY);
  applyThemeMode();
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

const isMobilePanelLayout = () => mobilePanelMediaQuery.matches;

const normalizeMobilePanelName = (panel) => {
  const normalized = String(panel || '').trim();
  return normalized === 'chats' || normalized === 'files' ? normalized : null;
};

const setMobilePanelInteractivity = (panel, enabled) => {
  if (!panel) return;
  if ('inert' in panel) {
    panel.inert = !enabled;
  }
  if (enabled) {
    panel.removeAttribute('aria-hidden');
  } else {
    panel.setAttribute('aria-hidden', 'true');
  }
};

const renderMobilePanelState = () => {
  const isMobile = isMobilePanelLayout();
  if (!isMobile) {
    state.mobileOverlayPanel = null;
  }
  const activePanel = isMobile ? normalizeMobilePanelName(state.mobileOverlayPanel) : null;
  const chatsOpen = activePanel === 'chats';
  const filesOpen = activePanel === 'files';
  const anyOpen = Boolean(activePanel);

  dom.workspaceView?.classList.toggle('mobile-panel-open', anyOpen);
  dom.workspaceView?.classList.toggle('mobile-chats-open', chatsOpen);
  dom.workspaceView?.classList.toggle('mobile-files-open', filesOpen);

  if (dom.mobilePanelBackdrop) {
    dom.mobilePanelBackdrop.hidden = !anyOpen;
  }

  if (dom.mobileChatsButton) {
    dom.mobileChatsButton.setAttribute('aria-expanded', chatsOpen ? 'true' : 'false');
    dom.mobileChatsButton.setAttribute('aria-label', chatsOpen ? 'Close chats' : 'Open chats');
  }
  if (dom.mobileFilesButton) {
    dom.mobileFilesButton.setAttribute('aria-expanded', filesOpen ? 'true' : 'false');
    dom.mobileFilesButton.setAttribute('aria-label', filesOpen ? 'Close files' : 'Open files');
  }

  setMobilePanelInteractivity(dom.chatSidebar, !isMobile || chatsOpen);
  setMobilePanelInteractivity(dom.filesPanel, !isMobile || filesOpen);
};

const closeMobilePanel = () => {
  state.mobileOverlayPanel = null;
  renderMobilePanelState();
};

const openMobilePanel = (panel) => {
  if (!isMobilePanelLayout()) {
    renderMobilePanelState();
    return;
  }
  const nextPanel = normalizeMobilePanelName(panel);
  if (!nextPanel) return;
  if (nextPanel !== 'chats') {
    closeSidebarSettingsMenu();
    closeUpdateNotesPanel();
  }
  state.mobileOverlayPanel = nextPanel;
  renderMobilePanelState();
};

const toggleMobilePanel = (panel) => {
  const nextPanel = normalizeMobilePanelName(panel);
  if (!nextPanel) return;
  if (state.mobileOverlayPanel === nextPanel) {
    closeMobilePanel();
    return;
  }
  openMobilePanel(nextPanel);
};

const getComposerMode = () => (state.composerMode === 'plan' ? 'plan' : 'chat');

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

const validatePasswordComplexity = (password) => {
  const candidate = String(password || '');
  if (
    candidate.length < 8
    || !/[a-z]/.test(candidate)
    || !/[A-Z]/.test(candidate)
    || !/[0-9]/.test(candidate)
    || !Array.from(COMMON_PASSWORD_SYMBOLS).some((symbol) => candidate.includes(symbol))
  ) {
    return PASSWORD_COMPLEXITY_MESSAGE;
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

const isActiveSessionBlockingModelSwitch = () => {
  const activeSessionId = getActivePersistentSessionId();
  return Boolean(activeSessionId) && (
    isSessionBusy(activeSessionId) || sessionNeedsApproval(activeSessionId)
  );
};

const MODEL_DISPLAY_NAME_OVERRIDES = {
  primary: 'GPT-5.5',
  'gpt-5.5': 'GPT-5.5',
  'gpt-5.5-alt': 'GPT-5.5-alt',
};

const MODEL_DISPLAY_ORDER = [
  'GPT-5.5',
  'GPT-5.5-alt',
  'DeepSeek',
  'Qwen3.7-Max',
];

const getModelKeyCandidates = (model) => [
  model?.id,
  model?.name,
  model?.model,
].map((value) => String(value || '').trim()).filter(Boolean);

const getModelDisplayName = (model) => {
  if (!model) return '';
  for (const key of getModelKeyCandidates(model)) {
    if (MODEL_DISPLAY_NAME_OVERRIDES[key]) {
      return MODEL_DISPLAY_NAME_OVERRIDES[key];
    }
  }
  return String(model.name || model.model || model.id || '').trim();
};

const sortModelsForDisplay = (models) => models
  .map((model, index) => ({ model, index }))
  .sort((left, right) => {
    const leftRank = MODEL_DISPLAY_ORDER.indexOf(getModelDisplayName(left.model));
    const rightRank = MODEL_DISPLAY_ORDER.indexOf(getModelDisplayName(right.model));
    const normalizedLeftRank = leftRank === -1 ? MODEL_DISPLAY_ORDER.length : leftRank;
    const normalizedRightRank = rightRank === -1 ? MODEL_DISPLAY_ORDER.length : rightRank;
    return normalizedLeftRank - normalizedRightRank || left.index - right.index;
  })
  .map(({ model }) => model);

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
  applyLiveStateToSession(normalizedSession.id, normalizedSession.live || null);
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
  stopLiveSessionPolling(persistentId);
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

const applyLiveStateToSession = (sessionId, live, { managePolling = true } = {}) => {
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

  const shouldPausePollingForApproval = status === 'awaiting_approval' && Boolean(pendingApproval);
  if (ACTIVE_LIVE_SESSION_STATUSES.has(status)) {
    if (managePolling) {
      if (shouldPausePollingForApproval) {
        stopLiveSessionPolling(persistentSessionId);
      } else {
        startLiveSessionPolling(persistentSessionId);
      }
    }
    setSessionBusy(persistentSessionId, true, { transport: 'tui' });
  } else {
    if (managePolling) {
      stopLiveSessionPolling(persistentSessionId);
    }
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
      description: String(pendingApproval.description || 'Potato Agent needs approval to continue.'),
      patternKey: '',
      patternKeys: [],
      options: [],
    });
  } else if (status !== 'awaiting_approval') {
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

const getClampedMessageScrollTop = (position) => {
  const scrollTop = Number(position?.scrollTop || 0);
  const scrollHeight = Number(position?.scrollHeight || 0);
  const clientHeight = Number(position?.clientHeight || 0);
  const maxScrollTop = Math.max(0, scrollHeight - clientHeight);
  return Math.max(0, Math.min(scrollTop, maxScrollTop));
};

const saveSessionScrollPosition = (sessionId) => {
  const normalizedSessionId = String(sessionId || '').trim();
  if (!normalizedSessionId || !dom.messages) return;
  state.sessionScrollPositions.set(normalizedSessionId, {
    scrollTop: dom.messages.scrollTop,
    scrollHeight: dom.messages.scrollHeight,
    clientHeight: dom.messages.clientHeight,
    atBottom: isScrolledNearBottom(dom.messages),
  });
};

const saveActiveSessionScrollPosition = () => {
  saveSessionScrollPosition(getActivePersistentSessionId());
};

const forgetSessionScrollPosition = (sessionId) => {
  const normalizedSessionId = String(sessionId || '').trim();
  if (!normalizedSessionId) return;
  state.sessionScrollPositions.delete(normalizedSessionId);
};

const moveSessionScrollPosition = (fromSessionId, toSessionId) => {
  const fromId = String(fromSessionId || '').trim();
  const toId = String(toSessionId || '').trim();
  if (!fromId || !toId || fromId === toId) return;
  const position = state.sessionScrollPositions.get(fromId);
  if (position) {
    state.sessionScrollPositions.set(toId, position);
    state.sessionScrollPositions.delete(fromId);
  }
};

const queueMessageScrollRestore = (sessionId) => {
  const normalizedSessionId = String(sessionId || '').trim();
  state.pendingMessageScrollRestore = normalizedSessionId || null;
};

const restorePendingMessageScrollPosition = () => {
  const sessionId = String(state.pendingMessageScrollRestore || '').trim();
  state.pendingMessageScrollRestore = null;
  if (!sessionId || !dom.messages || getActivePersistentSessionId() !== sessionId) return;

  const position = state.sessionScrollPositions.get(sessionId);
  if (!position) return;

  const atBottom = Boolean(position.atBottom);
  const nextScrollTop = atBottom
    ? dom.messages.scrollHeight
    : getClampedMessageScrollTop({
        ...position,
        scrollHeight: dom.messages.scrollHeight,
        clientHeight: dom.messages.clientHeight,
      });

  dom.messages.classList.add('restoring-scroll');
  dom.messages.scrollTop = nextScrollTop;
  state.shouldAutoScrollMessages = atBottom || isScrolledNearBottom(dom.messages);
  saveSessionScrollPosition(sessionId);
  window.requestAnimationFrame(() => {
    dom.messages?.classList.remove('restoring-scroll');
  });
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
    scheduleFileTreeRefresh('message.complete');
    return;
  }

  if (type === 'approval.request') {
    const persistentSessionId = getPersistentSessionIdFromTuiEvent(message);
    setSessionPendingApproval(persistentSessionId, {
      approvalId: '',
      command: String(message?.payload?.command || ''),
      description: String(message?.payload?.description || 'Potato Agent needs approval to continue.'),
      patternKey: '',
      patternKeys: [],
      options: [],
    });
    stopLiveSessionPolling(persistentSessionId);
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
    scheduleFileTreeRefresh('tool.complete');
    return;
  }

  if (type === 'background.complete') {
    setTuiBridgeStatus('TUI background task completed');
    scheduleFileTreeRefresh('background.complete');
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
    if (window.innerWidth <= 1180) return;
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
  if (dom.planButton) {
    const planActive = getComposerMode() === 'plan';
    dom.planButton.disabled = busy;
    dom.planButton.classList.toggle('active', planActive);
    dom.planButton.setAttribute('aria-pressed', planActive ? 'true' : 'false');
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
    dom.modelSelect.disabled = isActiveSessionBlockingModelSwitch() || state.models.length <= 1;
  }
};

const setComposerMode = (mode) => {
  state.composerMode = mode === 'plan' ? 'plan' : 'chat';
  refreshComposerBusyState();
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

  const previousSessionId = String(state.activeSessionId || '').trim();
  saveSessionScrollPosition(previousSessionId);
  state.draftSession = null;
  state.activeSession = normalizedSession;
  state.activeSessionId = normalizedSession.id;
  moveSessionScrollPosition(previousSessionId, normalizedSession.id);
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
  saveActiveSessionScrollPosition();
  activeTuiSessionId = '';
  activePersistentSessionId = '';
  state.sessionHistoryLoading = false;
  state.pendingAttachments = [];
  renderAttachments();
  state.draftSession = createDraftSession();
  state.activeSession = state.draftSession;
  state.activeSessionId = state.draftSession.id;
  state.messages = [];
  state.activeWorkspaceTab = CHAT_TAB_ID;
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

const getAttachmentTooLargeMessage = () => 'Total attachment size too large (> 200 MB).';

const escapeHtml = (text) =>
  String(text ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

const showError = (element, message) => {
  if (!element) return;
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
    dom.approvalDescription.textContent = approval.description || 'Potato Agent marked this command as dangerous and is waiting for your decision.';
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
  showError(dom.loginError, message || SESSION_EXPIRED_MESSAGE);
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
  renderEmailVerificationState();
};

const setPasswordChangeSubmitting = (submitting) => {
  state.passwordChangeSubmitting = submitting;
  if (dom.passwordSaveButton) {
    dom.passwordSaveButton.disabled = submitting;
    dom.passwordSaveButton.textContent = submitting ? 'Saving...' : 'Save';
  }
  if (dom.passwordCancelButton) {
    dom.passwordCancelButton.disabled = submitting;
  }
  for (const input of [dom.currentPassword, dom.newPassword, dom.confirmNewPassword]) {
    if (input) input.disabled = submitting;
  }
};

const clearPasswordForm = () => {
  if (dom.passwordForm) {
    dom.passwordForm.reset();
  }
  showError(dom.passwordError, '');
  showError(dom.passwordSuccess, '');
  setPasswordChangeSubmitting(false);
};

const openPasswordModal = () => {
  if (!dom.passwordModal) return;
  clearPasswordForm();
  dom.passwordModal.hidden = false;
  window.requestAnimationFrame(() => dom.currentPassword?.focus());
};

const closePasswordModal = () => {
  if (state.passwordChangeSubmitting || !dom.passwordModal) return;
  dom.passwordModal.hidden = true;
  clearPasswordForm();
};

const getUpdateNotesSeenKey = () => {
  const accountId = String(
    state.user?.email || state.user?.username || state.user?.name || 'anonymous'
  ).trim().toLowerCase() || 'anonymous';
  return `${UPDATE_NOTES_SEEN_KEY}:${encodeURIComponent(accountId)}`;
};

const getSeenUpdateNotesVersion = () => {
  try {
    return localStorage.getItem(getUpdateNotesSeenKey()) || '';
  } catch {
    return '';
  }
};

const markUpdateNotesSeen = () => {
  const version = String(state.updateNotes?.version || '').trim();
  if (!version) return;
  try {
    localStorage.setItem(getUpdateNotesSeenKey(), version);
  } catch {
    // Ignore localStorage failures; the badge can reappear in storage-restricted browsers.
  }
  renderUpdateNotesUnreadState();
};

const getUpdateNotesSortValue = (update, index) => {
  const version = String(update?.version || '').trim();
  const versionMatch = version.match(/^(\d{4})-(\d{2})-(\d{2})(?:-(\d+))?/);
  if (versionMatch) {
    const [, year, month, day, sequence] = versionMatch;
    const timestamp = Date.UTC(Number(year), Number(month) - 1, Number(day));
    const numericSequence = Number(sequence || 0);
    if (Number.isFinite(timestamp) && Number.isFinite(numericSequence)) {
      return timestamp * 1000 + numericSequence;
    }
  }

  const parsedDate = Date.parse(String(update?.date || '').trim());
  if (Number.isFinite(parsedDate)) {
    return parsedDate * 1000;
  }

  return -index;
};

const normalizeUpdateNotesPayload = (payload) => {
  const updates = Array.isArray(payload?.updates) ? payload.updates : [];
  const normalizedUpdates = updates
    .map((update, index) => {
      const version = String(update?.version || '').trim();
      if (!update || !version) return null;
      const items = Array.isArray(update.items)
        ? update.items.map((item) => String(item || '').trim()).filter(Boolean)
        : [];
      return {
        version,
        title: String(update.title || 'Workspace updates').trim() || 'Workspace updates',
        date: String(update.date || '').trim(),
        summary: String(update.summary || '').trim(),
        items,
        sortValue: getUpdateNotesSortValue(update, index),
      };
    })
    .filter(Boolean)
    .sort((a, b) => b.sortValue - a.sortValue)
    .slice(0, UPDATE_NOTES_VISIBLE_LIMIT)
    .map(({ sortValue, ...update }) => update);

  const latest = normalizedUpdates[0];
  if (!latest) return null;
  return {
    version: latest.version,
    title: 'Workspace updates',
    date: '',
    summary: '',
    updates: normalizedUpdates,
  };
};

const renderUpdateNotesContent = () => {
  if (!dom.updateNotesPanel || !state.updateNotes) return;
  if (dom.updateNotesTitle) dom.updateNotesTitle.textContent = state.updateNotes.title;
  if (dom.updateNotesDate) {
    dom.updateNotesDate.textContent = state.updateNotes.date;
    dom.updateNotesDate.hidden = !state.updateNotes.date;
  }
  if (dom.updateNotesSummary) {
    dom.updateNotesSummary.textContent = state.updateNotes.summary;
    dom.updateNotesSummary.hidden = !state.updateNotes.summary;
  }
  if (!dom.updateNotesList) return;
  dom.updateNotesList.innerHTML = '';
  const updates = Array.isArray(state.updateNotes.updates) ? state.updateNotes.updates : [];
  dom.updateNotesList.hidden = updates.length === 0;
  for (const update of updates) {
    const section = document.createElement('li');
    section.className = 'update-notes-entry';

    const heading = document.createElement('div');
    heading.className = 'update-notes-entry-heading';

    const title = document.createElement('div');
    title.className = 'update-notes-entry-title';
    title.textContent = update.title;
    heading.append(title);

    if (update.date) {
      const date = document.createElement('div');
      date.className = 'update-notes-entry-date';
      date.textContent = update.date;
      heading.append(date);
    }

    section.append(heading);

    if (update.summary) {
      const summary = document.createElement('p');
      summary.className = 'update-notes-entry-summary';
      summary.textContent = update.summary;
      section.append(summary);
    }

    if (update.items.length > 0) {
      const itemList = document.createElement('ul');
      itemList.className = 'update-notes-entry-items';
      for (const item of update.items) {
        const element = document.createElement('li');
        element.textContent = item;
        itemList.append(element);
      }
      section.append(itemList);
    }

    dom.updateNotesList.append(section);
  }
};

const renderUpdateNotesUnreadState = () => {
  const version = String(state.updateNotes?.version || '').trim();
  const unread = Boolean(version) && getSeenUpdateNotesVersion() !== version;
  if (dom.updateNotesButton) {
    dom.updateNotesButton.hidden = !version;
  }
  if (dom.updateNotesBadge) {
    dom.updateNotesBadge.hidden = !unread;
  }
  dom.updateNotesButton?.classList.toggle('has-unread', unread);
};

const openUpdateNotesPanel = () => {
  if (!dom.updateNotesPanel) return;
  renderUpdateNotesContent();
  closeSidebarSettingsMenu();
  if (dom.updateNotesBackdrop) {
    dom.updateNotesBackdrop.hidden = false;
  }
  dom.updateNotesPanel.hidden = false;
  dom.updateNotesButton?.setAttribute('aria-expanded', 'true');
  window.requestAnimationFrame(() => dom.updateNotesClose?.focus());
};

const closeUpdateNotesPanel = ({ markSeen = true } = {}) => {
  if (!dom.updateNotesPanel || dom.updateNotesPanel.hidden) return;
  dom.updateNotesPanel.hidden = true;
  if (dom.updateNotesBackdrop) {
    dom.updateNotesBackdrop.hidden = true;
  }
  dom.updateNotesButton?.setAttribute('aria-expanded', 'false');
  if (markSeen) {
    markUpdateNotesSeen();
  }
};

const toggleUpdateNotesPanel = () => {
  if (!dom.updateNotesPanel) return;
  if (dom.updateNotesPanel.hidden) {
    openUpdateNotesPanel();
    return;
  }
  closeUpdateNotesPanel();
};

const loadUpdateNotes = async () => {
  try {
    const response = await fetch(UPDATE_NOTES_PATH, {
      method: 'GET',
      cache: 'no-store',
      credentials: 'same-origin',
    });
    if (!response.ok) {
      throw new Error(`Failed to load update notes (${response.status})`);
    }
    state.updateNotes = normalizeUpdateNotesPayload(await response.json());
  } catch {
    state.updateNotes = null;
  }
  renderUpdateNotesContent();
  renderUpdateNotesUnreadState();
};

const initUpdateNotes = () => {
  if (dom.updateNotesButton) {
    dom.updateNotesButton.hidden = true;
  }
  renderUpdateNotesUnreadState();
  dom.updateNotesBackdrop?.setAttribute('hidden', '');
  dom.updateNotesPanel?.setAttribute('hidden', '');
  dom.updateNotesButton?.setAttribute('aria-expanded', 'false');
  loadUpdateNotes();
};

const closeSidebarSettingsMenu = () => {
  if (!dom.sidebarSettingsMenu) return;
  dom.sidebarSettingsMenu.hidden = true;
  dom.sidebarSettingsButton?.setAttribute('aria-expanded', 'false');
};

const toggleSidebarSettingsMenu = () => {
  if (!dom.sidebarSettingsMenu) return;
  const shouldOpen = dom.sidebarSettingsMenu.hidden;
  if (shouldOpen) {
    closeUpdateNotesPanel();
  }
  dom.sidebarSettingsMenu.hidden = !shouldOpen;
  dom.sidebarSettingsButton?.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
};

const performSignOut = async () => {
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
  closeSidebarSettingsMenu();
  showLogin();
};

const handlePasswordChangeSubmit = async (event) => {
  event.preventDefault();
  if (state.passwordChangeSubmitting) return;

  const currentPassword = dom.currentPassword?.value || '';
  const newPassword = dom.newPassword?.value || '';
  const confirmNewPassword = dom.confirmNewPassword?.value || '';
  showError(dom.passwordError, '');
  showError(dom.passwordSuccess, '');

  if (!currentPassword || !newPassword) {
    showError(dom.passwordError, 'Current password and new password are required.');
    return;
  }
  const passwordValidationMessage = validatePasswordComplexity(newPassword);
  if (passwordValidationMessage) {
    showError(dom.passwordError, passwordValidationMessage);
    return;
  }
  if (newPassword !== confirmNewPassword) {
    showError(dom.passwordError, 'Passwords do not match.');
    return;
  }

  try {
    setPasswordChangeSubmitting(true);
    const response = await api('/api/auth/password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
    const json = await response.json();
    if (json?.user) {
      state.user = json.user;
      renderWorkspaceHeader();
    }
    if (dom.passwordForm) {
      dom.passwordForm.reset();
    }
    showError(dom.passwordSuccess, 'Password changed.');
  } catch (error) {
    showError(dom.passwordError, String(error.message || 'Failed to change password'));
  } finally {
    setPasswordChangeSubmitting(false);
  }
};

const stopEmailVerificationTimer = () => {
  if (state.emailVerificationTimer) {
    window.clearInterval(state.emailVerificationTimer);
    state.emailVerificationTimer = null;
  }
};

const setRegisterCodeStatus = (message) => {
  if (!dom.registerCodeStatus) return;
  dom.registerCodeStatus.hidden = !message;
  dom.registerCodeStatus.textContent = message || '';
};

const renderEmailVerificationState = () => {
  const button = dom.sendEmailCodeButton;
  if (!button) return;

  const now = nowSeconds();
  const cooldown = Math.max(0, Number(state.emailVerificationResendAt || 0) - now);
  const expiresIn = Math.max(0, Number(state.emailVerificationExpiresAt || 0) - now);

  button.disabled = Boolean(state.emailVerificationSending || signupInFlight || cooldown > 0);
  if (state.emailVerificationSending) {
    button.textContent = 'Sending...';
  } else if (cooldown > 0) {
    button.textContent = `${cooldown}s`;
  } else if (state.emailVerificationId) {
    button.textContent = 'Resend code';
  } else {
    button.textContent = 'Send code';
  }

  if (state.emailVerificationId && state.emailVerificationEmail && expiresIn > 0) {
    const minutes = Math.max(1, Math.ceil(expiresIn / 60));
    setRegisterCodeStatus(`Code sent to ${state.emailVerificationEmail}. Expires in ${minutes} min.`);
  } else if (state.emailVerificationId && state.emailVerificationEmail) {
    setRegisterCodeStatus('Code expired. Send a new code.');
  } else {
    setRegisterCodeStatus('');
  }

  if ((cooldown > 0 || expiresIn > 0) && !state.emailVerificationTimer) {
    state.emailVerificationTimer = window.setInterval(
      renderEmailVerificationState,
      EMAIL_VERIFICATION_COUNTDOWN_INTERVAL_MS
    );
  }
  if (cooldown <= 0 && expiresIn <= 0) {
    stopEmailVerificationTimer();
  }
};

const clearEmailVerificationState = ({ clearCode = true } = {}) => {
  stopEmailVerificationTimer();
  state.emailVerificationId = '';
  state.emailVerificationEmail = '';
  state.emailVerificationExpiresAt = 0;
  state.emailVerificationResendAt = 0;
  state.emailVerificationSending = false;
  if (clearCode && dom.registerEmailCode) {
    dom.registerEmailCode.value = '';
  }
  renderEmailVerificationState();
};

const handleSendEmailVerification = async () => {
  if (state.emailVerificationSending || signupInFlight) return;
  const email = dom.registerEmail.value.trim();
  showError(dom.registerError, '');

  if (!email) {
    showError(dom.registerError, 'Email is required.');
    return;
  }

  state.emailVerificationSending = true;
  renderEmailVerificationState();
  try {
    const response = await api('/api/auth/signup/email-verifications', {
      method: 'POST',
      body: JSON.stringify({ email }),
    });
    const json = await response.json();
    state.emailVerificationId = String(json?.verification_id || '');
    state.emailVerificationEmail = email.toLowerCase();
    state.emailVerificationExpiresAt = Number(json?.expires_at || 0);
    state.emailVerificationResendAt = nowSeconds() + Number(json?.resend_after || 60);
    if (dom.registerEmailCode) {
      dom.registerEmailCode.value = '';
      dom.registerEmailCode.focus();
    }
  } catch (error) {
    clearEmailVerificationState({ clearCode: false });
    showError(dom.registerError, String(error.message || 'Failed to send verification code'));
  } finally {
    state.emailVerificationSending = false;
    renderEmailVerificationState();
  }
};

const stopPasswordResetTimer = () => {
  if (state.passwordResetTimer) {
    window.clearInterval(state.passwordResetTimer);
    state.passwordResetTimer = null;
  }
};

const setPasswordResetCodeStatus = (message) => {
  if (!dom.passwordResetCodeStatus) return;
  dom.passwordResetCodeStatus.hidden = !message;
  dom.passwordResetCodeStatus.textContent = message || '';
};

const renderPasswordResetState = () => {
  const button = dom.sendPasswordResetCodeButton;
  if (!button) return;

  const now = nowSeconds();
  const cooldown = Math.max(0, Number(state.passwordResetResendAt || 0) - now);
  const expiresIn = Math.max(0, Number(state.passwordResetExpiresAt || 0) - now);

  button.disabled = Boolean(
    state.passwordResetSending
    || state.passwordResetSubmitting
    || cooldown > 0
  );
  if (state.passwordResetSending) {
    button.textContent = 'Sending...';
  } else if (cooldown > 0) {
    button.textContent = `${cooldown}s`;
  } else if (state.passwordResetVerificationId) {
    button.textContent = 'Resend code';
  } else {
    button.textContent = 'Send code';
  }

  if (state.passwordResetVerificationId && expiresIn > 0) {
    const minutes = Math.max(1, Math.ceil(expiresIn / 60));
    setPasswordResetCodeStatus(`If this email belongs to an active account, a code was sent. Expires in ${minutes} min.`);
  } else if (state.passwordResetVerificationId) {
    setPasswordResetCodeStatus('Code expired. Send a new code.');
  } else {
    setPasswordResetCodeStatus('');
  }

  if ((cooldown > 0 || expiresIn > 0) && !state.passwordResetTimer) {
    state.passwordResetTimer = window.setInterval(
      renderPasswordResetState,
      EMAIL_VERIFICATION_COUNTDOWN_INTERVAL_MS
    );
  }
  if (cooldown <= 0 && expiresIn <= 0) {
    stopPasswordResetTimer();
  }
};

const clearPasswordResetState = ({ clearCode = true, clearMessages = true } = {}) => {
  stopPasswordResetTimer();
  state.passwordResetVerificationId = '';
  state.passwordResetEmail = '';
  state.passwordResetExpiresAt = 0;
  state.passwordResetResendAt = 0;
  state.passwordResetSending = false;
  if (clearCode && dom.passwordResetEmailCode) {
    dom.passwordResetEmailCode.value = '';
  }
  if (clearMessages) {
    showError(dom.passwordResetError, '');
    showError(dom.passwordResetSuccess, '');
  }
  renderPasswordResetState();
};

const setPasswordResetSubmitting = (submitting) => {
  state.passwordResetSubmitting = submitting;
  const submitButton = dom.passwordResetForm?.querySelector('button[type="submit"]');
  if (submitButton) {
    submitButton.disabled = submitting;
    submitButton.textContent = submitting ? 'Resetting...' : 'Reset password';
  }
  for (const input of [
    dom.passwordResetEmail,
    dom.passwordResetEmailCode,
    dom.passwordResetNewPassword,
    dom.passwordResetConfirmPassword,
  ]) {
    if (input) input.disabled = submitting;
  }
  if (dom.passwordResetBackButton) dom.passwordResetBackButton.disabled = submitting;
  if (dom.passwordResetLoginButton) dom.passwordResetLoginButton.disabled = submitting;
  renderPasswordResetState();
};

const handleSendPasswordResetVerification = async () => {
  if (state.passwordResetSending || state.passwordResetSubmitting) return;
  const email = dom.passwordResetEmail?.value.trim() || '';
  showError(dom.passwordResetError, '');
  showError(dom.passwordResetSuccess, '');

  if (!email) {
    showError(dom.passwordResetError, 'Email is required.');
    return;
  }

  state.passwordResetSending = true;
  renderPasswordResetState();
  try {
    const response = await api('/api/auth/password-reset/email-verifications', {
      method: 'POST',
      body: JSON.stringify({ email }),
    });
    const json = await response.json();
    state.passwordResetVerificationId = String(json?.verification_id || '');
    state.passwordResetEmail = email.toLowerCase();
    state.passwordResetExpiresAt = Number(json?.expires_at || 0);
    state.passwordResetResendAt = nowSeconds() + Number(json?.resend_after || 60);
    if (dom.passwordResetEmailCode) {
      dom.passwordResetEmailCode.value = '';
      dom.passwordResetEmailCode.focus();
    }
  } catch (error) {
    clearPasswordResetState({ clearCode: false, clearMessages: false });
    showError(dom.passwordResetError, String(error.message || 'Failed to send reset code'));
  } finally {
    state.passwordResetSending = false;
    renderPasswordResetState();
  }
};

const handlePasswordResetSubmit = async (event) => {
  event.preventDefault();
  if (state.passwordResetSubmitting) return;

  const email = dom.passwordResetEmail?.value.trim() || '';
  const emailVerificationCode = dom.passwordResetEmailCode?.value.trim() || '';
  const newPassword = dom.passwordResetNewPassword?.value || '';
  const confirmPassword = dom.passwordResetConfirmPassword?.value || '';
  showError(dom.passwordResetError, '');
  showError(dom.passwordResetSuccess, '');

  if (!state.passwordResetVerificationId || state.passwordResetEmail !== email.toLowerCase()) {
    showError(dom.passwordResetError, 'Send a reset code to this email first.');
    return;
  }
  if (state.passwordResetExpiresAt && state.passwordResetExpiresAt <= nowSeconds()) {
    showError(dom.passwordResetError, 'Verification code has expired. Send a new code.');
    return;
  }
  if (!/^\d{6}$/.test(emailVerificationCode)) {
    showError(dom.passwordResetError, 'Verification code must be 6 digits.');
    return;
  }
  const passwordValidationMessage = validatePasswordComplexity(newPassword);
  if (passwordValidationMessage) {
    showError(dom.passwordResetError, passwordValidationMessage);
    return;
  }
  if (newPassword !== confirmPassword) {
    showError(dom.passwordResetError, 'Passwords do not match.');
    return;
  }

  try {
    setPasswordResetSubmitting(true);
    await api('/api/auth/password-reset', {
      method: 'POST',
      body: JSON.stringify({
        email,
        new_password: newPassword,
        email_verification_id: state.passwordResetVerificationId,
        email_verification_code: emailVerificationCode,
      }),
    });
    if (dom.passwordResetForm) {
      dom.passwordResetForm.reset();
    }
    const loginEmail = document.getElementById('email');
    if (loginEmail) {
      loginEmail.value = email;
    }
    clearPasswordResetState({ clearMessages: false });
    showError(dom.passwordResetSuccess, 'Password reset. Sign in with your new password.');
  } catch (error) {
    showError(dom.passwordResetError, String(error.message || 'Failed to reset password'));
  } finally {
    setPasswordResetSubmitting(false);
  }
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
  const showPasswordReset = mode === 'password-reset';
  const showRegister = mode === 'register';
  const showWait = mode === 'signup-wait';
  const showRuntimeStart = mode === 'runtime-start';

  if (dom.authCard) {
    dom.authCard.dataset.authMode = mode;
  }

  if (showHome) {
    showError(dom.loginError, '');
    showError(dom.registerError, '');
    showError(dom.passwordResetError, '');
    showError(dom.passwordResetSuccess, '');
  }

  if (showSignin) {
    dom.authCardLabel.textContent = 'Sign in';
    dom.authCardTitle.textContent = 'Enter Potato Agent';
    dom.authCardCopy.textContent = 'Use your account to open the isolated Potato Agent workspace assigned to you.';
  }

  if (showPasswordReset) {
    dom.authCardLabel.textContent = 'Password reset';
    dom.authCardTitle.textContent = 'Reset your password';
    dom.authCardCopy.textContent = 'Verify your email address, then choose a new password.';
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
  dom.passwordResetForm.hidden = !showPasswordReset;
  dom.passwordResetNavActions.hidden = !showPasswordReset;
  dom.registerForm.hidden = !showRegister;
  dom.registerNavActions.hidden = !showRegister;
  dom.signupWaitView.hidden = !showWait;
  dom.runtimeStartView.hidden = !showRuntimeStart;
  if (showSignin) {
    showError(dom.loginError, '');
  }
  if (showPasswordReset) {
    showError(dom.passwordResetError, '');
    showError(dom.passwordResetSuccess, '');
    renderPasswordResetState();
  }
  if (showRegister) {
    showError(dom.registerError, '');
  }
};

const resetWorkspaceState = () => {
  stopAllLiveSessionPolling();
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
  state.sessionScrollPositions.clear();
  state.pendingMessageScrollRestore = null;
  state.messages = [];
  state.activeWorkspaceTab = CHAT_TAB_ID;
  state.filePreviewTabs = [];
  state.models = [];
  state.selectedModel = null;
  state.rootPath = '';
  state.workspaceRoot = '';
  state.currentPath = '';
  state.fileBrowserMode = 'home_only';
  state.homePath = '';
  state.expandedPaths = new Set();
  state.treeCache.clear();
  if (fileTreeRefreshTimer) {
    window.clearTimeout(fileTreeRefreshTimer);
    fileTreeRefreshTimer = null;
  }
  fileTreeRefreshPending = false;
  fileTreeLastFocusRefreshAt = 0;
  state.streamingMessageIds.clear();
  busySessionIds.clear();
  sessionRunTransportById.clear();
  sessionAbortControllersById.clear();
  pendingApprovalsBySessionId.clear();
  interruptingSessionIds.clear();
  state.pendingAttachments = [];
  state.isSending = false;
  state.pendingApproval = null;
  state.mobileOverlayPanel = null;
  state.approvalSubmitting = false;
  state.passwordChangeSubmitting = false;
  state.passwordResetSubmitting = false;
  if (dom.passwordModal) {
    dom.passwordModal.hidden = true;
  }
  clearPasswordForm();
  clearPasswordResetState();
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
        json?.message || SESSION_EXPIRED_MESSAGE
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
    if (
      (response.status === 401 || response.status === 403)
      && (
        payload?.reason === 'idle_timeout'
        || payload?.reason === 'password_changed'
        || /Workspace slept/i.test(detail)
        || /Password changed/i.test(detail)
        || /Please sign in again/i.test(detail)
      )
    ) {
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

const getTotalAttachmentSize = (attachments) =>
  (attachments || []).reduce((total, item) => total + Math.max(0, Number(item?.size || 0)), 0);

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

  let nextTotalSize = getTotalAttachmentSize(state.pendingAttachments);
  const acceptedAttachments = [];
  for (const file of incoming) {
    if (!file || !file.size) {
      showChatError('Cannot upload an empty file.');
      continue;
    }

    const fileSize = Math.max(0, Number(file.size || 0));
    if (nextTotalSize + fileSize > MAX_TOTAL_ATTACHMENT_SIZE_BYTES) {
      showChatError(getAttachmentTooLargeMessage());
      continue;
    }

    const attachment = createAttachmentItem(file);
    state.pendingAttachments = [...state.pendingAttachments, attachment];
    acceptedAttachments.push({ file, attachment });
    nextTotalSize += fileSize;
  }

  if (acceptedAttachments.length) {
    renderAttachments();
  }

  for (const { file, attachment } of acceptedAttachments) {
    try {
      const uploadedJson = await uploadAttachment(file);
      const uploaded = normalizeUploadResult(uploadedJson, file);
      state.pendingAttachments = state.pendingAttachments.map((item) =>
        item.itemId === attachment.itemId ? { ...item, ...uploaded } : item
      );
      scheduleFileTreeRefresh('upload');
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
  const nextEntries = [];
  const seen = new Set();
  const candidateEntries = [
    ...(Array.isArray(message.progressLines) ? message.progressLines : []),
    ...(Array.isArray(entries) ? entries : []),
  ];
  for (const entry of candidateEntries) {
    if (!entry) continue;
    if (seen.has(entry)) continue;
    seen.add(entry);
    nextEntries.push(entry);
  }
  message.progressLines = nextEntries;
};

const mergeSnapshotMessagesWithLocalProgress = (snapshotMessages, localMessages) => {
  const normalizedSnapshot = Array.isArray(snapshotMessages) ? snapshotMessages : [];
  const normalizedLocal = Array.isArray(localMessages) ? localMessages : [];
  if (normalizedSnapshot.length === 0 || normalizedLocal.length === 0) {
    return normalizedSnapshot;
  }

  const localById = new Map();
  const localAssistantsByIndex = [];
  for (const message of normalizedLocal) {
    const messageId = String(message?.id || '').trim();
    if (messageId) {
      localById.set(messageId, message);
    }
    if (String(message?.role || '') === 'assistant') {
      localAssistantsByIndex.push(message);
    }
  }

  let assistantIndex = 0;
  return normalizedSnapshot.map((message) => {
    if (String(message?.role || '') !== 'assistant') {
      return message;
    }

    const messageId = String(message?.id || '').trim();
    const localMessage = localById.get(messageId) || localAssistantsByIndex[assistantIndex] || null;
    assistantIndex += 1;
    if (!localMessage || !Array.isArray(localMessage.progressLines) || localMessage.progressLines.length === 0) {
      return message;
    }

    const mergedMessage = {
      ...message,
      progressLines: Array.isArray(localMessage.progressLines) ? [...localMessage.progressLines] : [],
    };
    appendProgressEntries(
      mergedMessage,
      Array.isArray(message.progressLines) ? message.progressLines : []
    );
    return mergedMessage;
  });
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
  const pendingScrollRestoreSessionId = String(state.pendingMessageScrollRestore || '').trim();
  const activeScrollSessionId = getActivePersistentSessionId();
  const chatTabVisible = state.activeWorkspaceTab === CHAT_TAB_ID;
  const shouldRestoreScroll = Boolean(
    chatTabVisible
    &&
    pendingScrollRestoreSessionId
    && pendingScrollRestoreSessionId === activeScrollSessionId
    && state.sessionScrollPositions.has(pendingScrollRestoreSessionId)
  );
  const shouldStickToBottom = chatTabVisible
    && !shouldRestoreScroll
    && (state.shouldAutoScrollMessages || isScrolledNearBottom(dom.messages));
  dom.messages.innerHTML = '';
  const visibleMessages = getRenderableMessages();

  if (state.sessionHistoryLoading) {
    state.pendingMessageScrollRestore = null;
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
    if (chatTabVisible) {
      restorePendingMessageScrollPosition();
    }
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
    role.textContent = message.role === 'user' ? 'You' : 'Potato Agent';
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

  if (chatTabVisible) {
    state.shouldAutoScrollMessages = shouldStickToBottom;
    if (shouldStickToBottom) {
      dom.messages.scrollTop = dom.messages.scrollHeight;
    }
    restorePendingMessageScrollPosition();
  }
};

const getChatDisplayTitle = (chat) => {
  if (!chat) return 'New chat';
  return chat.title || chat.preview || 'New chat';
};

const compareSessionsByActivity = (left, right) => (
  (right.last_active || right.started_at || 0) - (left.last_active || left.started_at || 0)
);

const sortSessionsByActivity = (sessions) => [...sessions].sort(compareSessionsByActivity);

const buildSessionsPageUrl = (offset = 0, limit = INITIAL_SESSION_PAGE_SIZE) => {
  const params = new URLSearchParams({
    limit: String(Math.max(1, Number(limit) || INITIAL_SESSION_PAGE_SIZE)),
    offset: String(Math.max(0, Number(offset) || 0)),
  });
  return `/api/sessions?${params.toString()}`;
};

const fetchSessionsPage = async (offset = 0, limit = INITIAL_SESSION_PAGE_SIZE) => {
  const response = await api(buildSessionsPageUrl(offset, limit), { method: 'GET' });
  return response.json();
};

const mergeSessionPage = (sessions, append = false) => {
  const normalized = sessions
    .map((session) => normalizeSessionSnapshot({
      ...session,
      persistentSessionId: session.id,
    }))
    .filter(Boolean);

  if (!append) {
    state.sessions = sortSessionsByActivity(normalized);
    return normalized;
  }

  const mergedById = new Map(state.sessions.map((session) => [session.id, session]));
  for (const session of normalized) {
    mergedById.set(session.id, {
      ...(mergedById.get(session.id) || {}),
      ...session,
    });
  }
  state.sessions = sortSessionsByActivity(Array.from(mergedById.values()));
  return normalized;
};

const applySessionsPage = (json, { append = false } = {}) => {
  const sessions = Array.isArray(json?.sessions) ? json.sessions : [];
  const normalized = mergeSessionPage(sessions, append);
  for (const session of normalized) {
    applyLiveStateToSession(session?.id, session?.live || null);
  }

  const nextOffset = Number(json?.next_offset);
  state.sessionsNextOffset = Number.isFinite(nextOffset)
    ? Math.max(0, nextOffset)
    : state.sessions.length;
  state.sessionsHasMore = Boolean(json?.has_more);

  if (state.activeSessionId && !state.activeSession?.isDraft) {
    const activeSession = state.sessions.find((session) => session.id === state.activeSessionId);
    if (activeSession) {
      state.activeSession = activeSession;
    }
  }

  if (state.renamingSessionId && !state.sessions.some((session) => session.id === state.renamingSessionId)) {
    resetSessionRenameState();
  }
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
        saveActiveSessionScrollPosition();
        if (chat.isDraft) {
          state.activeSession = chat;
          state.activeSessionId = chat.id;
          closeMobilePanel();
          renderWorkspace();
          return;
        }
        resetSessionRenameState();
        closeMobilePanel();
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

  if (state.sessionsHasMore || state.sessionsLoadingMore) {
    const shell = document.createElement('div');
    shell.className = 'chat-load-more-shell';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chat-load-more-button';
    button.disabled = state.sessionsLoadingMore;
    button.textContent = state.sessionsLoadingMore ? 'Loading sessions...' : 'Load more sessions';
    button.addEventListener('click', () => {
      loadMoreSessions().catch((error) => showChatError(error.message));
    });
    shell.append(button);
    dom.chatList.append(shell);
  }
};

const updateModelSelectWidth = () => {
  if (!dom.modelSelect) return;
  const labels = Array.from(dom.modelSelect.options)
    .map((option) => String(option.textContent || '').trim())
    .filter(Boolean);
  if (labels.length === 0) {
    dom.modelSelect.style.removeProperty('--model-select-width');
    return;
  }

  const probe = document.createElement('span');
  const computed = window.getComputedStyle(dom.modelSelect);
  probe.style.position = 'fixed';
  probe.style.visibility = 'hidden';
  probe.style.whiteSpace = 'nowrap';
  probe.style.pointerEvents = 'none';
  probe.style.font = computed.font;
  document.body.append(probe);

  let maxTextWidth = 0;
  for (const label of labels) {
    probe.textContent = label;
    maxTextWidth = Math.max(maxTextWidth, probe.getBoundingClientRect().width);
  }
  probe.remove();

  const selectChromeWidth = 34;
  dom.modelSelect.style.setProperty('--model-select-width', `${Math.ceil(maxTextWidth + selectChromeWidth)}px`);
};

const renderWorkspaceHeader = () => {
  if (dom.userName) {
    dom.userName.textContent = state.user?.username || state.user?.name || state.user?.email || 'Potato workspace';
  }
  dom.userEmail.textContent = state.user?.email || '';
  renderUpdateNotesUnreadState();
  dom.chatTitle.textContent = getActiveWorkspaceTitle();
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
  dom.modelSelect.disabled = isActiveSessionBlockingModelSwitch() || state.models.length <= 1;
  updateModelSelectWidth();
};

const renderFileBrowserControls = () => {
  if (!dom.fileOpenControls) return;
  const isUserReadable = state.fileBrowserMode === 'user_readable';
  dom.fileOpenControls.hidden = false;
  if (dom.filePathDisplay) {
    dom.filePathDisplay.hidden = isUserReadable;
  }
  if (dom.filePathInput) {
    dom.filePathInput.hidden = !isUserReadable;
  }
  if (isUserReadable && dom.filePathInput && !dom.filePathInput.value.trim()) {
    dom.filePathInput.value = state.homePath || state.workspaceRoot || state.user?.workspace_root || '~';
  }
  renderFilePathDisplay();
};

const normalizeFilePathForRequest = (path) => String(path || '').replace(/^\/+/, '');

const buildFileQuery = (path, root = state.workspaceRoot) => {
  const query = new URLSearchParams();
  query.set('path', normalizeFilePathForRequest(path));
  if (root) {
    query.set('root', root);
  }
  return query;
};

const buildFileDownloadUrl = (path, root = state.workspaceRoot) => (
  `/api/files/download?${buildFileQuery(path, root).toString()}`
);

const buildFilePreviewMetaUrl = (path, root = state.workspaceRoot) => (
  `/api/files/preview/meta?${buildFileQuery(path, root).toString()}`
);

const buildFilePreviewTextUrl = (path, root = state.workspaceRoot) => (
  `/api/files/preview/text?${buildFileQuery(path, root).toString()}`
);

const buildFilePreviewContentUrl = (path, root = state.workspaceRoot) => (
  `/api/files/preview/content?${buildFileQuery(path, root).toString()}`
);

const getFilePreviewKey = (path, root = state.workspaceRoot) => (
  `${String(root || '')}\n${normalizeFilePathForRequest(path)}`
);

const getFileNameFromPath = (path) => {
  const normalized = normalizeFilePathForRequest(path);
  const parts = normalized.split('/').filter(Boolean);
  return parts[parts.length - 1] || 'file';
};

const getActiveFilePreviewTab = () => {
  const activeId = String(state.activeWorkspaceTab || CHAT_TAB_ID);
  return state.filePreviewTabs.find((tab) => tab.id === activeId) || null;
};

const isValidWorkspaceTab = (tabId) => (
  tabId === CHAT_TAB_ID || state.filePreviewTabs.some((tab) => tab.id === tabId)
);

const getActiveWorkspaceTitle = () => {
  const activeFileTab = getActiveFilePreviewTab();
  if (activeFileTab) return activeFileTab.title || activeFileTab.filename || 'file';
  return getActiveChatTitle();
};

const setActiveWorkspaceTab = (tabId) => {
  const nextTabId = isValidWorkspaceTab(tabId) ? tabId : CHAT_TAB_ID;
  if (state.activeWorkspaceTab === CHAT_TAB_ID && nextTabId !== CHAT_TAB_ID) {
    saveActiveSessionScrollPosition();
  }
  state.activeWorkspaceTab = nextTabId;
  renderWorkspaceHeader();
  renderWorkspaceTabs();
  if (nextTabId === CHAT_TAB_ID) {
    renderMessages();
  }
};

const closeFilePreviewTab = (tabId) => {
  const normalizedTabId = String(tabId || '').trim();
  if (!normalizedTabId) return;
  const wasActive = state.activeWorkspaceTab === normalizedTabId;
  const closedIndex = state.filePreviewTabs.findIndex((tab) => tab.id === normalizedTabId);
  if (closedIndex < 0) return;
  state.filePreviewTabs = state.filePreviewTabs.filter((tab) => tab.id !== normalizedTabId);
  if (wasActive) {
    const leftNeighbor = state.filePreviewTabs[closedIndex - 1] || null;
    state.activeWorkspaceTab = leftNeighbor?.id || CHAT_TAB_ID;
  }
  renderWorkspaceHeader();
  renderWorkspaceTabs();
  if (wasActive && state.activeWorkspaceTab === CHAT_TAB_ID) {
    renderMessages();
  }
};

const loadFilePreviewTab = async (tabId) => {
  const tab = state.filePreviewTabs.find((item) => item.id === tabId);
  if (!tab) return;

  try {
    const meta = await api(buildFilePreviewMetaUrl(tab.path, tab.root), { method: 'GET' }).then((res) => res.json());
    const nextTab = state.filePreviewTabs.find((item) => item.id === tabId);
    if (!nextTab) return;

    nextTab.meta = meta || {};
    nextTab.filename = meta?.filename || nextTab.filename || getFileNameFromPath(nextTab.path);
    nextTab.title = nextTab.filename;
    nextTab.size = Number(meta?.size || 0);
    nextTab.mimeType = String(meta?.mime_type || '');
    nextTab.previewType = String(meta?.preview_type || 'unsupported');
    nextTab.downloadUrl = meta?.download_url || buildFileDownloadUrl(nextTab.path, nextTab.root);
    nextTab.contentUrl = meta?.content_url || (
      ['image', 'pdf'].includes(nextTab.previewType)
        ? buildFilePreviewContentUrl(nextTab.path, nextTab.root)
        : ''
    );

    if (nextTab.previewType === 'text') {
      renderWorkspaceHeader();
      renderWorkspaceTabs();
      const textJson = await api(buildFilePreviewTextUrl(nextTab.path, nextTab.root), { method: 'GET' }).then((res) => res.json());
      const textTab = state.filePreviewTabs.find((item) => item.id === tabId);
      if (!textTab) return;
      textTab.content = String(textJson?.content || '');
      textTab.loading = false;
      textTab.error = '';
      renderWorkspaceTabs();
      return;
    }

    nextTab.loading = false;
    nextTab.error = '';
    renderWorkspaceHeader();
    renderWorkspaceTabs();
  } catch (error) {
    const failedTab = state.filePreviewTabs.find((item) => item.id === tabId);
    if (!failedTab) return;
    failedTab.loading = false;
    failedTab.error = String(error.message || 'Failed to load preview');
    renderWorkspaceTabs();
  }
};

const openFilePreview = (path, entry = null) => {
  const relativePath = normalizeFilePathForRequest(path);
  if (!relativePath) return;
  const root = String(state.workspaceRoot || '').trim();
  const key = getFilePreviewKey(relativePath, root);
  const existingTab = state.filePreviewTabs.find((tab) => tab.key === key);
  if (existingTab) {
    setActiveWorkspaceTab(existingTab.id);
    closeMobilePanel();
    return;
  }

  const filename = entry?.name || getFileNameFromPath(relativePath);
  const tab = {
    id: `file-${uuid()}`,
    key,
    path: relativePath,
    root,
    title: filename,
    filename,
    size: Number(entry?.size || 0),
    mimeType: '',
    previewType: 'loading',
    content: '',
    contentUrl: '',
    downloadUrl: buildFileDownloadUrl(relativePath, root),
    loading: true,
    error: '',
    meta: null,
  };
  state.filePreviewTabs = [...state.filePreviewTabs, tab];
  if (state.activeWorkspaceTab === CHAT_TAB_ID) {
    saveActiveSessionScrollPosition();
  }
  state.activeWorkspaceTab = tab.id;
  renderWorkspaceHeader();
  renderWorkspaceTabs();
  closeMobilePanel();
  loadFilePreviewTab(tab.id);
};

const createWorkspaceTabButton = ({ id, title, closeable = false }) => {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'workspace-tab';
  if (closeable) {
    button.classList.add('closeable');
  }
  button.setAttribute('role', 'tab');
  button.setAttribute('aria-selected', String(state.activeWorkspaceTab === id));
  button.title = title;
  if (state.activeWorkspaceTab === id) {
    button.classList.add('active');
  }

  const label = document.createElement('span');
  label.className = 'workspace-tab-label';
  label.textContent = title;
  button.append(label);

  button.addEventListener('click', () => setActiveWorkspaceTab(id));

  if (closeable) {
    const close = document.createElement('span');
    close.className = 'workspace-tab-close';
    close.setAttribute('role', 'button');
    close.setAttribute('aria-label', `Close ${title}`);
    close.title = `Close ${title}`;
    close.textContent = 'x';
    close.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      closeFilePreviewTab(id);
    });
    button.append(close);
  }

  return button;
};

const appendFilePreviewDownloadButton = (container, tab) => {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'file-preview-download';
  button.setAttribute('aria-label', 'Save file');
  button.title = 'Save file';
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('class', 'file-preview-download-icon');
  icon.setAttribute('viewBox', '0 0 24 24');
  icon.setAttribute('aria-hidden', 'true');
  icon.setAttribute('focusable', 'false');
  icon.innerHTML = [
    '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"></path>',
    '<path d="M17 21v-8H7v8"></path>',
    '<path d="M7 3v5h8"></path>',
    '<path d="M9 17h6"></path>',
  ].join('');
  button.append(icon);
  button.addEventListener('click', () => {
    downloadFile(tab.path, tab.root).catch((error) => showChatError(error.message));
  });
  container.append(button);
};

const renderFilePreviewUnavailable = (panel, tab, message) => {
  const empty = document.createElement('div');
  empty.className = 'file-preview-empty';
  const text = document.createElement('div');
  text.textContent = message;
  const actions = document.createElement('div');
  actions.className = 'file-preview-empty-actions';
  appendFilePreviewDownloadButton(actions, tab);
  empty.append(text, actions);
  panel.append(empty);
};

const renderFilePreviewPanel = (tab) => {
  const panel = document.createElement('div');
  panel.className = 'workspace-tab-panel file-preview-panel';
  panel.setAttribute('role', 'tabpanel');
  panel.setAttribute('aria-label', tab.title || tab.filename || 'file');

  const toolbar = document.createElement('div');
  toolbar.className = 'file-preview-toolbar';

  const titleBlock = document.createElement('div');
  titleBlock.className = 'file-preview-title-block';
  const title = document.createElement('div');
  title.className = 'file-preview-title';
  title.textContent = tab.filename || tab.title || 'file';
  const meta = document.createElement('div');
  meta.className = 'file-preview-meta';
  const metaPieces = [];
  if (tab.mimeType) metaPieces.push(tab.mimeType);
  if (tab.size) metaPieces.push(formatFileSize(tab.size));
  meta.textContent = metaPieces.join(' · ');
  titleBlock.append(title, meta);

  const actions = document.createElement('div');
  actions.className = 'file-preview-actions';
  appendFilePreviewDownloadButton(actions, tab);
  toolbar.append(titleBlock, actions);
  panel.append(toolbar);

  const body = document.createElement('div');
  body.className = 'file-preview-body';

  if (tab.loading) {
    const loading = document.createElement('div');
    loading.className = 'file-preview-empty';
    loading.textContent = 'Loading preview...';
    body.append(loading);
  } else if (tab.error) {
    renderFilePreviewUnavailable(body, tab, tab.error);
  } else if (tab.previewType === 'too_large') {
    renderFilePreviewUnavailable(body, tab, 'This file is too large to preview.');
  } else if (tab.previewType === 'unsupported') {
    renderFilePreviewUnavailable(body, tab, 'Preview is not available for this file type.');
  } else if (tab.previewType === 'text') {
    const pre = document.createElement('pre');
    pre.className = 'file-preview-code';
    pre.textContent = tab.content || '';
    body.append(pre);
  } else if (tab.previewType === 'image') {
    const imageWrap = document.createElement('div');
    imageWrap.className = 'file-preview-image-wrap';
    const image = document.createElement('img');
    image.className = 'file-preview-image';
    image.alt = tab.filename || 'Preview image';
    image.src = tab.contentUrl || buildFilePreviewContentUrl(tab.path, tab.root);
    imageWrap.append(image);
    body.append(imageWrap);
  } else if (tab.previewType === 'pdf') {
    const frame = document.createElement('iframe');
    frame.className = 'file-preview-frame';
    frame.title = tab.filename || 'PDF preview';
    frame.src = tab.contentUrl || buildFilePreviewContentUrl(tab.path, tab.root);
    body.append(frame);
  } else {
    renderFilePreviewUnavailable(body, tab, 'Preview is not available for this file type.');
  }

  panel.append(body);
  return panel;
};

const renderWorkspaceTabs = () => {
  if (!dom.workspaceTabs || !dom.chatTabPanel || !dom.filePreviewPanels) return;

  if (!isValidWorkspaceTab(state.activeWorkspaceTab)) {
    state.activeWorkspaceTab = CHAT_TAB_ID;
  }

  const activeTabId = state.activeWorkspaceTab || CHAT_TAB_ID;
  dom.workspaceTabs.innerHTML = '';
  dom.workspaceTabs.append(createWorkspaceTabButton({ id: CHAT_TAB_ID, title: 'Chats' }));
  for (const tab of state.filePreviewTabs) {
    dom.workspaceTabs.append(createWorkspaceTabButton({
      id: tab.id,
      title: tab.title || tab.filename || 'file',
      closeable: true,
    }));
  }

  const chatActive = activeTabId === CHAT_TAB_ID;
  dom.chatTabPanel.hidden = !chatActive;
  dom.chatTabPanel.classList.toggle('active', chatActive);
  if (dom.composerForm) {
    dom.composerForm.hidden = !chatActive;
  }

  dom.filePreviewPanels.hidden = chatActive;
  dom.filePreviewPanels.innerHTML = '';
  if (!chatActive) {
    const activeFileTab = getActiveFilePreviewTab();
    if (activeFileTab) {
      dom.filePreviewPanels.append(renderFilePreviewPanel(activeFileTab));
    }
  }
};

const renderWorkspace = () => {
  syncActiveSessionUiState();
  renderChatList();
  renderWorkspaceHeader();
  renderMessages();
  renderAttachments();
  renderWorkspaceTabs();
  renderApprovalModal();
  renderFileBrowserControls();
  refreshComposerBusyState();
  renderMobilePanelState();
};

const normalizeDirectory = (path) => {
  const normalized = String(path || '/').replace(/\\/g, '/');
  return normalized.endsWith('/') ? normalized : `${normalized}/`;
};

const getParentDirectory = (path) => {
  const trimmed = normalizeDirectory(path).replace(/\/+$/g, '');
  if (!trimmed || trimmed === '/') return '/';
  const index = trimmed.lastIndexOf('/');
  return index <= 0 ? '/' : normalizeDirectory(trimmed.slice(0, index));
};

const isSameOrDescendantDirectory = (path, directory) => {
  const normalizedPath = normalizeDirectory(path);
  const normalizedDirectory = normalizeDirectory(directory);
  return normalizedDirectory === '/'
    ? normalizedPath.startsWith('/')
    : normalizedPath === normalizedDirectory || normalizedPath.startsWith(normalizedDirectory);
};

const pruneFileTreeDirectory = (path) => {
  const normalized = normalizeDirectory(path);
  for (const expandedPath of Array.from(state.expandedPaths)) {
    if (isSameOrDescendantDirectory(expandedPath, normalized)) {
      state.expandedPaths.delete(expandedPath);
    }
  }
  for (const cachedPath of Array.from(state.treeCache.keys())) {
    if (isSameOrDescendantDirectory(cachedPath, normalized)) {
      state.treeCache.delete(cachedPath);
    }
  }
};

const getFileTreePathDepth = (path) => normalizeDirectory(path)
  .split('/')
  .filter(Boolean)
  .length;

const getVisibleFileTreeRefreshPaths = () => {
  const root = normalizeDirectory(state.rootPath || state.currentPath || '/');
  const paths = new Set([root]);
  if (state.currentPath) {
    paths.add(normalizeDirectory(state.currentPath));
  }
  for (const expandedPath of state.expandedPaths) {
    const normalized = normalizeDirectory(expandedPath);
    if (isSameOrDescendantDirectory(normalized, root)) {
      paths.add(normalized);
    }
  }
  return Array.from(paths).sort((left, right) => (
    getFileTreePathDepth(left) - getFileTreePathDepth(right) || left.localeCompare(right)
  ));
};

const findNearestCachedDirectory = (path, rootPath = state.rootPath || '/') => {
  const root = normalizeDirectory(rootPath || '/');
  let candidate = normalizeDirectory(path);
  while (candidate !== root && candidate !== '/' && !state.treeCache.has(candidate)) {
    candidate = getParentDirectory(candidate);
  }
  if (state.treeCache.has(candidate)) return candidate;
  return root;
};

const runWithFileTreeConcurrency = async (items, worker) => {
  const queue = [...items];
  const workerCount = Math.min(FILE_TREE_REFRESH_MAX_CONCURRENCY, queue.length);
  const workers = Array.from({ length: workerCount }, async () => {
    while (queue.length) {
      const item = queue.shift();
      await worker(item);
    }
  });
  await Promise.all(workers);
};

const getDisplayDirectoryPath = () => {
  const workspaceRoot = String(state.workspaceRoot || state.user?.workspace_root || '').trim();
  if (!workspaceRoot) {
    return state.rootPath || state.currentPath || '/';
  }
  const relativePath = normalizeDirectory(state.currentPath || state.rootPath || '/').replace(/^\/+|\/+$/g, '');
  const root = workspaceRoot === '/' ? '/' : workspaceRoot.replace(/\/+$/g, '');
  if (!relativePath) return root || '/';
  if (root === '/') return `/${relativePath}`;
  return `${root}/${relativePath}`;
};

const splitDisplayPath = (path) => {
  const rawPath = String(path || '~').trim() || '~';
  if (rawPath === '/' || rawPath === '~') {
    return { prefix: '', tail: rawPath };
  }
  const normalizedPath = rawPath.length > 1 ? rawPath.replace(/\/+$/g, '') : rawPath;
  const separatorIndex = normalizedPath.lastIndexOf('/');
  if (separatorIndex < 0) {
    return { prefix: '', tail: normalizedPath };
  }
  if (separatorIndex === 0) {
    return { prefix: '/', tail: normalizedPath.slice(1) || '' };
  }
  return {
    prefix: `${normalizedPath.slice(0, separatorIndex)}/`,
    tail: normalizedPath.slice(separatorIndex + 1),
  };
};

const renderFilePathDisplay = () => {
  const displayPath = getDisplayDirectoryPath();
  const { prefix, tail } = splitDisplayPath(displayPath);
  if (dom.filePathDisplay) {
    dom.filePathDisplay.title = displayPath;
  }
  if (dom.filePathPrefix) {
    dom.filePathPrefix.textContent = prefix;
    dom.filePathPrefix.hidden = !prefix;
  }
  if (dom.filePathTail) {
    dom.filePathTail.textContent = tail || displayPath;
  }
  if (dom.cwdLabel) {
    dom.cwdLabel.textContent = displayPath;
  }
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

const listDirectory = async (path, force = false, { workspaceRoot = state.workspaceRoot } = {}) => {
  const directory = normalizeDirectory(path);
  if (!force && state.treeCache.has(directory)) {
    return state.treeCache.get(directory);
  }

  const relativePath = directory === '/' ? '' : directory.replace(/^\/+|\/+$/g, '');
  const query = new URLSearchParams();
  query.set('path', relativePath);
  const requestWorkspaceRoot = String(workspaceRoot || '').trim();
  if (requestWorkspaceRoot) {
    query.set('root', requestWorkspaceRoot);
  }
  const json = await api(`/api/files/tree?${query.toString()}`, { method: 'GET' }).then((res) => res.json());
  const entries = Array.isArray(json?.entries) ? json.entries : [];
  entries.sort((left, right) => {
    if (left.type !== right.type) return left.type === 'directory' ? -1 : 1;
    return left.name.localeCompare(right.name);
  });
  if (String(state.workspaceRoot || '').trim() === requestWorkspaceRoot) {
    state.treeCache.set(directory, entries);
  }
  return entries;
};

const downloadFile = async (path, root = state.workspaceRoot) => {
  const link = document.createElement('a');
  link.href = buildFileDownloadUrl(path, root);
  link.download = path.split('/').pop() || 'file';
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
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
    const activeFileTab = getActiveFilePreviewTab();
    if (
      entry.type === 'file'
      && activeFileTab
      && activeFileTab.root === String(state.workspaceRoot || '').trim()
      && normalizeFilePathForRequest(activeFileTab.path) === normalizeFilePathForRequest(nodePath)
    ) {
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
      openFilePreview(nodePath, entry);
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
    if (dom.cwdLabel) {
      dom.cwdLabel.textContent = '';
    }
    renderFilePathDisplay();
    return;
  }

  renderFilePathDisplay();
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

const refreshVisibleFileTree = async () => {
  if (!state.user || !state.currentPath || !dom.fileTree) return;

  const userId = String(state.user?.id || '');
  const workspaceRoot = String(state.workspaceRoot || '').trim();
  const root = normalizeDirectory(state.rootPath || state.currentPath || '/');
  const scrollTop = dom.fileTree.scrollTop || 0;
  const refreshPaths = getVisibleFileTreeRefreshPaths();
  let rootError = null;

  state.expandedPaths.add(root);

  await runWithFileTreeConcurrency(refreshPaths, async (path) => {
    if (
      !state.user
      || String(state.user?.id || '') !== userId
      || String(state.workspaceRoot || '').trim() !== workspaceRoot
      || normalizeDirectory(state.rootPath || '/') !== root
    ) {
      return;
    }

    try {
      await listDirectory(path, true, { workspaceRoot });
    } catch (error) {
      if (normalizeDirectory(path) === root) {
        rootError = error;
        return;
      }
      pruneFileTreeDirectory(path);
    }
  });

  if (rootError) {
    throw rootError;
  }

  if (
    !state.user
    || String(state.user?.id || '') !== userId
    || String(state.workspaceRoot || '').trim() !== workspaceRoot
    || normalizeDirectory(state.rootPath || '/') !== root
  ) {
    return;
  }

  const currentPath = normalizeDirectory(state.currentPath || root);
  if (!state.treeCache.has(currentPath)) {
    state.currentPath = findNearestCachedDirectory(currentPath, root);
  }
  state.expandedPaths.add(root);

  await renderFileTree();
  dom.fileTree.scrollTop = scrollTop;
};

const runFileTreeRefreshNow = async ({ silent = true } = {}) => {
  if (!state.user || !state.currentPath) return;
  if (fileTreeRefreshInFlight) {
    fileTreeRefreshPending = true;
    return;
  }

  fileTreeRefreshInFlight = true;
  try {
    await refreshVisibleFileTree();
  } catch (error) {
    if (!silent) {
      showChatError(String(error.message || 'Failed to refresh files'));
    }
  } finally {
    fileTreeRefreshInFlight = false;
    if (fileTreeRefreshPending) {
      fileTreeRefreshPending = false;
      scheduleFileTreeRefresh('pending');
    }
  }
};

const scheduleFileTreeRefresh = (reason = '', { delay = FILE_TREE_REFRESH_DEBOUNCE_MS } = {}) => {
  if (!state.user || !state.currentPath || document.hidden) return;
  if (fileTreeRefreshTimer) {
    window.clearTimeout(fileTreeRefreshTimer);
  }
  fileTreeRefreshTimer = window.setTimeout(() => {
    fileTreeRefreshTimer = null;
    runFileTreeRefreshNow({ silent: true });
  }, Math.max(0, Number(delay) || 0));
};

const refreshFileTreeAfterFocus = () => {
  if (document.hidden || !state.user || !state.currentPath) return;
  const now = Date.now();
  if (now - fileTreeLastFocusRefreshAt < FILE_TREE_FOCUS_REFRESH_MIN_INTERVAL_MS) return;
  fileTreeLastFocusRefreshAt = now;
  scheduleFileTreeRefresh('focus');
};

const fetchWorkspaceFiles = async () => {
  const [configJson, treeJson] = await Promise.all([
    api('/api/files/config', { method: 'GET' }).then((res) => res.json()),
    api('/api/files/tree', { method: 'GET' }).then((res) => res.json()),
  ]);
  state.fileBrowserMode = String(configJson?.mode || 'home_only') === 'user_readable'
    ? 'user_readable'
    : 'home_only';
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
  renderFilePathDisplay();
};

const fetchModels = async () => {
  const response = await api('/api/models', { method: 'GET' });
  const json = await response.json();
  state.models = sortModelsForDisplay(Array.isArray(json?.data) ? json.data : []);
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
  if (isActiveSessionBlockingModelSwitch()) {
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
  state.sessionsLoadingMore = false;
  const json = await fetchSessionsPage(0, INITIAL_SESSION_PAGE_SIZE);
  applySessionsPage(json, { append: false });
  renderChatList();
  renderWorkspaceHeader();
};

const loadMoreSessions = async () => {
  if (state.sessionsLoadingMore || !state.sessionsHasMore) return;

  const previousScrollTop = dom.chatList?.scrollTop || 0;
  state.sessionsLoadingMore = true;
  renderChatList();

  try {
    const json = await fetchSessionsPage(state.sessionsNextOffset, SESSION_LOAD_MORE_PAGE_SIZE);
    applySessionsPage(json, { append: true });
  } finally {
    state.sessionsLoadingMore = false;
    renderChatList();
    renderWorkspaceHeader();
    window.requestAnimationFrame(() => {
      if (dom.chatList) {
        dom.chatList.scrollTop = previousScrollTop;
      }
    });
  }
};

const openSession = async (sessionId) => {
  resetSessionRenameState();
  saveActiveSessionScrollPosition();
  if (!sessionId) {
    state.activeSession = state.draftSession;
    state.activeSessionId = state.draftSession?.id || null;
    state.messages = [];
    state.activeWorkspaceTab = CHAT_TAB_ID;
    state.shouldAutoScrollMessages = true;
    renderWorkspace();
    return;
  }

  if (state.activeSessionId === sessionId && state.messages.length > 0) {
    setActiveWorkspaceTab(CHAT_TAB_ID);
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
  state.activeWorkspaceTab = CHAT_TAB_ID;
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
    state.shouldAutoScrollMessages = !state.sessionScrollPositions.has(sessionId);
    queueMessageScrollRestore(sessionId);
    renderWorkspace();
    if (ACTIVE_LIVE_SESSION_STATUSES.has(String(liveState?.status || ''))) {
      recoverTuiBridgeAfterDisconnect(sessionId).catch(() => {});
    }
    return;
  }

  try {
    const response = await api(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'GET' });
    const json = await response.json();
    const serverMessages = Array.isArray(json?.messages)
      ? json.messages.map(normalizeMessageForDisplay)
      : [];
    const liveState = json?.live || json?.session?.live || null;
    const previousMessages = getCachedSessionMessages(sessionId) || state.messages;
    const normalizedMessages = mergeSnapshotMessagesWithLocalProgress(serverMessages, previousMessages);
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
  {
    preserveLiveMessages = false,
    forceReplaceLiveMessages = false,
    managePolling = true,
    shouldApplySnapshot = null,
  } = {},
) => {
  if (!sessionId) return null;

  const response = await api(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'GET' });
  const json = await response.json();
  const session = normalizeSessionSnapshot(json?.session || null);
  const serverMessages = Array.isArray(json?.messages)
    ? json.messages.map(normalizeMessageForDisplay)
    : null;
  const liveState = json?.live || json?.session?.live || null;
  const previousMessages = getCachedSessionMessages(sessionId);
  const normalizedMessages = Array.isArray(serverMessages)
    ? mergeSnapshotMessagesWithLocalProgress(serverMessages, previousMessages)
    : null;
  if (!session?.id) {
    return null;
  }
  if (typeof shouldApplySnapshot === 'function' && !shouldApplySnapshot(session.id)) {
    return null;
  }

  const sessionWithLive = {
    ...session,
    live: liveState,
  };
  state.sessions = [sessionWithLive, ...state.sessions.filter((chat) => chat.id !== session.id)];
  applyLiveStateToSession(session.id, liveState, { managePolling });
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
    applyLiveStateToSession(session.id, liveState, { managePolling });
    if (Array.isArray(normalizedMessages) && shouldReplaceMessages) {
      state.messages = normalizedMessages;
    }
  }
  return state.sessions.find((chat) => chat.id === session.id) || sessionWithLive;
};

function stopLiveSessionPolling(sessionId) {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId) return;
  liveSessionPollingSessionIds.delete(persistentSessionId);
  bumpLiveSessionPollingGeneration(persistentSessionId);
  const timer = liveSessionPollTimersBySessionId.get(persistentSessionId);
  if (timer) {
    window.clearTimeout(timer);
  }
  liveSessionPollTimersBySessionId.delete(persistentSessionId);
  liveSessionPollInFlightBySessionId.delete(persistentSessionId);
  liveSessionPollFailuresBySessionId.delete(persistentSessionId);
}

function stopAllLiveSessionPolling() {
  for (const sessionId of liveSessionPollingSessionIds) {
    bumpLiveSessionPollingGeneration(sessionId);
  }
  for (const timer of liveSessionPollTimersBySessionId.values()) {
    window.clearTimeout(timer);
  }
  liveSessionPollingSessionIds.clear();
  liveSessionPollTimersBySessionId.clear();
  liveSessionPollInFlightBySessionId.clear();
  liveSessionPollFailuresBySessionId.clear();
  liveSessionPollGenerationBySessionId.clear();
}

function bumpLiveSessionPollingGeneration(sessionId) {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId) return 0;
  liveSessionPollGenerationCounter += 1;
  liveSessionPollGenerationBySessionId.set(persistentSessionId, liveSessionPollGenerationCounter);
  return liveSessionPollGenerationCounter;
}

function getLiveSessionPollingGeneration(sessionId) {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId) return 0;
  return liveSessionPollGenerationBySessionId.get(persistentSessionId) || 0;
}

function isCurrentLiveSessionPoll(sessionId, generation) {
  const persistentSessionId = String(sessionId || '').trim();
  return Boolean(
    persistentSessionId
    && liveSessionPollingSessionIds.has(persistentSessionId)
    && getLiveSessionPollingGeneration(persistentSessionId) === generation
  );
}

function scheduleLiveSessionPoll(sessionId, delayMs = LIVE_SESSION_POLL_INTERVAL_MS) {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId || !state.user) return;
  if (!liveSessionPollingSessionIds.has(persistentSessionId)) return;
  if (liveSessionPollTimersBySessionId.has(persistentSessionId)) return;
  const generation = getLiveSessionPollingGeneration(persistentSessionId);
  const timer = window.setTimeout(() => {
    liveSessionPollTimersBySessionId.delete(persistentSessionId);
    pollLiveSessionSnapshot(persistentSessionId, generation).catch(() => {});
  }, delayMs);
  liveSessionPollTimersBySessionId.set(persistentSessionId, timer);
}

function startLiveSessionPolling(sessionId) {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId || !state.user) return;
  if (!liveSessionPollingSessionIds.has(persistentSessionId)) {
    liveSessionPollingSessionIds.add(persistentSessionId);
    bumpLiveSessionPollingGeneration(persistentSessionId);
  }
  scheduleLiveSessionPoll(persistentSessionId, LIVE_SESSION_POLL_INTERVAL_MS);
}

async function pollLiveSessionSnapshot(sessionId, generation = getLiveSessionPollingGeneration(sessionId)) {
  const persistentSessionId = String(sessionId || '').trim();
  if (!persistentSessionId || !state.user) return;
  if (!isCurrentLiveSessionPoll(persistentSessionId, generation)) return;

  if (liveSessionPollInFlightBySessionId.has(persistentSessionId)) {
    scheduleLiveSessionPoll(persistentSessionId, LIVE_SESSION_POLL_INTERVAL_MS);
    return;
  }

  liveSessionPollInFlightBySessionId.add(persistentSessionId);
  try {
    const session = await updateSessionSnapshot(persistentSessionId, {
      forceReplaceLiveMessages: true,
      managePolling: false,
      shouldApplySnapshot: (resolvedSessionId) => (
        resolvedSessionId === persistentSessionId
        && isCurrentLiveSessionPoll(persistentSessionId, generation)
      ),
    });
    liveSessionPollFailuresBySessionId.delete(persistentSessionId);
    if (!session) return;

    const liveState = session?.live || (isViewingSession(persistentSessionId) ? state.activeSession?.live : null);
    const status = String(liveState?.status || '').trim();
    const pendingApproval = liveState?.pending_approval || liveState?.pendingApproval || null;
    if (!ACTIVE_LIVE_SESSION_STATUSES.has(status)) {
      stopLiveSessionPolling(persistentSessionId);
      if (isViewingSession(persistentSessionId)) {
        renderWorkspace();
      } else {
        renderChatList();
      }
      return;
    }

    if (status === 'awaiting_approval' && pendingApproval) {
      stopLiveSessionPolling(persistentSessionId);
      if (isViewingSession(persistentSessionId)) {
        renderWorkspace();
      } else {
        renderChatList();
      }
      return;
    }

    if (isViewingSession(persistentSessionId)) {
      renderWorkspace();
    } else {
      renderChatList();
    }
    scheduleLiveSessionPoll(persistentSessionId, LIVE_SESSION_POLL_INTERVAL_MS);
  } catch {
    if (!isCurrentLiveSessionPoll(persistentSessionId, generation)) return;
    const failures = (liveSessionPollFailuresBySessionId.get(persistentSessionId) || 0) + 1;
    liveSessionPollFailuresBySessionId.set(persistentSessionId, failures);
    scheduleLiveSessionPoll(
      persistentSessionId,
      failures > 1 ? LIVE_SESSION_POLL_FAILURE_DELAY_MS : LIVE_SESSION_POLL_INTERVAL_MS
    );
  } finally {
    liveSessionPollInFlightBySessionId.delete(persistentSessionId);
  }
}

const isViewingSession = (sessionId) => Boolean(sessionId) && state.activeSessionId === sessionId;

const deleteChat = async (chatId, isDraft = false) => {
  if (isDraft) {
    stopLiveSessionPolling(chatId);
    forgetSessionScrollPosition(chatId);
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
  stopLiveSessionPolling(chatId);

  await api(`/api/sessions/${encodeURIComponent(chatId)}`, { method: 'DELETE' });
  clearLiveSessionMessages(chatId);
  forgetSessionScrollPosition(chatId);
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
  const attachmentsTooLarge = getTotalAttachmentSize(state.pendingAttachments) > MAX_TOTAL_ATTACHMENT_SIZE_BYTES;

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
  if (attachmentsTooLarge) {
    showChatError(getAttachmentTooLargeMessage());
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
  const submissionMode = getComposerMode();

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
        mode: submissionMode,
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
      saveSessionScrollPosition(draftSessionId);
      moveSessionScrollPosition(draftSessionId, persistentSessionId);
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
    state.composerMode = 'chat';

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
  renderMobilePanelState();
};

const showLogin = () => {
  closeMobilePanel();
  closeUpdateNotesPanel({ markSeen: false });
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
  const email = dom.registerEmail.value.trim();
  const emailVerificationCode = dom.registerEmailCode.value.trim();
  const password = document.getElementById('register-password').value;
  const passwordConfirm = document.getElementById('register-password-confirm').value;

  if (!state.emailVerificationId || state.emailVerificationEmail !== email.toLowerCase()) {
    showError(dom.registerError, 'Send a verification code to this email first.');
    return;
  }
  if (state.emailVerificationExpiresAt && state.emailVerificationExpiresAt <= nowSeconds()) {
    showError(dom.registerError, 'Verification code has expired. Send a new code.');
    return;
  }
  if (!/^\d{6}$/.test(emailVerificationCode)) {
    showError(dom.registerError, 'Verification code must be 6 digits.');
    return;
  }
  const passwordValidationMessage = validatePasswordComplexity(password);
  if (passwordValidationMessage) {
    showError(dom.registerError, passwordValidationMessage);
    return;
  }
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
        email_verification_id: state.emailVerificationId,
        email_verification_code: emailVerificationCode,
      }),
    });
    const json = await response.json();
    state.signupJobId = json?.job_id || null;
    clearEmailVerificationState();
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

dom.sendEmailCodeButton?.addEventListener('click', handleSendEmailVerification);

dom.sendPasswordResetCodeButton?.addEventListener('click', handleSendPasswordResetVerification);

dom.registerEmail?.addEventListener('input', () => {
  const currentEmail = dom.registerEmail.value.trim().toLowerCase();
  if (state.emailVerificationEmail && currentEmail !== state.emailVerificationEmail) {
    clearEmailVerificationState();
  }
});

dom.passwordResetEmail?.addEventListener('input', () => {
  const currentEmail = dom.passwordResetEmail.value.trim().toLowerCase();
  if (state.passwordResetEmail && currentEmail !== state.passwordResetEmail) {
    clearPasswordResetState();
  }
});

dom.registerEmailCode?.addEventListener('input', () => {
  const digits = dom.registerEmailCode.value.replace(/\D/g, '').slice(0, 6);
  if (dom.registerEmailCode.value !== digits) {
    dom.registerEmailCode.value = digits;
  }
});

dom.passwordResetEmailCode?.addEventListener('input', () => {
  const digits = dom.passwordResetEmailCode.value.replace(/\D/g, '').slice(0, 6);
  if (dom.passwordResetEmailCode.value !== digits) {
    dom.passwordResetEmailCode.value = digits;
  }
});

dom.showRegisterButton.addEventListener('click', () => {
  setAuthViewMode('register');
});

dom.showLoginButton.addEventListener('click', () => {
  setAuthViewMode('signin');
});

dom.forgotPasswordButton?.addEventListener('click', () => {
  const loginEmail = document.getElementById('email')?.value.trim() || '';
  if (dom.passwordResetEmail && !dom.passwordResetEmail.value.trim()) {
    dom.passwordResetEmail.value = loginEmail;
  }
  setAuthViewMode('password-reset');
  window.requestAnimationFrame(() => dom.passwordResetEmail?.focus());
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

dom.passwordResetBackButton?.addEventListener('click', () => {
  if (state.passwordResetSubmitting) return;
  setAuthViewMode('signin');
});

dom.passwordResetLoginButton?.addEventListener('click', () => {
  if (state.passwordResetSubmitting) return;
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

dom.changePasswordButton?.addEventListener('click', openPasswordModal);

dom.updateNotesButton?.addEventListener('click', (event) => {
  event.stopPropagation();
  toggleUpdateNotesPanel();
});

dom.updateNotesPanel?.addEventListener('click', (event) => {
  event.stopPropagation();
});

dom.updateNotesBackdrop?.addEventListener('click', () => {
  closeUpdateNotesPanel();
});

dom.updateNotesClose?.addEventListener('click', (event) => {
  event.stopPropagation();
  closeUpdateNotesPanel();
});

dom.sidebarSettingsButton?.addEventListener('click', (event) => {
  event.stopPropagation();
  toggleSidebarSettingsMenu();
});

dom.sidebarSettingsMenu?.addEventListener('click', (event) => {
  event.stopPropagation();
});

dom.sidebarChangePasswordButton?.addEventListener('click', () => {
  closeSidebarSettingsMenu();
  openPasswordModal();
});

dom.sidebarSignOutButton?.addEventListener('click', () => {
  performSignOut();
});

document.addEventListener('click', () => {
  closeSidebarSettingsMenu();
  closeUpdateNotesPanel();
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    closeMobilePanel();
    closeSidebarSettingsMenu();
    closeUpdateNotesPanel();
  }
});

dom.passwordForm?.addEventListener('submit', handlePasswordChangeSubmit);

dom.passwordResetForm?.addEventListener('submit', handlePasswordResetSubmit);

dom.passwordCancelButton?.addEventListener('click', closePasswordModal);

dom.passwordBackdrop?.addEventListener('click', closePasswordModal);

dom.logoutButton.addEventListener('click', performSignOut);

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
  closeMobilePanel();
  startNewChat().catch((error) => showChatError(error.message));
});

dom.mobileChatsButton?.addEventListener('click', () => {
  toggleMobilePanel('chats');
});

dom.mobileFilesButton?.addEventListener('click', () => {
  toggleMobilePanel('files');
});

dom.mobilePanelBackdrop?.addEventListener('click', () => {
  closeMobilePanel();
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

dom.planButton?.addEventListener('click', () => {
  if (state.isSending) return;
  setComposerMode(getComposerMode() === 'plan' ? 'chat' : 'plan');
  dom.promptInput?.focus();
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
  saveActiveSessionScrollPosition();
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
  if (fileTreeRefreshTimer) {
    window.clearTimeout(fileTreeRefreshTimer);
    fileTreeRefreshTimer = null;
  }
  fileTreeRefreshPending = false;
  const refreshPromise = state.currentPath
    ? runFileTreeRefreshNow({ silent: false })
    : fetchWorkspaceFiles();
  await refreshPromise.catch((error) => showChatError(error.message));
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

window.addEventListener('focus', refreshFileTreeAfterFocus);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (fileTreeRefreshTimer) {
      window.clearTimeout(fileTreeRefreshTimer);
      fileTreeRefreshTimer = null;
    }
    return;
  }
  refreshFileTreeAfterFocus();
});

const handleMobilePanelMediaChange = () => {
  if (!isMobilePanelLayout()) {
    state.mobileOverlayPanel = null;
  }
  renderMobilePanelState();
};

if (typeof mobilePanelMediaQuery.addEventListener === 'function') {
  mobilePanelMediaQuery.addEventListener('change', handleMobilePanelMediaChange);
} else if (typeof mobilePanelMediaQuery.addListener === 'function') {
  mobilePanelMediaQuery.addListener(handleMobilePanelMediaChange);
}

initResizablePanels();
initThemeControls();
initUpdateNotes();
autoResizePromptInput();
renderMobilePanelState();
renderEmailVerificationState();
renderPasswordResetState();
bootstrapSession();
