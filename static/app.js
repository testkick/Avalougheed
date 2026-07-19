/* Ava Lougheed presale — interactions */
(function () {
  const $ = (sel) => document.querySelector(sel);
  const emailRe = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  /* ---------- smooth scroll ---------- */
  document.querySelectorAll('[data-scroll-email]').forEach((el) =>
    el.addEventListener('click', () =>
      $('#email-anchor').scrollIntoView({ behavior: 'smooth' })
    )
  );

  /* ---------- email capture ---------- */
  const emailForm = $('#email-form');
  const emailInput = $('#email-input');
  const emailSuccess = $('#email-success');
  const emailError = $('#email-error');

  emailForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    emailError.hidden = true;
    const email = emailInput.value.trim();
    if (!emailRe.test(email)) {
      emailError.textContent = 'Please enter a valid email address.';
      emailError.hidden = false;
      return;
    }
    const btn = emailForm.querySelector('button');
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const res = await fetch('/api/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Something went wrong.');
      emailForm.hidden = true;
      emailSuccess.textContent = `You're on the list, ${email} — we'll be in touch.`;
      emailSuccess.hidden = false;
    } catch (err) {
      emailError.textContent = err.message || 'Something went wrong — please try again.';
      emailError.hidden = false;
      btn.disabled = false;
      btn.textContent = 'Join';
    }
  });

  /* ---------- deposit modal ---------- */
  const modal = $('#deposit-modal');
  const formView = $('#deposit-form-view');
  const successView = $('#deposit-success-view');
  const nameInput = $('#deposit-name');
  const depEmailInput = $('#deposit-email');
  const payBtn = $('#pay-button');
  const depError = $('#deposit-error');

  function openModal(showSuccess) {
    formView.hidden = !!showSuccess;
    successView.hidden = !showSuccess;
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    if (!showSuccess) nameInput.focus();
  }
  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = '';
  }

  document.querySelectorAll('[data-open-deposit]').forEach((el) =>
    el.addEventListener('click', () => openModal(false))
  );
  document.querySelectorAll('[data-close-deposit]').forEach((el) =>
    el.addEventListener('click', closeModal)
  );
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.hidden) closeModal();
  });

  function updatePayButton() {
    payBtn.disabled = !(nameInput.value.trim() && emailRe.test(depEmailInput.value.trim()));
  }
  nameInput.addEventListener('input', updatePayButton);
  depEmailInput.addEventListener('input', updatePayButton);

  payBtn.addEventListener('click', async () => {
    depError.hidden = true;
    payBtn.disabled = true;
    payBtn.textContent = 'Redirecting…';
    try {
      const res = await fetch('/api/create-checkout-session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: nameInput.value.trim(),
          email: depEmailInput.value.trim(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not start checkout.');
      window.location.href = data.url;
    } catch (err) {
      depError.textContent = err.message || 'Could not start checkout — please try again.';
      depError.hidden = false;
      payBtn.disabled = false;
      payBtn.textContent = 'Continue to secure payment';
    }
  });

  /* ---------- post-checkout confirmation ---------- */
  const params = new URLSearchParams(window.location.search);
  if (params.get('reserved') === '1' && params.get('session_id')) {
    fetch('/api/checkout-status?session_id=' + encodeURIComponent(params.get('session_id')))
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (s && s.paid) {
          const who = s.name ? `, ${s.name}` : '';
          const where = s.email ? ` We'll email ${s.email}` : ` We'll email you`;
          $('#deposit-success-copy').textContent =
            `Thank you${who} — your $25 deposit is confirmed.${where} when it's time to complete your order.`;
          openModal(true);
        }
      })
      .catch(() => {});
    history.replaceState(null, '', window.location.pathname + '#reserve');
  } else if (params.get('canceled') === '1') {
    history.replaceState(null, '', window.location.pathname + '#reserve');
  }
})();
