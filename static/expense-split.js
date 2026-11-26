/* Розподіл прямої витрати між відділами у формі заявки (expense_split.py, templates/request/_split.html).
   Блок видно, коли в непрямій витраті вибрано пряму статтю з пари «пряма → з розподілом»:
   відділ заявки не профітцентр — розподіл обов'язковий; профітцентр — кнопкою «Розділити між відділами».
   У рядку — лише стаття пари: пряма — тільки для профітцентру; сума рядків має дорівнювати сумі заявки. */
(function () {
  'use strict';
  const box = document.getElementById('split-block');
  if (!box) return;
  const cfg = JSON.parse((document.getElementById('split-data') || {}).textContent || '{}');
  const pairs = cfg.pairs || {}, names = cfg.names || {}, pc = new Set(cfg.pc || []);
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

  function state() {
    const code = item();
    if (contractor() || !pairs[code]) return null;
    return { direct: code, allocated: pairs[code], required: !pc.has(department()) };
  }

  // Статті рядка: пряма — лише для профітцентру; вибрана раніше лишається, якщо дозволена
  function fillItems(tr, st) {
    const sel = tr.querySelector('.split-item'), dep = tr.querySelector('.split-dep').value;
    const allowed = dep && !pc.has(dep) ? [st.allocated] : [st.direct, st.allocated];
    const want = sel.value || sel.dataset.selected;
    sel.innerHTML = allowed.map(c => `<option value="${c}">${names[c] || c}${c === st.direct ? ' — пряма' : ' — з розподілом'}</option>`).join('');
    sel.value = allowed.includes(want) ? want : (dep && pc.has(dep) ? st.direct : st.allocated);
    sel.dataset.selected = sel.value;
  }

  function addRow(dep, amount) {
    const tr = tpl.content.firstElementChild.cloneNode(true);
    rowsEl.appendChild(tr);
    if (dep) tr.querySelector('.split-dep').value = dep;
    if (amount) tr.querySelector('.split-amount').value = amount;
    const st = state();
    if (st) fillItems(tr, st);
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

  function render() {
    const st = state();
    box.hidden = !st;
    box.dataset.active = st ? '1' : '';
    sent.disabled = !st;
    if (!st) return;
    const showBody = st.required || open;
    why.textContent = st.required
      ? `— обов'язково: «${deptName(department())}» не профітцентр, а пряму статтю можна віднести лише на профітцентр`
      : '';
    offer.hidden = showBody;
    body.hidden = !showBody;
    body.querySelectorAll('input, select').forEach(el => { el.disabled = !showBody; });
    if (showBody && !rowsEl.querySelector('.split-row')) {
      // Перший рядок — уся сума: профітцентр — на свій відділ, інакше — порожній відділ
      addRow(pc.has(department()) ? department() : '', ($('amount') || {}).value || '');
      if (!st.required) addRow('', '');
    }
    rowsEl.querySelectorAll('.split-row').forEach(tr => fillItems(tr, st));
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
