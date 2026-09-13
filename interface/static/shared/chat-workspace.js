const LOCK_NAME = 'potato-chat-workspace-v1';
const CHANNEL_NAME = 'potato-chat-entry-v1';
const CLAIM_LOCK_NAME = 'potato-chat-transfer-v2';
const OWNER_KEY = 'potato-chat-owner-v2';
const RECEIPTS_KEY = 'potato-chat-receipts-v1';
const ENTRY_KEY = 'potato-chat-entry-v1';
const pages = ['genes', 'bulk_rnaseq', 'wgcna', 'spatial', 'genomes', 'genome_browser'];

export const accountKey = user => String(user?.id || user?.username || '');
const requestId = () => crypto.randomUUID?.()
  || Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
export const supportsWorkspace = () => Boolean(crypto?.getRandomValues
  && (!navigator.locks?.request || (window.BroadcastChannel && window.indexedDB)));
export const newEntry = (kind = 'return', payload = {}) => ({
  id: requestId(), kind, ...payload,
});

const validEntry = entry => {
  if (!entry || typeof entry.id !== 'string' || !entry.id || entry.id.length > 100) return false;
  if (entry.kind === 'return') return true;
  if (entry.kind === 'share') return /^[A-Za-z0-9_-]{16,512}$/.test(entry.token || '');
  return entry.kind === 'example' && pages.includes(entry.example?.page)
    && typeof entry.example?.text === 'string' && entry.example.text.trim()
    && entry.example.text.length <= 20000;
};

export const saveEntry = entry => {
  try {
    if (entry) sessionStorage.setItem(ENTRY_KEY, JSON.stringify(entry));
    else sessionStorage.removeItem(ENTRY_KEY);
  } catch { /* Navigation still works without tab storage. */ }
};

export const readEntry = () => {
  const hash = location.hash;
  let entry = null;
  try {
    if (hash.startsWith('#entry=')) entry = JSON.parse(decodeURIComponent(hash.slice(7)));
    else if (hash.startsWith('#example=')) {
      const example = JSON.parse(decodeURIComponent(hash.slice(9)));
      entry = newEntry('example', { example, ...(example.id ? { id: example.id } : {}) });
    } else if (hash.startsWith('#share=')) {
      entry = newEntry('share', { token: decodeURIComponent(hash.slice(7)) });
    } else entry = JSON.parse(sessionStorage.getItem(ENTRY_KEY) || 'null');
  } catch { entry = null; }
  if (/^#(entry|example|share)=/.test(hash)) {
    history.replaceState(history.state, '', location.pathname + location.search);
  }
  entry = validEntry(entry) ? entry : null;
  saveEntry(entry);
  return entry;
};

// Commit the handoff before releasing the lock, so a closing recipient cannot lose a draft.
export const handoffRecord = (operation, value = null, isCurrent = () => true) => new Promise((resolve, reject) => {
  const request = indexedDB.open('potato-chat-handoff-v2', 1);
  let database;
  let transaction;
  let settled = false;
  const finish = (error, result) => {
    if (settled) return;
    settled = true;
    clearTimeout(timer);
    if (error) {
      try { transaction?.abort(); } catch { /* The transaction may have already ended. */ }
    }
    database?.close();
    if (error) reject(error);
    else resolve(result);
  };
  const timer = setTimeout(() => finish(new Error('Workspace storage is unavailable')), 5000);
  request.onupgradeneeded = () => request.result.createObjectStore('workspace');
  request.onerror = () => finish(request.error);
  request.onblocked = () => finish(new Error('Workspace storage is blocked'));
  request.onsuccess = () => {
    database = request.result;
    if (settled) { database.close(); return; }
    try {
      if (!isCurrent()) throw new DOMException('Workspace ownership changed', 'AbortError');
      transaction = database.transaction('workspace', operation === 'get' ? 'readonly' : 'readwrite');
      const store = transaction.objectStore('workspace');
      const result = operation === 'put' ? store.put(value, 'handoff') : store.get('handoff');
      if (operation === 'delete') {
        result.onsuccess = () => {
          const account = typeof value === 'string' ? value : value?.account;
          if (result.result?.account === account && (!value?.id || value.id === result.result?.id)) store.delete('handoff');
        };
      }
      transaction.oncomplete = () => finish(null, result.result);
      transaction.onabort = () => finish(transaction.error || new Error('Workspace storage failed'));
      transaction.onerror = () => finish(transaction.error);
    } catch (error) { finish(error); }
  };
});

