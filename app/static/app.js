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

  if (messages) messages.scrollTop = messages.scrollHeight;

  let dirty = false;
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
  if (composer) composer.addEventListener('submit', () => { dirty = false; });

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
      if (bodyInput && !bodyInput.value && (!attachmentInput || !attachmentInput.files.length)) dirty = false;
    });
  }

  const refreshWhenSafe = () => {
    if (!dirty && (!bodyInput || document.activeElement !== bodyInput)) {
      window.location.reload();
      return;
    }
    if (liveNotice) {
      liveNotice.hidden = false;
      liveNotice.onclick = () => window.location.reload();
    }
  };

  if (window.EventSource) {
    const events = new EventSource('/events');
    events.addEventListener('refresh', refreshWhenSafe);
  }
})();
