(() => {
  'use strict';

  if (document.getElementById('feedback-button')) return;

  const trigger = document.createElement('button');
  trigger.id = 'feedback-button';
  trigger.className = 'feedback-trigger';
  trigger.type = 'button';
  trigger.textContent = 'Feedback';
  trigger.setAttribute('aria-haspopup', 'dialog');
  trigger.setAttribute('aria-controls', 'feedback-modal');

  const modal = document.createElement('div');
  modal.id = 'feedback-modal';
  modal.className = 'feedback-modal';
  modal.hidden = true;
  modal.dataset.state = 'idle';
  modal.innerHTML = `
    <div class="feedback-backdrop" data-feedback-close></div>
    <section class="feedback-dialog" role="dialog" aria-modal="true" aria-labelledby="feedback-title">
      <header class="feedback-dialog-header">
        <h2 id="feedback-title">Send feedback</h2>
        <button class="feedback-close" type="button" aria-label="Close feedback dialog" title="Close">&times;</button>
      </header>
      <form id="feedback-form" class="feedback-form">
        <label class="feedback-field" for="feedback-message">
          <span>Feedback</span>
          <textarea id="feedback-message" name="message" maxlength="5000" required></textarea>
        </label>
        <label class="feedback-field" for="feedback-contact-email">
          <span>Contact email <span class="feedback-optional">(optional)</span></span>
          <input id="feedback-contact-email" name="contact_email" type="email" maxlength="254" autocomplete="email" />
        </label>
        <p id="feedback-status" class="feedback-status" role="status" aria-live="polite" tabindex="-1" hidden></p>
        <div class="feedback-actions">
          <button class="feedback-cancel" type="button">Cancel</button>
          <button class="feedback-submit" type="submit">Send feedback</button>
        </div>
      </form>
    </section>
  `;

  document.body.append(trigger, modal);

  const dialog = modal.querySelector('.feedback-dialog');
  const backdrop = modal.querySelector('[data-feedback-close]');
  const closeButton = modal.querySelector('.feedback-close');
  const cancelButton = modal.querySelector('.feedback-cancel');
  const form = modal.querySelector('#feedback-form');
  const messageInput = modal.querySelector('#feedback-message');
  const contactInput = modal.querySelector('#feedback-contact-email');
  const status = modal.querySelector('#feedback-status');
  const submitButton = modal.querySelector('.feedback-submit');
  const workspace = document.getElementById('workspace-view');
  let previousFocus = null;
  let submitting = false;

  const workspaceIsActive = () => (
    workspace
    && !workspace.hidden
    && workspace.style.display !== 'none'
  );

  const setStatus = (kind, message) => {
    status.hidden = !message;
    status.dataset.kind = kind;
    status.textContent = message;
    modal.dataset.state = kind || 'idle';
  };

  const focusableElements = () => Array.from(dialog.querySelectorAll(
    'button:not([disabled]), textarea:not([disabled]), input:not([disabled])'
  ));

  const closeModal = () => {
    if (modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove('feedback-dialog-open');
    const focusTarget = previousFocus;
    previousFocus = null;
    if (focusTarget && document.contains(focusTarget) && !focusTarget.hidden) {
      focusTarget.focus();
    }
  };

  const openModal = () => {
    if (workspaceIsActive()) return;
    previousFocus = document.activeElement;
    setStatus('', '');
    modal.hidden = false;
    document.body.classList.add('feedback-dialog-open');
    window.requestAnimationFrame(() => messageInput.focus());
  };

  const syncWorkspaceVisibility = () => {
    const workspaceActive = workspaceIsActive();
    trigger.hidden = Boolean(workspaceActive);
    trigger.setAttribute('aria-hidden', workspaceActive ? 'true' : 'false');
    if (workspaceActive && !modal.hidden && modal.dataset.state !== 'success') {
      closeModal();
    }
  };

  const handleKeydown = (event) => {
    if (modal.hidden) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeModal();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = focusableElements();
    if (!focusable.length) {
      event.preventDefault();
      dialog.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const submitFeedback = async (event) => {
    event.preventDefault();
    if (submitting) return;

    const message = messageInput.value.trim();
    messageInput.setCustomValidity(message ? '' : 'Please enter your feedback.');
    if (!form.reportValidity()) return;

    submitting = true;
    submitButton.disabled = true;
    submitButton.textContent = 'Sending...';
    setStatus('pending', 'Sending feedback...');

    try {
      const response = await fetch('/api/feedback', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message,
          contact_email: contactInput.value.trim(),
          page_path: window.location.pathname || '/',
        }),
      });
      if (!response.ok) throw new Error(`feedback request failed: ${response.status}`);
      form.reset();
      setStatus('success', 'Thank you. Your feedback was sent.');
      status.focus();
    } catch (_error) {
      setStatus('error', 'Feedback could not be sent. Please try again later.');
      status.focus();
    } finally {
      submitting = false;
      submitButton.disabled = false;
      submitButton.textContent = 'Send feedback';
    }
  };

  trigger.addEventListener('click', openModal);
  closeButton.addEventListener('click', closeModal);
  cancelButton.addEventListener('click', closeModal);
  backdrop.addEventListener('click', closeModal);
  form.addEventListener('submit', submitFeedback);
  messageInput.addEventListener('input', () => messageInput.setCustomValidity(''));
  document.addEventListener('keydown', handleKeydown);

  if (workspace) {
    new MutationObserver(syncWorkspaceVisibility).observe(workspace, {
      attributes: true,
      attributeFilter: ['hidden', 'style', 'class'],
    });
  }
  syncWorkspaceVisibility();
})();
