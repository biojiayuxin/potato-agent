(() => {
  'use strict';
  if (document.getElementById('site-announcement')) return;

  const storagePrefix = 'potato.announcement.dismissed.';
  const dismissed = new Set();
  const root = document.documentElement;
  const chinese = root.lang.toLowerCase().startsWith('zh');
  const banner = document.createElement('aside');
  banner.id = 'site-announcement';
  banner.className = 'site-announcement';
  banner.setAttribute('aria-label', chinese ? '\u5168\u7ad9\u901a\u77e5' : 'Site announcement');
  banner.hidden = true;
  const message = document.createElement('div');
  message.className = 'site-announcement-message';
  message.setAttribute('role', 'status');
  message.setAttribute('aria-live', 'polite');
  message.tabIndex = 0;
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'site-announcement-close';
  close.textContent = '\u00d7';
  close.title = chinese ? '\u5173\u95ed\u901a\u77e5' : 'Dismiss announcement';
  close.setAttribute('aria-label', close.title);
  banner.append(message, close);
  document.body.prepend(banner);
  root.classList.add('has-announcements');

  let current = null;
  let serverOffset = 0;
  let expiryTimer = null;
  let inFlight = null;
  let refreshPending = false;

  function measure() {
    const height = banner.hidden ? 0 : Math.ceil(banner.getBoundingClientRect().height);
    root.style.setProperty('--announcement-height', `${height}px`);
  }

  function isDismissed(id) {
    if (dismissed.has(id)) return true;
    try { return localStorage.getItem(storagePrefix + id) === '1'; }
    catch (_) { return false; }
  }

  function render() {
    clearTimeout(expiryTimer);
    expiryTimer = null;
    const remaining = current?.ends_at == null ? Infinity
      : Date.parse(current.ends_at) - (Date.now() + serverOffset);
    const visible = current && remaining > 0 && !isDismissed(current.id);
    banner.hidden = !visible;
    if (visible && message.textContent !== current.message) message.textContent = current.message;
    if (visible && Number.isFinite(remaining)) {
      expiryTimer = setTimeout(render, Math.min(remaining, 2147483647));
    }
    measure();
  }

  function validatePayload(data) {
    if (!data || !Number.isFinite(Date.parse(data.server_time))) throw new Error('Invalid time');
    const item = data.announcement;
    if (item !== null && (!item || typeof item.id !== 'string' || !item.id
      || typeof item.message !== 'string' || !item.message.trim()
      || (item.ends_at !== null && !Number.isFinite(Date.parse(item.ends_at))))) {
      throw new Error('Invalid announcement');
    }
    return data;
  }

  async function refresh(force = false) {
    render();
    if (document.visibilityState === 'hidden') return;
    if (inFlight) {
      if (force) refreshPending = true;
      return;
    }
    const controller = new AbortController();
    const requestedAt = Date.now();
    inFlight = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/api/announcement', {
        cache: 'no-store', credentials: 'omit', signal: controller.signal,
      });
      if (!response.ok) throw new Error('Announcement unavailable');
      const data = validatePayload(await response.json());
      // Count request latency conservatively so a delayed response cannot extend expiry.
      serverOffset = Date.parse(data.server_time) - requestedAt;
      current = data.announcement;
    } catch (_) {
      // Keep a previously received announcement until its server deadline.
    } finally {
      clearTimeout(timeout);
      inFlight = null;
      render();
      if (refreshPending) {
        refreshPending = false;
        refresh();
      }
    }
  }

  close.addEventListener('click', () => {
    if (!current) return;
    dismissed.add(current.id);
    try { localStorage.setItem(storagePrefix + current.id, '1'); } catch (_) { /* Page memory remains available. */ }
    render();
  });
  window.addEventListener('storage', (event) => {
    if (event.key === null || event.key?.startsWith(storagePrefix)) render();
  });
  document.addEventListener('visibilitychange', () => refresh());
  window.addEventListener('pageshow', () => refresh());
  window.addEventListener('online', () => refresh());
  window.addEventListener('announcement:changed', () => refresh(true));
  window.addEventListener('resize', measure);
  if (window.ResizeObserver) new ResizeObserver(measure).observe(banner);
  setInterval(refresh, 30000);
  refresh();
})();