// The transfer lock serializes contenders; only the workspace lock authorizes chat.
export class ChatWorkspace {
  constructor({ onRequest, onStatus, onAuthChange, onYield, onTransferStatus }) {
    this.onRequest = onRequest;
    this.onStatus = onStatus;
    this.onAuthChange = onAuthChange;
    this.onYield = onYield;
    this.onTransferStatus = onTransferStatus;
    this.tabId = requestId();
    this.exclusive = Boolean(navigator.locks?.request);
    this.owned = false;
    this.ready = false;
    this.generation = 0;
    this.receipts = new Map();
    this.supported = supportsWorkspace();
    this.connect();
  }

  connect() {
    if (this.channel || !this.supported || !window.BroadcastChannel) return;
    try { this.channel = new BroadcastChannel(CHANNEL_NAME); }
    catch { if (this.exclusive) this.supported = false; return; }
    this.channel.onmessage = ({ data }) => {
      if (data?.type === 'auth-change') {
        if (data.clearReceipts) this.clearReceipts();
        this.onAuthChange(data.clearReceipts);
      }
      if (data?.type === 'takeover' && this.exclusive && this.owned) this.yieldTo(data);
      if (data?.type === 'transfer-status' && data.id === this.transferRequest?.id) {
        if (['busy', 'retry', 'account-changed'].includes(data.status)) {
          this.transferFailure = data.status;
          this.acquireAbort?.abort();
        } else this.onTransferStatus(data.status);
      }
    };
  }

  acquire(user, { takeover = true } = {}) {
    if (!this.supported) return Promise.resolve(false);
    if (this.owned) return Promise.resolve(this.account === accountKey(user));
    if (!this.exclusive) {
      this.connect();
      this.canResume = this.account === accountKey(user);
      this.account = accountKey(user);
      this.owned = true;
      this.restoreReceipts();
      return Promise.resolve(true);
    }
    if (this.acquiring) {
      return this.acquireAbort?.signal.aborted
        ? this.acquiring.then(() => this.acquire(user, { takeover })) : this.acquiring;
    }
    this.connect();
    const generation = this.generation;
    const controller = new AbortController();
    this.acquireAbort = controller;
    this.transferFailure = null;
    this.acquiring = navigator.locks.request(CLAIM_LOCK_NAME, { ifAvailable: true }, async claim => {
      if (!claim) { this.transferFailure = 'busy'; return false; }
      if (controller.signal.aborted || generation !== this.generation) return false;
      if (await this.requestLock(user, { ifAvailable: true }, generation)) return true;
      if (!this.supported || !takeover || controller.signal.aborted || generation !== this.generation) return false;
      this.transferRequest = { type: 'takeover', id: requestId(), account: accountKey(user), expiresAt: Date.now() + 20000 };
      this.onTransferStatus('waiting');
      const waiting = this.requestLock(user, { signal: controller.signal }, generation);
      const send = () => this.channel?.postMessage(this.transferRequest);
      send();
      const retry = setInterval(send, 500);
      const timeout = setTimeout(() => {
        this.transferFailure = 'timeout';
        controller.abort();
      }, 20000);
      try { return await waiting; }
      finally {
        clearInterval(retry);
        clearTimeout(timeout);
        this.transferRequest = null;
      }
    }).catch(() => { this.supported = false; return false; })
      .finally(() => {
        this.acquiring = null;
        this.acquireAbort = null;
        if (this.transferFailure && generation === this.generation) this.onTransferStatus(this.transferFailure);
      });
    return this.acquiring;
  }

