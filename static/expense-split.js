/* Розподіл прямої витрати між відділами у формі заявки (expense_split.py, templates/request/_split.html).
   Блок видно в непрямій витраті з прямою статтею або зі статтею з розподілом, що стоїть у парі «пряма → з розподілом»:
   пряма стаття, відділ заявки не профітцентр — розподіл обов'язковий; інакше — кнопкою «Розділити між відділами».
   У рядку — лише стаття пари: пряма — тільки на профітцентр, з розподілом — на її відділ-власника (так її розносить
   1С). Сума рядків має дорівнювати сумі заявки. */
(function () {
  'use strict';
  const box = document.getElementById('split-block');
  if (!box) return;
  const cfg = JSON.parse((document.getElementById('split-data') || {}).textContent || '{}');
  const pairs = cfg.pairs || {}, alloc = cfg.alloc || {}, ownersOf = cfg.owners || {}, names = cfg.names || {}, pc = new Set(cfg.pc || []);
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

  // Стаття заявки → пара: прямі статті, стаття з розподілом (або null — лише профітцентри), її власники.
  // Обов'язково — лише для прямої статті у відділі, що не є профітцентром
  function state() {
    const code = item();
    if (contractor()) return null;
    let directs, allocated;
    if (code in pairs) { directs = [code]; allocated = pairs[code] || null; }
    else if (code in alloc) { directs = alloc[code]; allocated = code; }
    else return null;
    return { directs, allocated, owners: allocated ? (ownersOf[allocated] || []) : [],
             required: code in pairs && !pc.has(department()) };
  }
  // Відділи, на які можна віднести статтю з розподілом: її власники, а якщо власник невідомий — будь-який
  const allocDeps = st => !st.allocated ? [] : (st.owners.length ? st.owners : (cfg.departments || []).map(d => d.code));
  // Статті, дозволені для відділу рядка (порожній відділ — усі статті пари)
  function allowedItems(st, dep) {
    if (!dep) return [...st.directs, st.allocated].filter(Boolean);
    return [...(pc.has(dep) ? st.directs : []), ...(allocDeps(st).includes(dep) ? [st.allocated] : [])];
  }

  // Відділи рядка: «Профітцентри» (пряма стаття) і «Власник статті …» (з розподілом); власник невідомий — «Інші
  // відділи». Вибраний раніше відділ, якого в новому списку немає, скидається
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function fillDeps(tr, st) {
    const sel = tr.querySelector('.split-dep');
    const was = sel.value || sel.dataset.selected || '';
    const deps = cfg.departments || [];
    const opt = d => `<option value="${esc(d.code)}">${esc(d.name)}</option>`;
    const pcs = deps.filter(d => pc.has(d.code));
    const ad = new Set(allocDeps(st));
    const rest = deps.filter(d => ad.has(d.code) && (st.owners.length || !pc.has(d.code)));
    let html = '<option value="">— відділ —</option>';
    html += `<optgroup label="${st.allocated ? 'Профітцентри — пряма стаття' : 'Профітцентри (пряму статтю — лише на них)'}">${pcs.map(opt).join('')}</optgroup>`;
    if (st.allocated) {
      const label = st.owners.length ? `Власник статті «${names[st.allocated] || st.allocated}»` : 'Інші відділи — стаття з розподілом';
      html += `<optgroup label="${esc(label)}">${rest.map(opt).join('')}</optgroup>`;
    }
    sel.innerHTML = html;
    const ok = pcs.some(d => d.code === was) || ad.has(was);
    sel.value = ok ? was : '';
    sel.dataset.selected = sel.value;
  }

  // Статті рядка за відділом; вибрана раніше лишається, якщо дозволена
  function fillItems(tr, st) {
    const sel = tr.querySelector('.split-item'), dep = tr.querySelector('.split-dep').value;
    const allowed = allowedItems(st, dep);
    const want = sel.value || sel.dataset.selected;
    if (!allowed.length) {  // відділ не підходить жодній статті пари
      sel.innerHTML = '<option value="">— лише на профітцентр —</option>';
      sel.dataset.selected = '';
      tr.classList.add('split-row-bad');
      return;
    }
    tr.classList.remove('split-row-bad');
    sel.innerHTML = allowed.map(c => `<option value="${esc(c)}">${esc(names[c] || c)}${c === st.allocated ? ' — з розподілом' : ' — пряма'}</option>`).join('');
    sel.value = allowed.includes(want) ? want : (dep && pc.has(dep) ? allowed[0] : allowed[allowed.length - 1]);
    sel.dataset.selected = sel.value;
  }

  // Вибрали статтю в рядку: з розподілом — відділ = власник (якщо він один), пряма — відділ лише профітцентр
  function itemChanged(tr, st) {
    const dep = tr.querySelector('.split-dep'), it = tr.querySelector('.split-item').value;
    tr.querySelector('.split-item').dataset.selected = it;
    if (it && it === st.allocated && !allocDeps(st).includes(dep.value)) {
      dep.value = st.owners.length === 1 ? st.owners[0] : '';
    } else if (it && st.directs.includes(it) && dep.value && !pc.has(dep.value)) {
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

  function render() {
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
      // Перший рядок — уся сума: профітцентр — на свій відділ, інакше — порожній відділ
      const own = department(), ad = allocDeps(st);
      const first = pc.has(own) || ad.includes(own) ? own : (st.owners.length === 1 ? st.owners[0] : '');
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
