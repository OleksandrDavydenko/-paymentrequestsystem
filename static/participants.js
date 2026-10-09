// Картка заявки: «+ Додати учасника» — підказки з користувачів системи, вибір додає учасника.
(function () {
  const input = document.getElementById('participant-search');
  if (!input) return;
  const form = input.closest('form');
  const hidden = document.getElementById('participant-email');
  const list = document.getElementById('participant-results');
  let timer, items = [], active = -1;

  function close() { list.hidden = true; list.innerHTML = ''; items = []; active = -1; }

  function pick(u) {
    hidden.value = u.email;
    input.value = u.name;
    close();
    form.submit();
  }

  function render(users, error) {
    list.innerHTML = '';
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
    hidden.value = '';
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    timer = setTimeout(async () => {
      try {
        const r = await fetch(input.dataset.url + '?q=' + encodeURIComponent(q), {headers: {'X-Requested-With': 'fetch'}});
        const data = await r.json();
        render(data.users || []);
      } catch (e) {
        render([], 'Не вдалося завантажити список користувачів');
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
      e.preventDefault();  // без вибору зі списку форму не відправляємо
      if (active >= 0 && items[active]) pick(items[active]);
    } else if (e.key === 'Escape') {
      close();
    }
  });
  input.addEventListener('blur', () => setTimeout(close, 150));
})();
