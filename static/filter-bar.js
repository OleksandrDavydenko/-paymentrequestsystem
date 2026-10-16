// Панель фільтрів: пошук із затримкою, кнопки-фільтри (один відкритий), пошук усередині, «Останні вибрані», телефон.
(function () {
  const bar = document.querySelector('[data-fbar]');
  if (!bar || bar.dataset.ready) return;
  bar.dataset.ready = '1';
  const form = bar.closest('form');
  const pops = [...bar.querySelectorAll('details.fpop')];
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* приватний режим */ } },
  };

  // Пошук одним рядком — застосовується сам, коли користувач перестав друкувати
  const q = bar.querySelector('[data-fbar-q]');
  let timer;
  if (q) {
    q.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        try { sessionStorage.setItem('fbar-focus', '1'); } catch (e) { /* ignore */ }
        form.submit();
      }, 650);
    });
    q.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(timer); form.submit(); } });
    try {
      if (sessionStorage.getItem('fbar-focus')) {
        sessionStorage.removeItem('fbar-focus');
        q.focus();
        q.setSelectionRange(q.value.length, q.value.length);
      }
    } catch (e) { /* ignore */ }
  }

  // Відкрито лише один фільтр; клік поза ним — закрити
  pops.forEach(p => p.addEventListener('toggle', () => {
    if (!p.open) return;
    pops.forEach(o => { if (o !== p) o.open = false; });
    showRecent(p);
    const s = p.querySelector('.fpop-search');
    if (s && window.matchMedia('(min-width: 761px)').matches) s.focus();
  }));
  document.addEventListener('click', e => { if (!e.target.closest('details.fpop')) pops.forEach(p => { p.open = false; }); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') pops.forEach(p => { p.open = false; }); });

  // Пошук усередині фільтра
  pops.forEach(p => {
    const s = p.querySelector('.fpop-search');
    if (!s) return;
    const opts = [...p.querySelectorAll('.fpop-opt')];
    const found = p.querySelector('.fpop-found');
    s.addEventListener('keydown', e => { if (e.key === 'Enter') e.preventDefault(); });
    s.addEventListener('input', () => {
      const t = s.value.trim().toLowerCase();
      let n = 0;
      opts.forEach(o => { const hit = !t || o.dataset.label.includes(t); o.hidden = !hit; if (hit) n++; });
      p.querySelectorAll('.fpop-group').forEach(g => { g.hidden = !!t; });
      found.hidden = !t;
      found.textContent = `Знайдено ${n} з ${opts.length}`;
    });
  });

  // «Очистити» в межах одного фільтра
  bar.querySelectorAll('[data-fpop-clear]').forEach(b => b.addEventListener('click', () => {
    b.closest('details').querySelectorAll('input[type=checkbox]').forEach(c => { c.checked = false; });
    form.submit();
  }));

  // «Останні вибрані» — запам'ятовуються при застосуванні й піднімаються нагору списку
  form.addEventListener('submit', () => {
    pops.forEach(p => {
      const key = 'fbar-recent:' + p.dataset.key;
      const picked = [...p.querySelectorAll('input:checked')].map(c => c.value);
      if (!picked.length) return;
      const prev = store.get(key) || [];
      store.set(key, [...new Set([...picked, ...prev])].slice(0, 5));
    });
  });
  function showRecent(p) {
    if (p.dataset.recentDone) return;
    p.dataset.recentDone = '1';
    const recent = store.get('fbar-recent:' + p.dataset.key) || [];
    const list = p.querySelector('.fpop-list');
    const opts = [...list.querySelectorAll('.fpop-opt')];
    if (opts.length < 8) return;
    const hits = recent.map(v => opts.find(o => o.dataset.value === v)).filter(Boolean);
    if (!hits.length) return;
    const head = document.createElement('div');
    head.className = 'fpop-group muted';
    head.textContent = 'Останні вибрані';
    const rest = document.createElement('div');
    rest.className = 'fpop-group muted';
    rest.textContent = 'Усі';
    list.prepend(head, ...hits, rest);
  }

  // Телефон: фільтри ховаються за кнопкою «Фільтри»
  const toggle = bar.querySelector('[data-fbar-toggle]');
  if (toggle) toggle.addEventListener('click', () => {
    const open = bar.classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
})();