  requestLock(user, options, generation) {
    return new Promise(resolve => {
      navigator.locks.request(LOCK_NAME, options, async lock => {
        if (!lock || generation !== this.generation) { resolve(false); return; }
        this.owned = true;
        this.account = accountKey(user);
        this.canResume = false;
        try {
          this.canResume = localStorage.getItem(OWNER_KEY) === this.tabId;
          localStorage.setItem(OWNER_KEY, this.tabId);
        } catch { /* Without a stored owner, reload state from the server. */ }
        this.restoreReceipts();
        await new Promise(release => { this.releaseLock = release; resolve(true); });
      }).catch(error => {
        if (error.name !== 'AbortError') this.supported = false;
        resolve(false);
      });
    });
  }

  async yieldTo(request) {
    if (!this.exclusive || this.yielding || request.expiresAt < Date.now()) return;
    let status;
    if (request.account !== this.account) status = 'account-changed';
    else if (this.pending) status = 'busy';
    else if (!this.ready) status = 'waiting';
    else {
      this.yielding = true;
      try { status = await this.onYield(request); }
      catch { status = 'retry'; }
      finally { this.yielding = false; }
    }
    if (this.owned) this.channel?.postMessage({ type: 'transfer-status', id: request.id, status });
  }

  receipt(entry, status) {
    this.onStatus(entry.id, status);
    return status;
  }

  async receive(entry) {
    if (!this.owned || !validEntry(entry)) return;
    if (entry.account !== this.account) return this.receipt(entry, 'account-changed');
    if (entry.kind === 'return') return this.receipt(entry, 'returned');
    if (this.receipts.has(entry.id)) return this.receipt(entry, this.receipts.get(entry.id));
    if (this.pending?.id === entry.id) return this.receipt(entry, 'pending');
    if (!this.ready || this.pending || this.yielding) return this.receipt(entry, 'busy');
    const generation = this.generation;
    this.pending = entry;
    this.receipt(entry, 'pending');
    let status;
    try { status = await this.onRequest(entry); }
    catch { status = 'retry'; }
    if (generation !== this.generation || !this.owned) return;
    this.pending = null;
    if (['completed', 'cancelled', 'importing'].includes(status)) {
      this.receipts.set(entry.id, status);
      this.receipts = new Map([...this.receipts].slice(-128));
      try {
        (this.exclusive ? localStorage : sessionStorage).setItem(
          RECEIPTS_KEY, JSON.stringify({ account: this.account, items: [...this.receipts] }),
        );
      } catch { /* In-memory receipts still deduplicate within this workspace. */ }
    }
    return this.receipt(entry, status);
  }

  authChanged({ clearReceipts = false } = {}) {
    this.connect();
    this.channel?.postMessage({ type: 'auth-change', clearReceipts });
    if (clearReceipts) this.clearReceipts();
  }

  restoreReceipts() {
    try {
      const stored = JSON.parse((this.exclusive ? localStorage : sessionStorage).getItem(RECEIPTS_KEY) || '{}');
      this.receipts = new Map(stored.account === this.account ? stored.items : []);
    } catch { this.receipts = new Map(); }
  }

  clearReceipts() {
    this.receipts.clear();
    try { (this.exclusive ? localStorage : sessionStorage).removeItem(RECEIPTS_KEY); }
    catch { /* In-memory receipts are still cleared when storage is unavailable. */ }
  }

  release() {
    this.acquireAbort?.abort();
    this.owned = false;
    this.ready = false;
    this.pending = null;
    this.generation += 1;
    this.releaseLock?.();
    this.releaseLock = null;
  }

  suspend() {
    this.release();
    this.channel?.close();
    this.channel = null;
  }
}
