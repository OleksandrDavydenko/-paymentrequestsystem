// Вибір контрагента в картці заявки.
// 1) Поле з підказками: назва, ЄДРПОУ або IBAN — до 10 збігів, ↑↓/Enter/Esc.
// 2) «☰» — вікно довідника: пошук, фільтри, «Нещодавні», «+ Новий контрагент» (якого ще немає в 1С).
(function () {
  const ITEMS = JSON.parse(document.getElementById('cp-items-data').textContent || '[]');
  const RECENT_KEY = 'counterparty-recent';
  const MAX_SUGGEST = 10, MAX_LIST = 500;
  let filter = '';

  const $ = id => document.getElementById(id);
  const picker = $('cp-picker'), hidden = $('counterparty_id'), input = $('cp-input'), chip = $('cp-chip');
  const clearBtn = $('cp-clear'), suggest = $('cp-suggest'), dialog = $('cp-dialog');
  const dInput = $('cp-dialog-input'), dList = $('cp-dialog-list'), dCount = $('cp-dialog-count');
  const recentBox = $('cp-recent'), recentList = $('cp-recent-list');
  const listView = $('cp-list-view'), newView = $('cp-new-view');

  const norm = s => (s || '').toLowerCase().replace(/[ʼ’`]/g, "'").replace(/[«»"]/g, '').replace(/\s+/g, ' ').trim();
  const compact = s => (s || '').replace(/\s+/g, '').toUpperCase();
  const index = i => { i._name = norm(i.name); i._iban = compact(i.iban); };
  ITEMS.forEach(index);
  const byId = id => ITEMS.find(i => String(i.id) === String(id));
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function search(query, limit) {
    const q = norm(query), qc = compact(query);
    const pass = i => !filter || (filter === 'pending' ? i.pending : filter === 'resident' ? i.resident : !i.resident);
    const rows = ITEMS.filter(pass);
    if (!q) return rows.slice(0, limit);
    const tokens = q.split(' ');
    const found = [];
    for (const i of rows) {
      let rank = 9;
      if (i.edrpou && i.edrpou === qc) rank = 0;
      else if (qc.length > 4 && i._iban.includes(qc)) rank = 1;
      else if (i.edrpou && qc.length > 2 && i.edrpou.startsWith(qc)) rank = 2;
      else if (tokens.every(t => i._name.includes(t))) rank = i._name.startsWith(q) ? 3 : 4;
      if (rank < 9) found.push([rank, i]);
    }
    found.sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name, 'uk'));
    return found.slice(0, limit).map(f => f[1]);
  }

  const meta = i => [i.edrpou ? (i.resident ? 'ЄДРПОУ ' : '№ ') + esc(i.edrpou) : '', !i.resident && i.country ? esc(i.country) : '',
    i.iban ? '…' + esc(i.iban.slice(-4)) : ''].filter(Boolean).join(' · ');
  const badge = i => i.pending ? '<span class="cp-new-chip">новий</span>' : (i.resident ? '' : '<span class="exp-kind exp-kind-allocated">нерезидент</span>');

  // ---------- вибір ----------
  function recent() {
    try { return (JSON.parse(localStorage.getItem(RECENT_KEY) || '[]') || []).filter(id => byId(id)); }
    catch (e) { return []; }
  }
  function remember(id) {
    try { localStorage.setItem(RECENT_KEY, JSON.stringify([id, ...recent().filter(x => String(x) !== String(id))].slice(0, 5))); }
    catch (e) { /* приватний режим */ }
  }
  function select(item, { save = true } = {}) {
    hidden.value = item ? item.id : '';
    input.value = item ? item.name : '';
    input.title = item ? item.name + (item.edrpou ? ' · ' + item.edrpou : '') : '';
    chip.hidden = !(item && item.pending);
    clearBtn.hidden = !item;
    picker.classList.toggle('has-value', !!item);
    picker.classList.remove('invalid', 'unmatched');
    if (item && save) remember(item.id);
    closeSuggest();
    hidden.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // ---------- підказки під полем ----------
  let active = -1, current = [];
  function closeSuggest() { suggest.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; }
  function renderSuggest() {
    const saved = filter; filter = '';
    current = search(input.value, MAX_SUGGEST);
    filter = saved;
    suggest.innerHTML = '';
    if (!current.length) suggest.innerHTML = '<li class="exp-empty">Не знайдено. Відкрийте довідник ☰ — там можна внести нового контрагента</li>';
    current.forEach((item, idx) => {
      const li = document.createElement('li');
      li.role = 'option';
      li.className = 'exp-option' + (idx === active ? ' active' : '');
      li.innerHTML = `<span class="exp-name">${esc(item.name)}</span><span class="exp-meta">${badge(item)}<span class="exp-code">${meta(item)}</span></span>`;
      li.addEventListener('mousedown', e => { e.preventDefault(); select(item); });
      suggest.appendChild(li);
    });
    suggest.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }
  input.addEventListener('input', () => {
    if (hidden.value) { hidden.value = ''; chip.hidden = clearBtn.hidden = true; picker.classList.remove('has-value'); }
    picker.classList.toggle('unmatched', !!input.value.trim());
    active = -1;
    if (input.value.trim()) renderSuggest(); else closeSuggest();
  });
  input.addEventListener('blur', () => setTimeout(closeSuggest, 120));
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (suggest.hidden) renderSuggest();
      if (!current.length) return;
      active = (active + (e.key === 'ArrowDown' ? 1 : -1) + current.length) % current.length;
      [...suggest.querySelectorAll('.exp-option')].forEach((li, i) => li.classList.toggle('active', i === active));
    } else if (e.key === 'Enter') {
      if (!suggest.hidden && current.length) { e.preventDefault(); select(current[active >= 0 ? active : 0]); }
      else if (!hidden.value && input.value.trim()) e.preventDefault();
    } else if (e.key === 'Escape') closeSuggest();
  });
  clearBtn.addEventListener('click', () => { select(null); input.focus(); });

  // ---------- вікно довідника ----------
  let dActive = -1, dRows = [];
  function renderRecent() {
    const ids = dInput.value.trim() || filter ? [] : recent();
    recentBox.hidden = !ids.length;
    recentList.innerHTML = '';
    ids.forEach(id => {
      const item = byId(id);
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'exp-recent-chip';
      b.textContent = item.name;
      b.addEventListener('click', () => choose(item));
      recentList.appendChild(b);
    });
  }
  function renderDialog() {
    const all = search(dInput.value, Infinity);
    dRows = all.slice(0, MAX_LIST);
    dCount.textContent = (dInput.value.trim() || filter) ? `Знайдено ${all.length} з ${ITEMS.length}` : `Усього ${ITEMS.length}`;
    dList.innerHTML = dRows.length ? '' : '<div class="exp-empty">Не знайдено. Якщо контрагента ще немає в 1С — натисніть «+ Новий контрагент»</div>';
    dRows.forEach((item, idx) => {
      const row = document.createElement('div');
      row.role = 'option';
      row.className = 'exp-row cp-row' + (String(item.id) === hidden.value ? ' selected' : '') + (idx === dActive ? ' active' : '');
      row.dataset.idx = idx;
      row.innerHTML = `<span class="exp-name">${esc(item.name)}</span><span class="cp-meta muted">${meta(item)}</span>${badge(item)}`;
      row.addEventListener('click', () => choose(item));
      dList.appendChild(row);
    });
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
  function choose(item) { select(item); dialog.close(); input.focus(); }
  function showList() { newView.hidden = true; listView.hidden = false; dInput.focus(); }

  $('cp-open').addEventListener('click', () => {
    dInput.value = hidden.value ? '' : input.value;
    dActive = -1;
    showList();
    renderDialog();
    dialog.showModal();
    dInput.focus();
  });
  dInput.addEventListener('input', () => { dActive = -1; renderDialog(); dList.scrollTop = 0; });
  dialog.querySelectorAll('.exp-kind-filter .chip').forEach(btn => btn.addEventListener('click', () => {
    filter = btn.dataset.filter;
    dialog.querySelectorAll('.exp-kind-filter .chip').forEach(b => b.classList.toggle('active', b === btn));
    dActive = -1; renderDialog(); dInput.focus();
  }));
  dInput.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setDialogActive(Math.min(dActive + 1, dRows.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setDialogActive(Math.max(dActive - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (dRows.length) choose(dRows[dActive >= 0 ? dActive : 0]); }
  });
  ['cp-dialog-close', 'cp-dialog-cancel'].forEach(id => $(id).addEventListener('click', () => dialog.close()));
  dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });

  // ---------- новий контрагент ----------
  const f = id => $('cp-new-' + id);
  const isResident = () => dialog.querySelector('input[name=cp-new-resident]:checked').value === '1';
  function syncKind() {
    const res = isResident();
    dialog.querySelectorAll('.cp-nonres').forEach(el => { el.hidden = res; });
    $('cp-new-edrpou-label').textContent = res ? 'ЄДРПОУ / ІПН' : 'Реєстраційний номер';
    f('edrpou').placeholder = res ? '8 цифр (ФОП — 10)' : 'номер у реєстрі країни';
    f('edrpou').inputMode = res ? 'numeric' : 'text';
  }
  dialog.querySelectorAll('input[name=cp-new-resident]').forEach(r => r.addEventListener('change', syncKind));
  function clearErrors() {
    dialog.querySelectorAll('[data-err]').forEach(el => { el.textContent = ''; });
    $('cp-new-error').textContent = '';
    $('cp-duplicate').hidden = true;
  }
  $('cp-new-open').addEventListener('click', () => {
    listView.hidden = true; newView.hidden = false;
    clearErrors();
    // Якщо в пошуку були цифри — це, мабуть, ЄДРПОУ; інакше — назва
    const q = dInput.value.trim();
    if (/^\d{8,10}$/.test(q)) f('edrpou').value = q; else if (q && !f('name').value) f('name').value = q;
    syncKind();
    (f('edrpou').value ? f('name') : f('edrpou')).focus();
  });
  $('cp-new-back').addEventListener('click', showList);
  // Enter у полях нового контрагента не відправляє форму заявки
  newView.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') { e.preventDefault(); $('cp-new-save').click(); } });

  let duplicate = null;
  $('cp-duplicate-pick').addEventListener('click', () => {
    let item = duplicate && byId(duplicate.id);
    if (!item && duplicate) { item = { ...duplicate, resident: true, iban: '', country: '' }; index(item); ITEMS.push(item); }
    if (item) choose(item);
  });
  $('cp-new-save').addEventListener('click', async () => {
    clearErrors();
    const btn = $('cp-new-save');
    btn.disabled = true;
    try {
      const resp = await fetch(window.CP_NEW_URL, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
        body: JSON.stringify({ resident: isResident() ? '1' : '0', edrpou: f('edrpou').value, country: f('country').value,
          name: f('name').value, iban: f('iban').value, bank: f('bank').value, note: f('note').value }),
      });
      const data = await resp.json();
      if (data.errors) {
        Object.entries(data.errors).forEach(([k, v]) => { const el = dialog.querySelector(`[data-err="${k}"]`); if (el) el.textContent = v; });
      } else if (data.duplicate) {
        duplicate = data.duplicate;
        $('cp-duplicate-text').textContent = `Такий контрагент уже є${data.duplicate.pending ? ' (внесений у заявках, ще без 1С)' : ' в 1С'}: `
          + data.duplicate.name + (data.duplicate.edrpou ? ' · ' + data.duplicate.edrpou : '') + '.';
        $('cp-duplicate').hidden = false;
      } else if (data.item) {
        index(data.item);
        ITEMS.push(data.item);
        ['edrpou', 'country', 'name', 'iban', 'bank', 'note'].forEach(k => { f(k).value = ''; });
        choose(data.item);
      }
    } catch (e) {
      $('cp-new-error').textContent = 'Не вдалося зберегти — перевірте з\'єднання і спробуйте ще раз';
    } finally {
      btn.disabled = false;
    }
  });

  // Перед відправкою: введений, але не вибраний текст — підсвітити поле
  input.form.addEventListener('submit', e => {
    if (!hidden.value && input.value.trim()) { e.preventDefault(); picker.classList.add('invalid'); input.focus(); renderSuggest(); }
  });

  // Початковий стан
  const initial = byId(hidden.value);
  if (initial) select(initial, { save: false });
  else if (hidden.value) {
    input.value = picker.dataset.initialName || ('#' + hidden.value);
    clearBtn.hidden = false;
    picker.classList.add('has-value');
  }
})();
