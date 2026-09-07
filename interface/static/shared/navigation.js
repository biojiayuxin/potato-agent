(() => {
  'use strict';
  const modules = [
    { id: 'lite', label: 'Potato Agent', href: '/lite' },
    { id: 'genomes', label: 'Genomes', href: '/genomes', large: true },
    { id: 'genes', label: 'Genes', href: '/genes' },
    { id: 'bulk_rnaseq', label: 'Gene Expression', href: '/bulk-rnaseq', large: true, group: 'expression' },
    { id: 'wgcna', label: 'WGCNA Network', href: '/wgcna', large: true, group: 'expression' },
    { id: 'spatial', label: 'Spatial Expression', href: '/spatial', large: true, group: 'expression' },
    { id: 'variants', label: 'Variants', group: 'more' },
    { id: 'germplasm', label: 'Germplasm', group: 'more' },
    { id: 'dashboard', label: 'Dashboard', href: '/dashboard' },
    { id: 'about', label: 'About', href: '/about' },
  ];
  const browser = { id: 'genome_browser', label: 'Genome Browser', large: true };
  const nav = document.querySelector('[data-portal-module]');
  if (!nav) return;
  const compact = matchMedia('(max-width: 960px)');
  const small = matchMedia('(max-width: 800px)');
  const notice = nav.dataset.portalModule === 'notice';
  const source = notice ? new URLSearchParams(location.search).get('module') : nav.dataset.portalModule;
  const current = [...modules, browser].find(item => item.id === source);
  const noticeURL = id => `/static/lite/high-resolution-required.html?module=${encodeURIComponent(id)}`;
  function checkDevice() {
    if (!notice && current?.large && small.matches) location.replace(noticeURL(current.id));
  }
  checkDevice();
  const activeId = current === browser ? 'genomes' : current?.id;
  function element(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    if (text) node.textContent = text;
    return node;
  }
  function icon(name) {
    const node = element('span', `portal-nav-icon portal-icon-${name}`);
    node.setAttribute('aria-hidden', 'true');
    return node;
  }
  function link(item) {
    const node = element(item.href ? 'a' : 'button', 'portal-nav-item', item.label);
    if (item.href) {
      node.href = small.matches && item.large ? noticeURL(item.id) : item.href;
      node.dataset.module = item.id;
    } else {
      node.type = 'button';
      node.disabled = true;
    }
    if (item.id === activeId) {
      node.classList.add('active');
      node.setAttribute('aria-current', 'page');
    }
    return node;
  }
  function button(text, label) {
    const node = element('button', 'portal-nav-item', text);
    node.type = 'button';
    node.setAttribute('aria-label', label);
    node.title = label;
    node.setAttribute('aria-expanded', 'false');
    return node;
  }
  const panel = element('nav', 'portal-nav-panel');
  panel.setAttribute('aria-label', 'Module links');
  panel.id = 'portal-navigation-panel';
  panel.hidden = true;
  document.body.append(panel);
  let trigger = null;
  function close(restore = true) {
    panel.hidden = true;
    if (trigger) {
      trigger.setAttribute('aria-expanded', 'false');
      if (restore) trigger.focus({ preventScroll: true });
    }
    trigger = null;
  }
  function position() {
    if (!trigger) return;
    const rect = (compact.matches ? nav : trigger).getBoundingClientRect();
    const top = Math.max(0, rect.bottom + 4);
    panel.style.top = `${top}px`;
    panel.style.left = compact.matches ? '0px' : `${Math.max(0, Math.min(rect.left, innerWidth - 280))}px`;
    panel.style.maxHeight = `${Math.max(0, innerHeight - top - 8)}px`;
  }
  function open(node, group) {
    if (trigger === node) { close(); return; }
    close(false);
    trigger = node;
    panel.replaceChildren();
    let previousGroup;
    for (const item of modules.filter(item => !group || item.group === group)) {
      if (!group && item.group && item.group !== previousGroup) {
        panel.append(element('div', 'portal-nav-group-label', item.group === 'expression' ? 'Expression' : 'Coming soon'));
      }
      previousGroup = item.group;
      panel.append(link(item));
    }
    node.setAttribute('aria-expanded', 'true');
    panel.hidden = false;
    position();
  }
  const caption = element('span', 'portal-nav-caption', current?.label || 'High-resolution device required');
  nav.append(caption);
  const toggle = button('', 'Module navigation');
  toggle.append(icon('menu'));
  toggle.classList.add('portal-nav-toggle');
  nav.append(toggle);
  toggle.addEventListener('click', () => open(toggle));
  const desktop = element('div', 'portal-nav-desktop');
  nav.append(desktop);
  modules.filter(item => !item.group).forEach(item => {
    if (item.id === 'dashboard') {
      for (const [group, label] of [['expression', 'Expression'], ['more', 'Coming soon']]) {
        const control = button(label, label);
        control.append(icon('chevron-down'));
        if (group === current?.group) control.classList.add('active');
        desktop.append(control);
        control.addEventListener('click', () => open(control, group));
      }
    }
    desktop.append(link(item));
  });
  nav.querySelectorAll('button').forEach(node => node.setAttribute('aria-controls', panel.id));
  document.addEventListener('click', event => {
    if (trigger && !panel.contains(event.target) && !trigger.contains(event.target)) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && trigger) { event.preventDefault(); close(); }
    if (!panel.contains(event.target) && event.target !== trigger) return;
    const links = [...panel.querySelectorAll('a')];
    const index = links.indexOf(document.activeElement);
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) && links.length) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? links.length - 1
        : index < 0 ? (event.key === 'ArrowDown' ? 0 : links.length - 1)
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + links.length) % links.length;
      links[next].focus();
    }
  });
  document.addEventListener('focusin', event => {
    if (trigger && !panel.contains(event.target) && event.target !== trigger) close(false);
  });
  panel.addEventListener('keydown', event => {
    if (event.key === 'Tab' && !event.shiftKey && event.target === panel.querySelector('a:last-of-type')) close();
  });
  compact.addEventListener('change', () => close());
  small.addEventListener('change', () => {
    checkDevice();
    document.querySelectorAll('[data-module]').forEach(node => {
      const item = modules.find(item => item.id === node.dataset.module);
      node.href = small.matches && item.large ? noticeURL(item.id) : item.href;
    });
  });
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, true);
  new ResizeObserver(position).observe(nav);
  if (notice && current) {
    document.querySelector('.high-resolution-message .muted').textContent = `${current.label} requires a display wider than 800px. Please use a larger-screen device.`;
  }
})();
