// Згортання розділів адмінки: клік по заголовку розділу ([data-collapsible] > .toolbar).
// Стан запам'ятовується в цьому браузері; розділ, на який веде посилання (#mail-settings), завжди відкритий.
(function () {
  const KEY = 'admin-sections';
  const sections = [...document.querySelectorAll('[data-collapsible]')];
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { saved = {}; }

  function persist() {
    try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) { /* приватний режим тощо */ }
  }

  function setOpen(sec, open, remember) {
    sec.classList.toggle('collapsed', !open);
    const title = sec.querySelector(':scope > .toolbar > h1, :scope > .toolbar > h2');
    if (title) title.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (remember) { saved[sec.id] = open; persist(); }
  }

  sections.forEach(sec => {
    const bar = sec.querySelector(':scope > .toolbar');
    const title = bar && bar.querySelector('h1, h2');
    if (!title) return;
    bar.classList.add('sec-head');
    title.setAttribute('role', 'button');
    title.tabIndex = 0;
    const open = sec.id in saved ? saved[sec.id] : sec.dataset.default === 'open';
    setOpen(sec, open, false);
    bar.addEventListener('click', e => {
      // Кнопки й форми в заголовку («+ Додати користувача», «Оновити зараз») розділ не згортають
      // (лише ті, що всередині заголовка: сам розділ теж може бути формою, як «Налаштування процесу»)
      const control = e.target.closest('button, a, input, select, form');
      if (control && bar.contains(control)) return;
      setOpen(sec, sec.classList.contains('collapsed'), true);
    });
    title.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        setOpen(sec, sec.classList.contains('collapsed'), true);
      }
    });
  });

  function openTarget() {
    const target = location.hash && document.getElementById(location.hash.slice(1));
    const sec = target && target.closest('[data-collapsible]');
    if (sec) { setOpen(sec, true, true); sec.scrollIntoView({ block: 'start' }); }
  }
  openTarget();
  window.addEventListener('hashchange', openTarget);

  const all = (open) => sections.forEach(sec => setOpen(sec, open, true));
  const ex = document.getElementById('sec-expand-all');
  const col = document.getElementById('sec-collapse-all');
  if (ex) ex.addEventListener('click', () => all(true));
  if (col) col.addEventListener('click', () => all(false));
})();
