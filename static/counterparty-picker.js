// Вибір контрагента в картці заявки. Довідник великий (десятки тисяч з 1С) — пошук на сервері.
// 1) Поле з підказками: назва або ЄДРПОУ — до 10 збігів, ↑↓/Enter/Esc.
// 2) «☰» — вікно довідника: пошук, фільтри, «Нещодавні», «+ Новий контрагент» (якого ще немає в 1С).
(function () {
  const $ = id => document.getElementById(id);
  const picker = $('cp-picker'), hidden = $('counterparty_id'), input = $('cp-input'), chip = $('cp-chip');
  const clearBtn = $('cp-clear'), suggest = $('cp-suggest'), dialog = $('cp-dialog');
  const dInput = $('cp-dialog-input'), dList = $('cp-dialog-list'), dCount = $('cp-dialog-count');
  const recentBox = $('cp-recent'), recentList = $('cp-recent-list');
  const listView = $('cp-list-view'), newView = $('cp-new-view');
  const SEARCH_URL = picker.dataset.searchUrl;
  const RECENT_KEY = 'counterparty-recent-v2';
  let filter = '';

  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const meta = i => [i.edrpou ? (i.resident ? 'ЄДРПОУ ' : '№ ') + esc(i.edrpou) : '', !i.resident && i.country ? esc(i.country) : '']
    .filter(Boolean).join(' · ');
  const badge = i => i.pending ? '<span class="cp-new-chip">новий</span>' : (i.resident ? '' : '<span class="exp-kind exp-kind-allocated">нерезидент</span>');

  // Запит до сервера; відповіді, що запізнилися, відкидаються
  let seq = 0;
  async function search(q, { kind = '', limit = 10 } = {}) {
    const my = ++seq;
    const url = `${SEARCH_URL}?q=${encodeURIComponent(q)}&kind=${kind}&limit=${limit}`;
    const data = await (await fetch(url, { headers: { 'X-Requested-With': 'fetch' } })).json();
    return my === seq ? data : null;
  }

  // ---------- вибір ----------
  function recent() {
    try { return (JSON.parse(localStorage.getItem(RECENT_KEY) || '[]') || []).filter(x => x && x.id); }
    catch (e) { return []; }
  }
  function remember(item) {
    try { localStorage.setItem(RECENT_KEY, JSON.stringify([item, ...recent().filter(x => x.id !== item.id)].slice(0, 6))); }
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
    if (item && save) remember(item);
    closeSuggest();
    hidden.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // ---------- підказки під полем ----------
  let active = -1, current = [], timer;
  function closeSuggest() { suggest.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; }
  function renderSuggest(items) {
    current = items;
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
  function querySuggest() {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { closeSuggest(); return; }
    timer = setTimeout(async () => {
      try { const data = await search(q, { limit: 10 }); if (data) renderSuggest(data.items); }
      catch (e) { /* мережа — спробує на наступному введенні */ }
    }, 200);
  }
  input.addEventListener('input', () => {
    if (hidden.value) { hidden.value = ''; chip.hidden = clearBtn.hidden = true; picker.classList.remove('has-value'); }
    picker.classList.toggle('unmatched', !!input.value.trim());
    active = -1;
    querySuggest();
  });
  input.addEventListener('blur', () => setTimeout(closeSuggest, 120));
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!current.length || suggest.hidden) return;
      active = (active + (e.key === 'ArrowDown' ? 1 : -1) + current.length) % current.length;
      [...suggest.querySelectorAll('.exp-option')].forEach((li, i) => li.classList.toggle('active', i === active));
    } else if (e.key === 'Enter') {
      if (!suggest.hidden && current.length) { e.preventDefault(); select(current[active >= 0 ? active : 0]); }
      else if (!hidden.value && input.value.trim()) e.preventDefault();
    } else if (e.key === 'Escape') closeSuggest();
  });
  clearBtn.addEventListener('click', () => { select(null); input.focus(); });

  // ---------- вікно довідника ----------
  let dActive = -1, dRows = [], dTimer;
  function renderRecent() {
    const items = dInput.value.trim() || filter ? [] : recent();
    recentBox.hidden = !items.length;
    recentList.innerHTML = '';
    items.forEach(item => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'exp-recent-chip';
      b.textContent = item.name;
      b.addEventListener('click', () => choose(item));
      recentList.appendChild(b);
    });
  }
  function renderRows(data) {
    dRows = data.items;
    const q = dInput.value.trim();
    dCount.textContent = data.total > dRows.length ? `Показано ${dRows.length} з ${data.total} — уточніть пошук`
      : (q || filter ? `Знайдено ${data.total}` : '');
    dList.innerHTML = dRows.length ? '' : '<div class="exp-empty">Не знайдено. Якщо контрагента ще немає в 1С — натисніть «+ Новий контрагент»</div>';
    dRows.forEach((item, idx) => {
      const row = document.createElement('div');
      row.role = 'option';
      row.className = 'exp-row cp-row' + (item.id === hidden.value ? ' selected' : '') + (idx === dActive ? ' active' : '');
      row.dataset.idx = idx;
      row.innerHTML = `<span class="exp-name">${esc(item.name)}</span><span class="cp-meta muted">${meta(item)}</span>${badge(item)}`;
      row.addEventListener('click', () => choose(item));
      dList.appendChild(row);
    });
    renderRecent();
  }
  function loadDialog(delay = 200) {
    clearTimeout(dTimer);
    dTimer = setTimeout(async () => {
      try {
        const data = await search(dInput.value.trim(), { kind: filter, limit: 100 });
        if (data) renderRows(data);
      } catch (e) {
        dList.innerHTML = '<div class="exp-empty">Не вдалося завантажити довідник — перевірте з\'єднання</div>';
      }
    }, delay);
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
    dList.innerHTML = '<div class="exp-empty">Завантаження…</div>';
    renderRecent();
    dialog.showModal();
    dInput.focus();
    loadDialog(0);
  });
  dInput.addEventListener('input', () => { dActive = -1; loadDialog(); dList.scrollTop = 0; });
  dialog.querySelectorAll('.exp-kind-filter .chip').forEach(btn => btn.addEventListener('click', () => {
    filter = btn.dataset.filter;
    dialog.querySelectorAll('.exp-kind-filter .chip').forEach(b => b.classList.toggle('active', b === btn));
    dActive = -1; loadDialog(0); dInput.focus();
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
    $('cp-new-edrpou-label').textContent = res ? 'ЄДРПОУ / ІПН *' : 'Реєстраційний номер';
    $('cp-new-edrpou-hint').textContent = res ? 'Обов\'язково: 8 цифр для ТОВ та інших юросіб, 10 цифр (ІПН) для ФОП і фізосіб'
      : 'Необов\'язково — номер компанії в реєстрі її країни';
    f('edrpou').placeholder = res ? '8 цифр (ФОП, фізособи — 10)' : 'необов\'язково';
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
    const q = dInput.value.trim();  // цифри в пошуку — мабуть, ЄДРПОУ; інакше — назва
    if (/^\d{8,10}$/.test(q)) f('edrpou').value = q; else if (q && !f('name').value) f('name').value = q;
    syncKind();
    (isResident() && !f('edrpou').value ? f('edrpou') : f('name')).focus();
  });
  $('cp-new-back').addEventListener('click', showList);
  newView.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') { e.preventDefault(); $('cp-new-save').click(); } });

  let duplicate = null;
  $('cp-duplicate-pick').addEventListener('click', () => { if (duplicate) choose(duplicate); });
  $('cp-new-save').addEventListener('click', async () => {
    clearErrors();
    const btn = $('cp-new-save');
    btn.disabled = true;
    try {
      const resp = await fetch(window.CP_NEW_URL, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
        body: JSON.stringify({ resident: isResident() ? '1' : '0', edrpou: f('edrpou').value, country: f('country').value,
          name: f('name').value, note: f('note').value }),
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
        ['edrpou', 'country', 'name', 'note'].forEach(k => { f(k).value = ''; });
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
    if (!hidden.value && input.value.trim()) { e.preventDefault(); picker.classList.add('invalid'); input.focus(); querySuggest(); }
  });

  // Початковий стан
  const initial = JSON.parse($('cp-initial').textContent || 'null');
  if (initial) select(initial, { save: false });
  else if (hidden.value) {
    input.value = picker.dataset.initialName || ('#' + hidden.value);
    clearBtn.hidden = false;
    picker.classList.add('has-value');
  }
})();
