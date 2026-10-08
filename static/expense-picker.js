// Вибір статті витрат у картці заявки.
// 1) Поле з підказками: пишемо код або частину назви — до 10 збігів, ↑↓/Enter/Esc.
// 2) «☰ Довідник» — модальне вікно з повним списком, пошуком і «Нещодавніми».
(function () {
  const ITEMS = JSON.parse(document.getElementById('expense-items-data').textContent || '[]');
  const BY_CODE = new Map(ITEMS.map(i => [i.code, i]));
  const RECENT_KEY = 'expense-recent';
  const MAX_SUGGEST = 10;
  const MAX_LIST = 500;

  const picker = document.getElementById('exp-picker');
  const hidden = document.getElementById('expense_code');
  const input = document.getElementById('expense-input');
  const chip = document.getElementById('exp-chip');
  const clearBtn = document.getElementById('exp-clear');
  const suggest = document.getElementById('exp-suggest');
  const dialog = document.getElementById('exp-dialog');
  const dInput = document.getElementById('exp-dialog-input');
  const dList = document.getElementById('exp-dialog-list');
  const dCount = document.getElementById('exp-dialog-count');
  const recentBox = document.getElementById('exp-recent');
  const recentList = document.getElementById('exp-recent-list');

  // ---------- пошук ----------
  const norm = s => (s || '').toLowerCase().replace(/[ʼ’`]/g, "'").replace(/\s+/g, ' ').trim();
  const collator = new Intl.Collator('uk', { numeric: true });
  ITEMS.forEach(i => { i._code = norm(i.code); i._name = norm(i.name); });

  function search(query, limit) {
    const q = norm(query);
    if (!q) return ITEMS.slice(0, limit);
    const tokens = q.split(' ');
    const found = [];
    for (const i of ITEMS) {
      const hay = i._code + ' ' + i._name;
      if (!tokens.every(t => hay.includes(t))) continue;
      let rank = 4;
      if (i._code === q) rank = 0;
      else if (i._code.startsWith(q)) rank = 1;
      else if (i._name.startsWith(q)) rank = 2;
      else if (i._name.split(' ').some(w => w.startsWith(tokens[0]))) rank = 3;
      found.push([rank, i]);
    }
    found.sort((a, b) => a[0] - b[0] || collator.compare(a[1].code, b[1].code));
    return found.slice(0, limit).map(f => f[1]);
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // Підсвітити збіги токенів у тексті
  function highlight(text, query) {
    const tokens = norm(query).split(' ').filter(Boolean);
    if (!tokens.length) return escapeHtml(text);
    const lower = text.toLowerCase().replace(/[ʼ’`]/g, "'");  // та сама довжина, що й text
    const marks = new Array(text.length).fill(false);
    tokens.forEach(t => {
      let from = 0, idx;
      while (t && (idx = lower.indexOf(t, from)) !== -1) {
        for (let k = idx; k < idx + t.length; k++) marks[k] = true;
        from = idx + t.length;
      }
    });
    let out = '', open = false;
    for (let k = 0; k < text.length; k++) {
      if (marks[k] && !open) { out += '<mark>'; open = true; }
      if (!marks[k] && open) { out += '</mark>'; open = false; }
      out += escapeHtml(text[k]);
    }
    return out + (open ? '</mark>' : '');
  }

  // ---------- вибір ----------
  function recent() {
    try { return (JSON.parse(localStorage.getItem(RECENT_KEY) || '[]') || []).filter(c => BY_CODE.has(c)); }
    catch (e) { return []; }
  }
  function remember(code) {
    try { localStorage.setItem(RECENT_KEY, JSON.stringify([code, ...recent().filter(c => c !== code)].slice(0, 5))); }
    catch (e) { /* приватний режим */ }
  }

  function select(item, { save = true } = {}) {
    hidden.value = item ? item.code : '';
    input.value = item ? item.name : '';
    input.title = item ? item.name + ' (' + item.code + ')' : '';
    chip.textContent = item ? item.code : '';
    chip.hidden = clearBtn.hidden = !item;
    picker.classList.toggle('has-value', !!item);
    picker.classList.remove('invalid', 'unmatched');
    if (item && save) remember(item.code);
    closeSuggest();
  }

  // ---------- підказки під полем ----------
  let active = -1, current = [];

  function closeSuggest() {
    suggest.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    active = -1;
  }

  function renderSuggest() {
    current = search(input.value, MAX_SUGGEST);
    suggest.innerHTML = '';
    if (!current.length) {
      suggest.innerHTML = '<li class="exp-empty">Нічого не знайдено. Спробуйте інший код або відкрийте довідник</li>';
    }
    current.forEach((item, idx) => {
      const li = document.createElement('li');
      li.role = 'option';
      li.className = 'exp-option' + (idx === active ? ' active' : '');
      li.innerHTML = `<span class="exp-name">${highlight(item.name, input.value)}</span>`
        + `<span class="exp-code">${highlight(item.code, input.value)}</span>`;
      li.addEventListener('mousedown', e => { e.preventDefault(); select(item); });
      suggest.appendChild(li);
    });
    suggest.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function moveActive(list, delta) {
    if (!list.length) return -1;
    return (active + delta + list.length) % list.length;
  }

  input.addEventListener('input', () => {
    // Користувач почав редагувати — попередній вибір знімається, доки не вибере знову
    if (hidden.value) { hidden.value = ''; chip.hidden = clearBtn.hidden = true; picker.classList.remove('has-value'); }
    picker.classList.toggle('unmatched', !!input.value.trim());
    active = -1;
    if (input.value.trim()) renderSuggest(); else closeSuggest();
  });
  input.addEventListener('focus', () => { if (!hidden.value && input.value.trim()) renderSuggest(); });
  input.addEventListener('blur', () => setTimeout(closeSuggest, 120));
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (suggest.hidden) renderSuggest();
      active = moveActive(current, e.key === 'ArrowDown' ? 1 : -1);
      [...suggest.querySelectorAll('.exp-option')].forEach((li, i) => {
        li.classList.toggle('active', i === active);
        if (i === active) li.scrollIntoView({ block: 'nearest' });
      });
    } else if (e.key === 'Enter') {
      if (!suggest.hidden && current.length) {
        e.preventDefault();  // не відправляти форму
        select(current[active >= 0 ? active : 0]);
      } else if (!hidden.value && input.value.trim()) {
        e.preventDefault();
      }
    } else if (e.key === 'Escape') {
      closeSuggest();
    }
  });
  clearBtn.addEventListener('click', () => { select(null); input.focus(); });

  // ---------- модальне вікно довідника ----------
  let dActive = -1, dRows = [];

  function renderRecent() {
    const codes = dInput.value.trim() ? [] : recent();
    recentBox.hidden = !codes.length;
    recentList.innerHTML = '';
    codes.forEach(code => {
      const item = BY_CODE.get(code);
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'exp-recent-chip';
      b.innerHTML = `<span class="exp-code">${escapeHtml(item.code)}</span> ${escapeHtml(item.name)}`;
      b.addEventListener('click', () => choose(item));
      recentList.appendChild(b);
    });
  }

  function renderDialog() {
    const q = dInput.value;
    const all = search(q, Infinity);
    dRows = all.slice(0, MAX_LIST);
    dCount.textContent = q.trim() ? `Знайдено ${all.length} з ${ITEMS.length}` : `Усього ${ITEMS.length}`;
    dList.innerHTML = '';
    if (!dRows.length) {
      dList.innerHTML = '<div class="exp-empty">Нічого не знайдено</div>';
    }
    dRows.forEach((item, idx) => {
      const row = document.createElement('div');
      row.role = 'option';
      row.className = 'exp-row' + (item.code === hidden.value ? ' selected' : '') + (idx === dActive ? ' active' : '');
      row.dataset.idx = idx;
      row.innerHTML = `<span class="exp-code">${highlight(item.code, q)}</span><span class="exp-name">${highlight(item.name, q)}</span>`;
      row.addEventListener('click', () => choose(item));
      dList.appendChild(row);
    });
    if (all.length > MAX_LIST) {
      dList.insertAdjacentHTML('beforeend', `<div class="exp-empty">Показано перші ${MAX_LIST}. Уточніть пошук.</div>`);
    }
    renderRecent();
  }

  function setDialogActive(idx) {
    dActive = idx;
    dList.querySelectorAll('.exp-row').forEach(r => {
      const on = Number(r.dataset.idx) === idx;
      r.classList.toggle('active', on);
      if (on) r.scrollIntoView({ block: 'nearest' });
    });
  }

  function choose(item) {
    select(item);
    dialog.close();
    input.focus();
  }

  document.getElementById('exp-open').addEventListener('click', () => {
    dInput.value = hidden.value ? '' : input.value;
    dActive = -1;
    renderDialog();
    dialog.showModal();
    dInput.focus();
    const sel = dList.querySelector('.exp-row.selected');
    if (sel) sel.scrollIntoView({ block: 'center' });
  });
  dInput.addEventListener('input', () => { dActive = -1; renderDialog(); dList.scrollTop = 0; });
  dInput.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setDialogActive(Math.min(dActive + 1, dRows.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setDialogActive(Math.max(dActive - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (dRows.length) choose(dRows[dActive >= 0 ? dActive : 0]); }
  });
  ['exp-dialog-close', 'exp-dialog-cancel'].forEach(id =>
    document.getElementById(id).addEventListener('click', () => dialog.close()));
  dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });  // клік по фону

  // Перед відправкою: введений, але не вибраний текст — підсвітити поле
  input.form.addEventListener('submit', e => {
    if (!hidden.value && input.value.trim()) {
      e.preventDefault();
      picker.classList.add('invalid');
      input.focus();
      renderSuggest();
    }
  });

  // Початковий стан
  const initial = BY_CODE.get(hidden.value);
  if (initial) select(initial, { save: false });
  else if (hidden.value) { input.value = hidden.value; chip.hidden = clearBtn.hidden = false; chip.textContent = hidden.value; }
})();
