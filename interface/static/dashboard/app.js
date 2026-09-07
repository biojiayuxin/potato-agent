(() => {
  'use strict';

  const fields = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'];
  const visibleFields = ['input_tokens', 'output_tokens', 'cache_read_tokens'];
  const labels = ['Input', 'Output', 'Cache read'];
  const number = new Intl.NumberFormat('en-US');
  const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 });
  const dateFormat = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  const shortDate = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const byId = (id) => document.getElementById(id);
  let usage = null;
  let period = 30;
  let usageInFlight = null;
  let retryTimer = null;
  let attemptedDay = '';
  let retryAfter = 0;

  function dateValue(value) { return new Date(`${value}T00:00:00Z`); }

  function beijingDay() {
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  }

  async function fetchJSON(url) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(url, { credentials: 'omit', cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error('Unavailable');
      return await response.json();
    } finally { clearTimeout(timeout); }
  }

  function validUsage(data) {
    if (!data || data.time_zone !== 'Asia/Shanghai' || data.status !== 'available'
      || !Array.isArray(data.days) || data.days.length !== 30) return false;
    const start = dateValue(data.start_date).getTime();
    return Number.isFinite(start) && data.days.every((day, index) => (
      day.date === new Date(start + index * 86400000).toISOString().slice(0, 10)
      && [...fields, 'total_tokens'].every((field) => Number.isSafeInteger(day[field]) && day[field] >= 0)
      && fields.reduce((sum, field) => sum + day[field], 0) === day.total_tokens
    )) && data.days[29].date === data.through_date;
  }

  function renderUnavailable() {
    document.querySelectorAll('[data-token]').forEach((element) => {
      element.textContent = 'Unavailable';
      element.classList.add('is-message');
      element.removeAttribute('title');
    });
    byId('usage-period').textContent = 'Asia/Shanghai \u00b7 Through yesterday';
    byId('trend-layout').hidden = true;
    byId('chart-status').hidden = false;
    byId('chart-status').textContent = 'Unavailable';
  }

  function selectDay(day, button) {
    byId('plot').querySelectorAll('button').forEach((bar) => {
      bar.setAttribute('aria-pressed', String(bar === button));
      bar.tabIndex = bar === button ? 0 : -1;
    });
    byId('detail-date').textContent = dateFormat.format(dateValue(day.date));
    byId('detail-total').textContent = number.format(day.total_tokens);
    byId('detail-tokens').replaceChildren(...visibleFields.map((field, index) => {
      const row = document.createElement('div');
      const term = document.createElement('dt');
      const value = document.createElement('dd');
      term.textContent = labels[index];
      value.textContent = number.format(day[field]);
      row.append(term, value);
      return row;
    }));
  }

  function renderUsage() {
    if (!usage) { renderUnavailable(); return; }
    const days = usage.days.slice(-period);
    document.querySelectorAll('[data-token]').forEach((element) => {
      const total = days.reduce((sum, day) => sum + day[element.dataset.token], 0);
      element.textContent = compact.format(total);
      element.title = `${number.format(total)} tokens`;
      element.classList.remove('is-message');
    });
    byId('usage-period').textContent = `${shortDate.format(dateValue(days[0].date))} - ${dateFormat.format(dateValue(usage.through_date))} \u00b7 Asia/Shanghai`;
    byId('trend-layout').hidden = false;
    byId('chart-status').hidden = true;
    const maximum = Math.max(1, ...days.map((day) => day.total_tokens));
    const magnitude = 10 ** Math.floor(Math.log10(maximum)) / 5;
    const ceiling = Math.ceil(maximum / magnitude) * magnitude;
    byId('axis-max').textContent = compact.format(ceiling);
    byId('axis-mid').textContent = compact.format(ceiling / 2);
    const plot = byId('plot');
    plot.style.setProperty('--day-count', days.length);
    plot.dataset.period = String(period);
    const bars = days.map((day, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'day-bar';
      button.classList.toggle('is-zero', day.total_tokens === 0);
      button.dataset.date = day.date;
      button.setAttribute('aria-label', `${dateFormat.format(dateValue(day.date))}: ${number.format(day.total_tokens)} total tokens. ${visibleFields.map((field, i) => `${labels[i]} ${number.format(day[field])}`).join(', ')}.`);
      button.setAttribute('aria-controls', 'detail-tokens');
      const fill = document.createElement('span');
      fill.className = 'bar-fill';
      fill.style.setProperty('--bar-height', `${100 * day.total_tokens / ceiling}%`);
      fill.setAttribute('aria-hidden', 'true');
      button.append(fill);
      ['pointerenter', 'focus', 'click'].forEach((event) => {
        button.addEventListener(event, () => selectDay(day, button));
      });
      button.addEventListener('keydown', (event) => {
        const targets = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: days.length - 1 };
        if (!(event.key in targets)) return;
        event.preventDefault();
        const target = bars[Math.min(days.length - 1, Math.max(0, targets[event.key]))];
        target.focus({ preventScroll: true });
        target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      });
      return button;
    });
    plot.replaceChildren(...bars);
    byId('x-axis').replaceChildren(...[0, Math.floor((days.length - 1) / 2), days.length - 1].map((index) => {
      const label = document.createElement('span');
      label.textContent = shortDate.format(dateValue(days[index].date));
      return label;
    }));
    selectDay(days.at(-1), bars.at(-1));
    byId('plot-scroll').scrollLeft = byId('plot-scroll').scrollWidth;
  }

  async function loadUsage() {
    if (usageInFlight) return usageInFlight;
    const day = beijingDay();
    if (attemptedDay === day && (usage || Date.now() < retryAfter)) return;
    attemptedDay = day;
    usageInFlight = (async () => {
      byId('usage-section').setAttribute('aria-busy', 'true');
      try {
        const data = await fetchJSON('/api/dashboard/usage');
        if (!validUsage(data)) throw new Error('Unavailable');
        usage = data;
      } catch (_) {
        usage = null;
        retryAfter = Date.now() + 60000;
      } finally {
        renderUsage();
        byId('usage-section').setAttribute('aria-busy', 'false');
        usageInFlight = null;
        clearTimeout(retryTimer);
        retryTimer = setTimeout(refreshUsage, 60000);
      }
    })();
    return usageInFlight;
  }

  function refreshUsage() {
    clearTimeout(retryTimer);
    if (document.visibilityState === 'hidden') return;
    loadUsage();
    retryTimer = setTimeout(refreshUsage, 60000);
  }

  async function loadUpdates(id, url) {
    const column = byId(id);
    const status = column.querySelector('.updates-status');
    const list = column.querySelector('.update-list');
    const more = column.querySelector('.more-updates');
    try {
      const data = await fetchJSON(url);
      if (!Array.isArray(data.updates) || !data.updates.every((entry) => entry
        && ['version', 'date', 'title', 'summary'].every((field) => typeof entry[field] === 'string')
        && Number.isFinite(Date.parse(entry.date)) && Array.isArray(entry.items)
        && entry.items.every((item) => typeof item === 'string'))) throw new Error('Unavailable');
      const updates = [...data.updates].sort((a, b) => b.version.localeCompare(a.version, 'en', { numeric: true }) || Date.parse(b.date) - Date.parse(a.date));
      list.replaceChildren(...updates.map((entry, index) => {
        const item = document.createElement('li');
        item.className = 'update-entry';
        item.hidden = index >= 3;
        const date = document.createElement('time');
        date.textContent = entry.date;
        const parsedDate = new Date(entry.date);
        date.dateTime = [parsedDate.getFullYear(), String(parsedDate.getMonth() + 1).padStart(2, '0'), String(parsedDate.getDate()).padStart(2, '0')].join('-');
        const title = document.createElement('h5');
        title.textContent = entry.title;
        item.append(date, title);
        if (entry.summary) {
          const summary = document.createElement('p');
          summary.textContent = entry.summary;
          item.append(summary);
        }
        if (entry.items.length) {
          const bullets = document.createElement('ul');
          entry.items.forEach((text) => {
            const bullet = document.createElement('li');
            bullet.textContent = text;
            bullets.append(bullet);
          });
          item.append(bullets);
        }
        return item;
      }));
      status.hidden = updates.length > 0;
      status.textContent = 'No updates yet.';
      more.hidden = updates.length <= 3;
      more.addEventListener('click', () => {
        const expanded = more.getAttribute('aria-expanded') !== 'true';
        more.setAttribute('aria-expanded', String(expanded));
        more.textContent = expanded ? 'Less' : 'More';
        [...list.children].forEach((item, index) => { item.hidden = !expanded && index >= 3; });
        list.scrollTop = 0;
        if (expanded) list.focus({ preventScroll: true });
      });
    } catch (_) {
      status.textContent = 'Unavailable';
      status.hidden = false;
    }
  }

  document.querySelectorAll('input[name="period"]').forEach((input) => {
    input.addEventListener('change', () => { period = Number(input.value); renderUsage(); });
  });
  window.addEventListener('pageshow', refreshUsage);
  document.addEventListener('visibilitychange', refreshUsage);
  loadUsage();
  loadUpdates('omics-updates', '/static/dashboard/potato-omics-updates.json');
  loadUpdates('agent-updates', '/static/lite/update-notes.json');
})();
