/* Розподіл заявки між відділами у формі (expense_split.py, item_groups.py, templates/request/_split.html).
   Блок видно в непрямій витраті зі статтею з групи кількох варіантів (з розподілом / пряма / позапланова) або з
   прямою статтею без групи: пряма стаття, відділ заявки не профітцентр — розподіл обов'язковий; інакше — кнопкою
   «Розділити між відділами». У рядку — варіант групи: пряма — тільки на профітцентр, з розподілом — на її
   відділ-власника (так її розносить 1С), позапланова — на «Інше». Сума рядків має дорівнювати сумі заявки.
   Під статтею — «Інші варіанти» групи: клік підставляє варіант (expensePicker.pick). */
(function () {
  'use strict';
  const box = document.getElementById('split-block');
  if (!box) return;
  const cfg = JSON.parse((document.getElementById('split-data') || {}).textContent || '{}');
  const groups = cfg.groups || {}, ownersOf = cfg.owners || {}, names = cfg.names || {}, pc = new Set(cfg.pc || []);
  const OTHER = cfg.other || '-', OTHER_NAME = cfg.other_name || 'Інше';
  const $ = id => document.getElementById(id);
  const why = $('split-why'), offer = $('split-offer'), body = $('split-body'), rowsEl = $('split-rows');
  const sent = $('split-sent'), totalEl = $('split-total'), tpl = $('split-row-tpl');
  let open = box.dataset.open === '1';

  const num = v => { const n = parseFloat(String(v || '').replace(/[\s ]/g, '').replace(',', '.')); return isNaN(n) ? 0 : n; };
  const money = n => n.toLocaleString('uk-UA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const item = () => ($('expense_code') || {}).value || '';
  const department = () => { const s = $('department'); return (s && s.value) || box.dataset.department || ''; };
  const contractor = () => { const r = document.querySelector('input[name=payment_kind]:checked'); return !!r && r.value === 'contractor'; };
  const deptName = code => { const d = (cfg.departments || []).find(x => x.code === code); return d ? d.name : code; };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // Стаття заявки → варіанти групи (direct / allocated / unplanned), власники статті з розподілом.
  // Розподіл можливий для прямої статті або групи з кількох варіантів; обов'язковий — для прямої у непрофітцентрі
  function state() {
    const code = item(), g = groups[code];
    if (contractor() || !g) return null;
    const list = [g.direct, g.allocated, g.unplanned].filter(Boolean);
    if (!g.direct && list.length < 2) return null;
    return { direct: g.direct || null, allocated: g.allocated || null, unplanned: g.unplanned || null, list,
             owners: g.allocated ? (ownersOf[g.allocated] || []) : [],
             required: code === g.direct && !pc.has(department()) };
  }
  // Відділи, на які можна віднести статтю з розподілом: її власники, а якщо власник невідомий — будь-який
  const allocDeps = st => !st.allocated ? [] : (st.owners.length ? st.owners : (cfg.departments || []).map(d => d.code));
  // Статті, дозволені для відділу рядка (порожній відділ — усі варіанти групи)
  function allowedItems(st, dep) {
    if (!dep) return st.list;
    if (dep === OTHER) return st.unplanned ? [st.unplanned] : [];
    return [...(st.direct && pc.has(dep) ? [st.direct] : []), ...(allocDeps(st).includes(dep) ? [st.allocated] : [])];
  }
  const label = (st, c) => `${names[c] || c} — ${c === st.allocated ? 'з розподілом' : c === st.unplanned ? 'позапланова' : 'пряма'}`;

  // Відділи рядка: «Профітцентри — пряма стаття», «Власник статті …» (з розподілом; власник невідомий — «Інші
  // відділи»), «Позапланова — на «Інше»». Вибраний раніше відділ, якого в новому списку немає, скидається
  function fillDeps(tr, st) {
    const sel = tr.querySelector('.split-dep');
    const was = sel.value || sel.dataset.selected || '';
    const deps = cfg.departments || [];
    const opt = d => `<option value="${esc(d.code)}">${esc(d.name)}</option>`;
    const pcs = st.direct ? deps.filter(d => pc.has(d.code)) : [];
    const ad = new Set(allocDeps(st));
    const rest = deps.filter(d => ad.has(d.code) && (st.owners.length || !pcs.some(p => p.code === d.code)));
    let html = '<option value="">— відділ —</option>';
    if (pcs.length) html += `<optgroup label="${st.allocated || st.unplanned ? 'Профітцентри — пряма стаття' : 'Профітцентри (пряму статтю — лише на них)'}">${pcs.map(opt).join('')}</optgroup>`;
    if (st.allocated) {
      const lbl = st.owners.length ? `Власник статті «${names[st.allocated] || st.allocated}»` : 'Інші відділи — стаття з розподілом';
      html += `<optgroup label="${esc(lbl)}">${rest.map(opt).join('')}</optgroup>`;
    }
    if (st.unplanned) html += `<optgroup label="Позапланова — на «Інше»"><option value="${esc(OTHER)}">${esc(OTHER_NAME)}</option></optgroup>`;
    sel.innerHTML = html;
    const ok = pcs.some(d => d.code === was) || ad.has(was) || (st.unplanned && was === OTHER);
    sel.value = ok ? was : '';
    sel.dataset.selected = sel.value;
  }

  // Статті рядка за відділом; вибрана раніше лишається, якщо дозволена
  function fillItems(tr, st) {
    const sel = tr.querySelector('.split-item'), dep = tr.querySelector('.split-dep').value;
    const allowed = allowedItems(st, dep);
    const want = sel.value || sel.dataset.selected;
    if (!allowed.length) {  // відділ не підходить жодному варіанту групи
      sel.innerHTML = '<option value="">— лише на профітцентр —</option>';
      sel.dataset.selected = '';
      tr.classList.add('split-row-bad');
      return;
    }
    tr.classList.remove('split-row-bad');
    sel.innerHTML = allowed.map(c => `<option value="${esc(c)}">${esc(label(st, c))}</option>`).join('');
    sel.value = allowed.includes(want) ? want : (!dep || (pc.has(dep) && st.direct) ? allowed[0] : allowed[allowed.length - 1]);
    sel.dataset.selected = sel.value;
  }

  // Вибрали статтю в рядку: з розподілом — відділ = власник (якщо він один), позапланова — «Інше»,
  // пряма — відділ лише профітцентр
  function itemChanged(tr, st) {
    const dep = tr.querySelector('.split-dep'), it = tr.querySelector('.split-item').value;
    tr.querySelector('.split-item').dataset.selected = it;
    if (it && it === st.unplanned) {
      dep.value = OTHER;
    } else if (it && it === st.allocated && !allocDeps(st).includes(dep.value)) {
      dep.value = st.owners.length === 1 ? st.owners[0] : '';
    } else if (it && it === st.direct && dep.value && !pc.has(dep.value)) {
      dep.value = '';
    }
    dep.dataset.selected = dep.value;
    fillItems(tr, st);
  }

  function addRow(dep, amount) {
    const tr = tpl.content.firstElementChild.cloneNode(true);
    rowsEl.appendChild(tr);
    if (dep) tr.querySelector('.split-dep').value = dep;
    if (amount) tr.querySelector('.split-amount').value = amount;
    const st = state();
    if (st) { fillDeps(tr, st); if (dep) { tr.querySelector('.split-dep').value = dep; } fillItems(tr, st); }
    return tr;
  }

  function total() {
    const rows = [...rowsEl.querySelectorAll('.split-row')];
    const sum = rows.reduce((s, tr) => s + num(tr.querySelector('.split-amount').value), 0);
    const need = num(($('amount') || {}).value);
    const cur = ($('currency') || {}).value || '';
    const ok = need > 0 && Math.abs(sum - need) < 0.005;
    totalEl.className = 'split-total ' + (ok ? 'is-ok' : 'is-bad');
    totalEl.textContent = need > 0
      ? `Розподілено ${money(sum)} з ${money(need)} ${cur}` + (ok ? ' ✓' : ` — ${sum < need ? 'не вистачає' : 'зайве'} ${money(Math.abs(need - sum))}`)
      : `Розподілено ${money(sum)} ${cur} — вкажіть суму заявки`;
  }

  // Залишок бюджету рядка (той самий запит, що й під статтею, — /budget/remaining)
  const restTimers = new WeakMap();
  function rest(tr) {
    const url = box.dataset.budgetUrl, span = tr.querySelector('.split-rest');
    if (!url || !span) return;
    clearTimeout(restTimers.get(tr));
    restTimers.set(tr, setTimeout(() => {
      const dep = tr.querySelector('.split-dep').value, it = tr.querySelector('.split-item').value;
      if (!dep || !it) { span.textContent = ''; return; }
      const p = new URLSearchParams({ department: dep, item: it, amount: tr.querySelector('.split-amount').value || '',
        currency: ($('currency') || {}).value || '', pay_date: ($('pay_date') || {}).value || '', id: box.dataset.id || '' });
      fetch(url + '?' + p, { headers: { Accept: 'application/json' } }).then(r => r.ok ? r.json() : null).then(d => {
        if (!d || !d.enabled || !d.in_budget) { span.textContent = d && d.enabled ? 'немає в бюджеті' : ''; span.className = 'split-rest muted'; return; }
        span.textContent = (d.negative ? 'перевитрата ' : 'залишок ') + d.fmt.remaining + ' USD' + (d.over ? ' · понад залишок' : '');
        span.className = 'split-rest ' + (d.over || d.negative ? 'is-bad' : 'muted');
      }).catch(() => { span.textContent = ''; });
    }, 300));
  }

  // «Інші варіанти: … · …» під статтею — варіанти її групи; варіант зі списку статей користувача підставляється кліком
  const variantsEl = $('exp-variants');
  function renderVariants() {
    if (!variantsEl) return;
    const code = item(), g = !contractor() && groups[code];
    const others = g ? [['allocated', 'з розподілом'], ['direct', 'пряма'], ['unplanned', 'позапланова']]
      .filter(([r]) => g[r] && g[r] !== code) : [];
    variantsEl.hidden = !others.length;
    if (!others.length) { variantsEl.textContent = ''; return; }
    const picker = window.expensePicker;
    variantsEl.innerHTML = 'Інші варіанти статті: ' + others.map(([r, kind]) => {
      const c = g[r], text = `${esc(names[c] || c)} <span class="muted">(${kind})</span>`;
      return picker && picker.has && picker.has(c)
        ? `<button type="button" class="btn-link exp-variant" data-code="${esc(c)}">${text}</button>`
        : `<span class="exp-variant-off" title="Цієї статті немає у вашому списку статей">${text}</span>`;
    }).join(' · ');
  }
  if (variantsEl) variantsEl.addEventListener('click', e => {
    const b = e.target.closest('.exp-variant');
    if (b && window.expensePicker) { window.expensePicker.pick(b.dataset.code); render(); }
  });

  function render() {
    renderVariants();
    const st = state();
    box.hidden = !st;
    box.dataset.active = st ? '1' : '';
    sent.disabled = !st;
    if (!st) return;
    const showBody = st.required || open;
    why.textContent = st.required
      ? `— обов'язково: «${deptName(department())}» не профітцентр, а пряму статтю можна віднести лише на профітцентр` +
        (st.allocated ? (st.owners.length ? `; статтю з розподілом — на її власника` : '') : '. Варіанта з розподілом для цієї статті немає — лише на профітцентри (або виберіть статтю без «(прямий)»)')
      : '';
    offer.hidden = showBody;
    body.hidden = !showBody;
    body.querySelectorAll('input, select').forEach(el => { el.disabled = !showBody; });
    if (showBody && !rowsEl.querySelector('.split-row')) {
      // Перший рядок — уся сума: на вибраний варіант (позапланова — «Інше», з розподілом — власник), пряма —
      // свій відділ-профітцентр або власник варіанта з розподілом
      const own = department(), ad = allocDeps(st), code = item();
      const first = code === st.unplanned ? OTHER
        : (code === st.direct && pc.has(own)) || (code === st.allocated && ad.includes(own)) ? own
        : (ad.includes(own) && st.allocated) ? own : (st.owners.length === 1 ? st.owners[0] : (pc.has(own) && st.direct ? own : ''));
      addRow(first, ($('amount') || {}).value || '');
      if (!st.required) addRow('', '');
    }
    rowsEl.querySelectorAll('.split-row').forEach(tr => { fillDeps(tr, st); fillItems(tr, st); });
    total();
    rowsEl.querySelectorAll('.split-row').forEach(rest);
  }

  rowsEl.addEventListener('change', e => {
    const tr = e.target.closest('.split-row');
    if (!tr) return;
    if (e.target.classList.contains('split-dep')) {
      // новий відділ — стаття за замовчуванням: профітцентру пряма, іншому відділу з розподілом
      const sel = tr.querySelector('.split-item');
      sel.dataset.selected = ''; sel.selectedIndex = -1;
      const st = state(); if (st) fillItems(tr, st);
    } else if (e.target.classList.contains('split-item')) {
      const st = state(); if (st) itemChanged(tr, st);
    }
    rest(tr); total();
  });
  rowsEl.addEventListener('input', e => { if (e.target.classList.contains('split-amount')) { total(); rest(e.target.closest('.split-row')); } });
  rowsEl.addEventListener('click', e => {
    if (!e.target.closest('.split-remove')) return;
    e.target.closest('.split-row').remove();
    total();
  });
  $('split-add').addEventListener('click', () => { addRow('', ''); total(); });
  $('split-open').addEventListener('click', () => { open = true; render(); });
  $('split-cancel').addEventListener('click', () => {
    const st = state();
    if (st && st.required) { why.textContent = '— для цього відділу розподіл обов\'язковий'; return; }
    open = false; rowsEl.innerHTML = ''; render();
  });

  ['expense_code', 'department', 'currency', 'pay_date'].forEach(id => { const el = $(id); if (el) el.addEventListener('change', render); });
  const amount = $('amount');
  if (amount) amount.addEventListener('input', total);
  document.querySelectorAll('input[name=payment_kind]').forEach(r => r.addEventListener('change', render));
  // Статтю пікер змінює й без подій для форми — легка перевірка, як у залишку бюджету
  let last = '';
  setInterval(() => { const k = item() + '|' + department() + '|' + contractor(); if (k !== last) { last = k; render(); } }, 1000);
  render();
})();
