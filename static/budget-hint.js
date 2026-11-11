/* Залишок бюджету статті у формі заявки: план місяця оплати, факт 1С, заявки в дорозі й залишок (USD);
   сума заявки понад залишок — попередження (і, за налаштуванням, фіндиректор або заборона відправки).
   Дані — /budget/remaining; оновлюється, коли змінюються стаття, відділ, сума, валюта чи дата оплати. */
(function () {
  'use strict';
  const box = document.getElementById('budget-hint');
  if (!box) return;
  const amountNote = document.getElementById('budget-amount-note');
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const MODE_TEXT = {
    cfo: 'Таку заявку обов\'язково погоджуватиме фіндиректор.',
    block: 'Відправити її не вийде — зменшіть суму, змініть статтю чи місяць оплати.',
    warn: 'Заявку можна відправити — погоджувачі побачать попередження.',
  };

  function params() {
    const dep = $('department');
    return {
      item: ($('expense_code') || {}).value || '',
      department: (dep && dep.value) || box.dataset.department || '',
      amount: ($('amount') || {}).value || '',
      currency: ($('currency') || {}).value || '',
      pay_date: ($('pay_date') || {}).value || '',
      id: box.dataset.id || '',
    };
  }

  let lastKey = null, seq = 0, timer = null;

  function hide() {
    box.hidden = true;
    box.className = 'budget-hint';
    if (amountNote) amountNote.hidden = true;
  }

  function render(d) {
    if (!d.enabled || !d.in_budget) { hide(); return; }
    const f = d.fmt;
    const over = d.over;
    const low = !over && (d.negative || (d.used_pct != null && d.used_pct >= 90));
    box.className = 'budget-hint' + (over || d.negative ? ' is-over' : low ? ' is-low' : '');
    const pct = d.used_pct == null ? '' : d.used_pct + ' % використано';
    const width = Math.max(0, Math.min(100, d.used_pct || 0));
    const lvl = d.negative || (d.used_pct || 0) > 100 ? 'over' : (d.used_pct || 0) >= 90 ? 'warn' : 'ok';
    box.innerHTML =
      '<div class="budget-hint-title">Бюджет ' + esc(d.month_title) + ' · відділ «' + esc(d.dept_name) + '»' +
        (d.owner ? ' <span class="badge grey" title="Стаття з відділом-власником: витрати за нею — у бюджеті власника">власник статті</span>' : '') + '</div>' +
      '<div class="budget-hint-nums">План ' + f.plan + ' · факт 1С ' + f.fact + ' · у заявках ' + f.pending +
        (d.pending_count ? ' (' + d.pending_count + ')' : '') + '</div>' +
      '<div class="budget-hint-rest"><span>' + (d.negative ? 'Перевитрата' : 'Залишок') + ' <strong>' + f.remaining + ' USD</strong></span>' +
        '<span class="bg-bar lvl-' + lvl + '" aria-hidden="true"><span style="width:' + width + '%"></span></span>' +
        '<span class="muted">' + pct + '</span></div>' +
      (over ? '<div class="budget-hint-over">⚠ Заявка ≈ ' + f.amount_usd + ' USD — понад залишок на ' + f.over_by + ' USD. ' +
        esc(MODE_TEXT[d.over_mode] || '') + '</div>' : '');
    box.hidden = false;
    if (amountNote) {
      if (f.amount_usd) {
        amountNote.className = 'field-hint budget-amount-note' + (over ? ' budget-hint-over' : '');
        amountNote.textContent = '≈ ' + f.amount_usd + ' USD за курсом 1С — ' +
          (over ? 'понад залишок бюджету статті на ' + f.over_by + ' USD' : 'у межах бюджету статті (залишок ' + f.remaining + ')');
        amountNote.hidden = false;
      } else {
        amountNote.hidden = true;
      }
    }
  }

  function refresh() {
    const p = params();
    const key = JSON.stringify(p);
    if (key === lastKey) return;
    lastKey = key;
    if (!p.item) { hide(); return; }
    const my = ++seq;
    fetch(box.dataset.url + '?' + new URLSearchParams(p), { headers: { 'Accept': 'application/json' } })
      .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
      .then(d => { if (my === seq) render(d); })
      .catch(() => { if (my === seq) hide(); });
  }

  function soon() { clearTimeout(timer); timer = setTimeout(refresh, 250); }

  ['expense_code', 'department', 'currency', 'pay_date'].forEach(id => { const el = $(id); if (el) el.addEventListener('change', soon); });
  const amount = $('amount');
  if (amount) { amount.addEventListener('input', soon); amount.addEventListener('change', soon); }
  document.addEventListener('click', e => { if (e.target.closest('#date-chips')) soon(); });
  // Поля, які інші скрипти заповнюють без подій (сума рахунків, дата з кнопок), — легка перевірка раз на 1,5 с;
  // запит до сервера — лише коли щось змінилося
  setInterval(refresh, 1500);
  refresh();
})();
