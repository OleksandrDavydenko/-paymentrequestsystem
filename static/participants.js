// Картка заявки: учасники. Додавання й вилучення — лише в списку на сторінці; зберігаються
// разом із заявкою («Записати» / «OK» / «Відправити») або кнопкою «Зберегти учасників» (коли заявку не редагують).
(function () {
  const box = document.getElementById('participants');
  const input = document.getElementById('participant-search');
  if (!box || !input) return;
  const chips = document.getElementById('pchips');
  const inputs = document.getElementById('participants-inputs');
  const list = document.getElementById('participant-results');
  const pending = document.getElementById('participants-pending');
  const saveForm = document.getElementById('participants-form');
  const formId = box.dataset.form;
  const initial = JSON.parse(box.dataset.state || '[]');
  const saved = JSON.parse(box.dataset.saved || '[]').map(e => e.toLowerCase());
  let state = initial.slice();
  let timer, items = [], active = -1;

  function changed() {
    const now = state.map(p => p.email.toLowerCase());
    return now.length !== saved.length || now.some(e => !saved.includes(e));
  }

  function render() {
    chips.innerHTML = '';
    if (!state.length) {
      const none = document.createElement('span');
      none.className = 'muted';
      none.textContent = 'поки немає';
      chips.appendChild(none);
    }
    state.forEach(p => {
      const chip = document.createElement('span');
      const isNew = !saved.includes(p.email.toLowerCase());
      chip.className = 'pchip' + (isNew ? ' pchip-new' : '');
      chip.title = isNew ? p.email + ' · буде додано після збереження' : (p.title || p.email);
      chip.append(p.name);
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'pchip-x';
      x.textContent = '✕';
      x.title = 'Прибрати з учасників';
      x.setAttribute('aria-label', 'Прибрати ' + p.name);
      x.addEventListener('click', () => { state = state.filter(q => q !== p); render(); });
      chip.appendChild(x);
      chips.appendChild(chip);
    });
    // приховані поля для форми, яка збереже учасників
    inputs.querySelectorAll('input[name=participants]').forEach(i => i.remove());
    state.forEach(p => {
      const h = document.createElement('input');
      h.type = 'hidden'; h.name = 'participants'; h.value = p.email; h.setAttribute('form', formId);
      inputs.appendChild(h);
    });
    const dirty = changed();
    if (pending) pending.hidden = !dirty;
    if (saveForm) saveForm.hidden = !dirty;
    box.classList.toggle('is-dirty', dirty);
  }

  function close() { list.hidden = true; list.innerHTML = ''; items = []; active = -1; }

  function pick(u) {
    if (!state.some(p => p.email.toLowerCase() === u.email.toLowerCase())) state.push({email: u.email, name: u.name});
    input.value = '';
    close();
    render();
    input.focus();
  }

  function show(users, error) {
    list.innerHTML = '';
    users = users.filter(u => !state.some(p => p.email.toLowerCase() === u.email.toLowerCase()));
    items = users;
    active = users.length ? 0 : -1;
    if (error || !users.length) {
      const li = document.createElement('li');
      li.className = 'muted';
      li.textContent = error || 'Нікого не знайдено серед користувачів системи';
      list.appendChild(li);
    }
    users.forEach((u, i) => {
      const li = document.createElement('li');
      li.innerHTML = '<strong></strong> <span class="muted"></span>';
      li.children[0].textContent = u.name;
      li.children[1].textContent = u.email;
      li.classList.toggle('active', i === active);
      li.addEventListener('mousedown', e => { e.preventDefault(); pick(u); });
      list.appendChild(li);
    });
    list.hidden = false;
  }

  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    timer = setTimeout(async () => {
      try {
        const r = await fetch(input.dataset.url + '?q=' + encodeURIComponent(q), {headers: {'X-Requested-With': 'fetch'}});
        const data = await r.json();
        show(data.users || []);
      } catch (e) {
        show([], 'Не вдалося завантажити список користувачів');
      }
    }, 250);
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!items.length) return;
      e.preventDefault();
      active = (active + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
      [...list.children].forEach((li, i) => li.classList.toggle('active', i === active));
    } else if (e.key === 'Enter') {
      e.preventDefault();  // Enter у пошуку не відправляє форму заявки
      if (active >= 0 && items[active]) pick(items[active]);
    } else if (e.key === 'Escape') {
      close();
    }
  });
  input.addEventListener('blur', () => setTimeout(close, 150));

  const reset = document.getElementById('participants-reset');
  if (reset) reset.addEventListener('click', () => { state = initial.filter(p => saved.includes(p.email.toLowerCase())); render(); });

  // Не загубити незбережених учасників при переході зі сторінки без збереження
  window.addEventListener('beforeunload', e => {
    if (!changed() || window.__submitting) return;
    e.preventDefault();
    e.returnValue = '';
  });
  document.addEventListener('submit', () => { window.__submitting = true; }, true);

  render();
})();
