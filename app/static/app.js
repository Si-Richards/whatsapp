(() => {
  const messages = document.getElementById('messages');
  const composer = document.getElementById('composer');
  const bodyInput = document.getElementById('message-body');
  const attachmentInput = document.getElementById('attachment');
  const attachmentLabel = document.getElementById('attachment-label');
  const replyToInput = document.getElementById('reply-to');
  const replyBar = document.getElementById('reply-bar');
  const replyText = document.getElementById('reply-text');
  const replyCancel = document.getElementById('reply-cancel');
  const liveNotice = document.getElementById('live-notice');

  const scrollToLatest = () => {
    if (!messages) return;
    messages.scrollTop = messages.scrollHeight;
  };

  // Scroll once the DOM is ready and again after media finishes sizing.
  requestAnimationFrame(scrollToLatest);
  window.addEventListener('load', scrollToLatest, { once: true });
  if (messages) {
    messages.querySelectorAll('img,video,audio').forEach((media) => {
      media.addEventListener('load', scrollToLatest, { once: true });
      media.addEventListener('loadedmetadata', scrollToLatest, { once: true });
    });
  }

  let dirty = false;
  let refreshTimer = null;
  let refreshInProgress = false;
  let lastRefreshRequest = 0;

  const markDirty = () => { dirty = true; };
  if (bodyInput) bodyInput.addEventListener('input', markDirty);

  if (attachmentInput) {
    attachmentInput.addEventListener('change', () => {
      dirty = Boolean(attachmentInput.files && attachmentInput.files.length);
      if (attachmentLabel) {
        attachmentLabel.textContent = dirty ? attachmentInput.files[0].name : 'Attach';
      }
    });
  }

  if (composer) {
    composer.addEventListener('submit', () => {
      dirty = false;
      refreshInProgress = true;
    });
  }

  document.querySelectorAll('[data-reply-id]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!replyToInput || !replyBar || !replyText) return;
      replyToInput.value = button.dataset.replyId || '';
      replyText.textContent = button.dataset.replyText || 'WhatsApp message';
      replyBar.hidden = false;
      dirty = true;
      if (bodyInput) bodyInput.focus();
    });
  });

  if (replyCancel) {
    replyCancel.addEventListener('click', () => {
      if (replyToInput) replyToInput.value = '';
      if (replyBar) replyBar.hidden = true;
      if (bodyInput && !bodyInput.value && (!attachmentInput || !attachmentInput.files.length)) {
        dirty = false;
      }
    });
  }

  const canAutoRefresh = () => {
    if (dirty || refreshInProgress || document.hidden) return false;
    if (bodyInput && document.activeElement === bodyInput) return false;
    return true;
  };

  const doRefresh = () => {
    if (refreshInProgress) return;
    refreshInProgress = true;
    window.location.reload();
  };

  const scheduleRefresh = () => {
    const now = Date.now();
    lastRefreshRequest = now;

    // Several Meta status webhooks can arrive for a single message. Collapse
    // those into one refresh rather than repeatedly reloading the whole UI.
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => {
      if (Date.now() - lastRefreshRequest < 1200) return;

      if (canAutoRefresh()) {
        doRefresh();
        return;
      }

      if (liveNotice) {
        liveNotice.hidden = false;
        liveNotice.onclick = doRefresh;
      }
    }, 1500);
  };

  if (window.EventSource) {
    const events = new EventSource('/events');
    events.addEventListener('refresh', scheduleRefresh);
    events.onerror = () => {
      // EventSource reconnects automatically. Do not reload the page merely
      // because the streaming connection was briefly interrupted.
    };
    window.addEventListener('beforeunload', () => events.close(), { once: true });
  }
})();
