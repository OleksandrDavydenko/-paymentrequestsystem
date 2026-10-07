// Сортування таблиць кліком по заголовку колонки (table.sortable).
// Значення береться з data-sort клітинки, інакше — з її тексту.
// На мобільному (заголовки сховані) над таблицею з'являється select «Сортувати».
(function () {
  const collator = new Intl.Collator('uk', { numeric: true, sensitivity: 'base' });

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

  function storageKey(table) {
    return 'sort:' + location.pathname + ':' + (table.id || [...document.querySelectorAll('table.sortable')].indexOf(table));
  }

  function save(table, idx, dir) {
    try { localStorage.setItem(storageKey(table), JSON.stringify({ idx, dir })); } catch (e) { /* приватний режим */ }
  }

  function load(table) {
    try { return JSON.parse(localStorage.getItem(storageKey(table)) || 'null'); } catch (e) { return null; }
  }

  function sortTable(table, idx, dir) {
    const tbody = table.tBodies[0];
    const rows = [...tbody.rows].filter(r => !r.querySelector('td.empty'));
    rows.sort((a, b) => compare(cellValue(a, idx), cellValue(b, idx)) * (dir === 'desc' ? -1 : 1));
    rows.forEach(r => tbody.appendChild(r));
    table.querySelectorAll('thead th').forEach((th, i) => {
      th.classList.toggle('sorted-asc', i === idx && dir === 'asc');
      th.classList.toggle('sorted-desc', i === idx && dir === 'desc');
      if (th.classList.contains('sortable-th')) {
        th.setAttribute('aria-sort', i === idx ? (dir === 'asc' ? 'ascending' : 'descending') : 'none');
      }
    });
    const select = table._sortSelect;
    if (select) select.value = idx + ':' + dir;
  }

  function init(table) {
    const headers = [...table.querySelectorAll('thead th')];
    const options = [];
    headers.forEach((th, idx) => {
      if (th.hasAttribute('data-nosort') || !th.textContent.trim()) return;
      th.classList.add('sortable-th');
      th.tabIndex = 0;
      th.title = 'Сортувати';
      const activate = () => {
        const dir = th.classList.contains('sorted-asc') ? 'desc' : 'asc';
        sortTable(table, idx, dir);
        save(table, idx, dir);
      };
      th.addEventListener('click', e => { if (!e.target.closest('a, input')) activate(); });
      th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); } });
      options.push([idx, th.textContent.replace(/[↗▲▼]/g, '').trim()]);
    });

    // Мобільний select сортування
    const wrap = document.createElement('label');
    wrap.className = 'mobile-sort';
    wrap.innerHTML = '<span>Сортувати:</span>';
    const select = document.createElement('select');
    select.innerHTML = '<option value="">— за замовчуванням —</option>' + options.map(([i, name]) =>
      `<option value="${i}:asc">${name} ↑</option><option value="${i}:desc">${name} ↓</option>`).join('');
    select.addEventListener('change', () => {
      if (!select.value) { try { localStorage.removeItem(storageKey(table)); } catch (e) {} location.reload(); return; }
      const [i, dir] = select.value.split(':');
      sortTable(table, Number(i), dir);
      save(table, Number(i), dir);
    });
    wrap.appendChild(select);
    (table.closest('.table-wrap') || table).before(wrap);
    table._sortSelect = select;

    const saved = load(table);
    if (saved && headers[saved.idx] && headers[saved.idx].classList.contains('sortable-th')) {
      sortTable(table, saved.idx, saved.dir);
    }
  }

  document.querySelectorAll('table.sortable').forEach(init);
})();
