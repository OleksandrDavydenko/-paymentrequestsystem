// Інструменти таблиць-списків (table.sortable):
//  • сортування кліком по заголовку (значення з data-sort клітинки або з тексту);
//  • зміна ширини колонок перетягуванням правого краю заголовка (подвійний клік — автоширина);
//  • меню «Колонки»: показати/сховати колонки, скинути налаштування;
//  • на мобільному (картки) — select «Сортувати».
// Налаштування кожної таблиці зберігаються в localStorage.
(function () {
  const collator = new Intl.Collator('uk', { numeric: true, sensitivity: 'base' });
  const LOCKED = ['Номер', 'Сума'];   // ці колонки сховати не можна
  const MIN_WIDTH = 60;

  // ---------- збереження налаштувань ----------
  function key(table) {
    return 'tbl:' + location.pathname + ':' + (table.id || [...document.querySelectorAll('table.sortable')].indexOf(table));
  }
  function loadState(table) {
    try { return JSON.parse(localStorage.getItem(key(table)) || '{}') || {}; } catch (e) { return {}; }
  }
  function saveState(table) {
    try { localStorage.setItem(key(table), JSON.stringify(table._state)); } catch (e) { /* приватний режим */ }
  }
  function clearState(table) {
    try { localStorage.removeItem(key(table)); } catch (e) { /* ignore */ }
  }

  // ---------- сортування ----------
  function cellValue(row, idx) {
    const cell = row.children[idx];
    if (!cell) return '';
    return cell.dataset.sort !== undefined ? cell.dataset.sort : cell.textContent.trim();
  }
  function compare(a, b) {
    const na = Number(a), nb = Number(b);
    if (a !== '' && b !== '' && !isNaN(na) && !isNaN(nb)) return na - nb;
    return collator.compare(a, b);
  }
  function sortTable(table, idx, dir) {
    const tbody = table.tBodies[0];
    const rows = [...tbody.rows].filter(r => !r.querySelector('td.empty'));
    rows.sort((a, b) => compare(cellValue(a, idx), cellValue(b, idx)) * (dir === 'desc' ? -1 : 1));
    rows.forEach(r => tbody.appendChild(r));
    table._headers.forEach((th, i) => {
      th.classList.toggle('sorted-asc', i === idx && dir === 'asc');
      th.classList.toggle('sorted-desc', i === idx && dir === 'desc');
      if (th.classList.contains('sortable-th')) {
        th.setAttribute('aria-sort', i === idx ? (dir === 'asc' ? 'ascending' : 'descending') : 'none');
      }
    });
    if (table._sortSelect) table._sortSelect.value = idx + ':' + dir;
  }

  // ---------- показ/приховування колонок ----------
  function setHidden(table, idx, hidden) {
    table._headers[idx].hidden = hidden;
    if (table._cols[idx]) table._cols[idx].hidden = hidden;
    for (const row of table.tBodies[0].rows) {
      const cell = row.children[idx];
      if (cell && !cell.classList.contains('empty')) cell.hidden = hidden;
    }
    if (table.tFoot) for (const row of table.tFoot.rows) if (row.children[idx]) row.children[idx].hidden = hidden;
  }

  // ---------- ширина колонок ----------
  function freezeLayout(table) {
    if (table.classList.contains('fixed-layout')) return;
    // Зафіксувати поточні ширини всіх видимих колонок, далі змінюється лише та, яку тягнуть
    table._headers.forEach((th, i) => {
      if (!th.hidden) table._cols[i].style.width = th.getBoundingClientRect().width + 'px';
    });
    table.classList.add('fixed-layout');
  }
  function applyWidths(table) {
    const widths = table._state.widths || {};
    if (!Object.keys(widths).length) return;
    freezeLayout(table);
    Object.entries(widths).forEach(([i, w]) => { if (table._cols[i]) table._cols[i].style.width = w + 'px'; });
  }
  function resetWidths(table) {
    table.classList.remove('fixed-layout');
    table._cols.forEach(c => { c.style.width = ''; });
  }
  function addResizer(table, th, idx) {
    const grip = document.createElement('span');
    grip.className = 'col-resizer';
    grip.title = 'Перетягніть, щоб змінити ширину. Подвійний клік — автоширина';
    th.appendChild(grip);
    grip.addEventListener('click', e => e.stopPropagation());
    grip.addEventListener('pointerdown', e => {
      e.preventDefault();
      e.stopPropagation();
      freezeLayout(table);
      const startX = e.clientX;
      const startW = th.getBoundingClientRect().width;
      grip.setPointerCapture(e.pointerId);
      document.body.classList.add('resizing');
      const move = ev => {
        const w = Math.max(MIN_WIDTH, Math.round(startW + ev.clientX - startX));
        table._cols[idx].style.width = w + 'px';
      };
      const up = () => {
        grip.removeEventListener('pointermove', move);
        grip.removeEventListener('pointerup', up);
        document.body.classList.remove('resizing');
        table._state.widths = table._state.widths || {};
        table._state.widths[idx] = parseInt(table._cols[idx].style.width, 10);
        saveState(table);
      };
      grip.addEventListener('pointermove', move);
      grip.addEventListener('pointerup', up);
    });
    grip.addEventListener('dblclick', e => {
      e.stopPropagation();
      if (table._state.widths) delete table._state.widths[idx];
      saveState(table);
      resetWidths(table);
      applyWidths(table);
    });
  }

  // ---------- панель над таблицею ----------
  function buildToolbar(table, sortOptions) {
    const bar = document.createElement('div');
    bar.className = 'table-tools';

    // Меню «Колонки» (десктоп)
    const menu = document.createElement('details');
    menu.className = 'columns-menu';
    menu.innerHTML = '<summary class="btn btn-sm">Колонки ▾</summary><div class="columns-dropdown"></div>';
    const list = menu.querySelector('.columns-dropdown');
    table._headers.forEach((th, idx) => {
      const name = th._label;
      if (!name) return;
      const label = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !th.hidden;
      cb.disabled = LOCKED.includes(name);
      cb.addEventListener('change', () => {
        setHidden(table, idx, !cb.checked);
        const hidden = new Set(table._state.hidden || []);
        cb.checked ? hidden.delete(idx) : hidden.add(idx);
        table._state.hidden = [...hidden];
        saveState(table);
      });
      label.append(cb, ' ' + name);
      list.appendChild(label);
    });
    const reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'btn btn-sm';
    reset.textContent = 'Скинути ширину й колонки';
    reset.addEventListener('click', () => {
      const sort = table._state.sort;
      table._state = sort ? { sort } : {};
      saveState(table);
      location.reload();
    });
    list.appendChild(reset);
    document.addEventListener('click', e => { if (!menu.contains(e.target)) menu.open = false; });

    // Сортування (мобільний)
    const wrap = document.createElement('label');
    wrap.className = 'mobile-sort';
    wrap.innerHTML = '<span>Сортувати:</span>';
    const select = document.createElement('select');
    select.innerHTML = '<option value="">— за замовчуванням —</option>' + sortOptions.map(([i, name]) =>
      `<option value="${i}:asc">${name} ↑</option><option value="${i}:desc">${name} ↓</option>`).join('');
    select.addEventListener('change', () => {
      if (!select.value) { delete table._state.sort; saveState(table); location.reload(); return; }
      const [i, dir] = select.value.split(':');
      sortTable(table, Number(i), dir);
      table._state.sort = { idx: Number(i), dir };
      saveState(table);
    });
    wrap.appendChild(select);
    table._sortSelect = select;

    bar.append(menu, wrap);
    (table.closest('.table-wrap') || table).before(bar);
  }

  // ---------- ініціалізація ----------
  function init(table) {
    table._state = loadState(table);
    table._headers = [...table.tHead.rows[0].cells];

    // colgroup для керування шириною
    let colgroup = table.querySelector('colgroup');
    if (!colgroup) {
      colgroup = document.createElement('colgroup');
      table._headers.forEach(() => colgroup.appendChild(document.createElement('col')));
      table.prepend(colgroup);
    }
    table._cols = [...colgroup.children];

    const sortOptions = [];
    table._headers.forEach((th, idx) => {
      th._label = th.textContent.replace(/[↗▲▼↕]/g, '').trim();
      if (th.hasAttribute('data-nosort') || !th._label) return;
      th.classList.add('sortable-th');
      th.tabIndex = 0;
      const activate = () => {
        const dir = th.classList.contains('sorted-asc') ? 'desc' : 'asc';
        sortTable(table, idx, dir);
        table._state.sort = { idx, dir };
        saveState(table);
      };
      th.addEventListener('click', e => { if (!e.target.closest('a, input, .col-resizer')) activate(); });
      th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); } });
      sortOptions.push([idx, th._label]);
    });
    table._headers.forEach((th, idx) => { if (idx < table._headers.length - 1 || th._label) addResizer(table, th, idx); });

    (table._state.hidden || []).forEach(idx => {
      if (table._headers[idx] && !LOCKED.includes(table._headers[idx]._label)) setHidden(table, idx, true);
    });
    buildToolbar(table, sortOptions);
    applyWidths(table);

    const s = table._state.sort;
    if (s && table._headers[s.idx] && table._headers[s.idx].classList.contains('sortable-th')) sortTable(table, s.idx, s.dir);
  }

  // Прибрати налаштування старого формату (до появи цього скрипта)
  try {
    Object.keys(localStorage).filter(k => k.startsWith('sort:')).forEach(k => localStorage.removeItem(k));
  } catch (e) { /* ignore */ }

  document.querySelectorAll('table.sortable').forEach(init);
})();
