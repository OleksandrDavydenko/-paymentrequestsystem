// Панель «Підсумок» заявки: сума, контрагент, стаття, відділ, дата й форма оплати оновлюються під час введення;
// «Погоджуватимуть» і вимоги для відправки рахує сервер (route-preview) для поточних даних форми.
// Швидкий вибір дати оплати й різниця між рахунками та сумою заявки.
(function () {
  const form = document.getElementById('request-form');
  if (!form) return;
  const $ = id => document.getElementById(id);
  const val = id => ($(id) || {}).value || '';
  const radio = name => (form.querySelector(`input[name="${name}"]:checked`) || {}).value || '';
  const num = s => { const n = parseFloat(String(s || '').replace(/[\s ]/g, '').replace(',', '.')); return isNaN(n) ? null : n; };
  const fmt = n => n.toLocaleString('uk-UA', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const fmtDate = iso => iso ? iso.split('-').reverse().join('.') : '—';
  const lineRows = () => [...document.querySelectorAll('#lines-body tr')].filter(tr => tr.id !== 'no-lines');
  const steps = $('sum-steps');

  // Тип оплати: «Оплата підряднику» — статті відділу заявки й рахунки; «Непрямі витрати» — бюджет, без рахунків
  const listsEl = $('expense-lists');
  const LISTS = listsEl ? JSON.parse(listsEl.textContent) : null;
  let lastList = null;
  // Без питання «Що оплачуємо»: статті — бюджет користувача, блок «Рахунки» — за налаштуванням (auto — за статтею)
  function applyNoKind(dept) {
    const block = $('block-lines');
    if (!block) return;
    const code = val('expense_code');
    const direct = LISTS.direct[dept] !== false && (LISTS.contractor[dept] || LISTS.contractor[''] || []).some(i => i.code === code);
    block.hidden = LISTS.block === 'show' ? false : (LISTS.block === 'auto' ? !direct : true) && !lineRows().length;
    const mark = block.querySelector('.req-mark'), need = direct && LISTS.required && LISTS.required[dept];
    if (need && !mark) block.querySelector('.req-step').insertAdjacentHTML('beforeend', ' <span class="req-mark" title="Обов’язково">*</span>');
    if (!need && mark) mark.remove();
  }

  function applyKind() {
    if (!LISTS) return;
    const dept0 = val('department') || LISTS.current || '';
    if (LISTS.ask === false) { applyNoKind(dept0); return; }
    // Відділ без прямих витрат — кроку немає, заявка непряма
    const forced = LISTS.direct && LISTS.direct[dept0] === false;
    if ($('kind-choice')) {
      $('kind-choice').hidden = forced;
      $('kind-forced').hidden = !forced;
      if (forced) {
        const ind = form.querySelector('input[name=payment_kind][value=indirect]');
        if (ind && !ind.checked) ind.checked = true;
        form.classList.remove('kind-pending');
      }
    }
    const kind = radio('payment_kind'), dept = dept0;
    // Пояснення: вибраний тип підсвічено, «Ви обрали: …»
    document.querySelectorAll('#kind-explain li').forEach(li => li.classList.toggle('is-chosen', li.dataset.kind === kind));
    if ($('kind-chosen') && LISTS.kinds) {
      $('kind-chosen').hidden = !kind;
      if (kind) { $('kind-chosen-name').textContent = LISTS.kinds[kind].label; $('kind-chosen-text').textContent = LISTS.kinds[kind].chosen; }
    }
    const contractor = kind === 'contractor';
    const key = kind + '|' + dept;
    if (key !== lastList && window.expensePicker && kind) {
      lastList = key;
      window.expensePicker.setItems(contractor ? (LISTS.contractor[dept] || []) : LISTS.budget,
        contractor ? 'Для цього відділу ще не задано статей оплати підряднику — зверніться до адміністратора'
                   : 'Вам ще не призначено статті бюджету — зверніться до адміністратора');
    }
    const block = $('block-lines');
    if (block) block.hidden = !contractor && !lineRows().length;
    const mark = block && block.querySelector('.req-mark');
    if (block && LISTS.required) {
      const need = contractor && LISTS.required[dept];
      if (need && !mark) block.querySelector('.req-step').insertAdjacentHTML('beforeend', ' <span class="req-mark" title="Обов’язково">*</span>');
      if (!need && mark) mark.remove();
    }
  }

  // Швидкий вибір дати оплати
  document.querySelectorAll('#date-chips .chip').forEach(b => b.addEventListener('click', () => {
    $('pay_date').value = b.dataset.date;
    $('pay_date').dispatchEvent(new Event('change', {bubbles: true}));
  }));

  function selectedText(id) {
    const el = $(id);
    if (!el) return '';
    return el.tagName === 'SELECT' ? (el.options[el.selectedIndex] || {}).text || '' : el.value;
  }

  function linesDiff(amount, currency) {
    const box = $('lines-diff');
    if (!box) return;
    const rows = lineRows();
    let total = 0, other = false;
    rows.forEach(tr => {
      const a = num((tr.querySelector('[name$="-amount"]') || {}).value);
      const c = (tr.querySelector('select') || {}).value;
      if (a === null) return;
      if (c && c !== currency) other = true; else total += a;
    });
    const diff = amount === null ? 0 : Math.round((amount - total) * 100) / 100;
    box.hidden = !rows.length || other || !diff;
    box.textContent = diff ? `Не збігається із сумою заявки на ${fmt(Math.abs(diff))} ${currency}` : '';
  }

  function clientTodo(amount) {
    const todo = [];
    if (!val('counterparty_id') && $('cp-input')) todo.push('Контрагент');
    if ($('expense_code') && !val('expense_code')) todo.push('Стаття витрат');
    if (!amount || amount <= 0) todo.push('Сума');
    if (!val('pay_date')) todo.push('Дата оплати');
    if ($('organization_id') && !val('organization_id') && radio('payment_form') === 'bank') todo.push('Організація');
    if (form.querySelector('input[name=payer]') && !radio('payer')) todo.push('Платник');
    if (form.querySelector('input[name=accountant_type]') && !radio('accountant_type')) todo.push('Бухгалтер');
    if (form.querySelector('input[name=payment_kind]') && !radio('payment_kind')) todo.unshift('Що оплачуємо');
    return todo;
  }

  let serverTodo = [], timer = null, lastKey = '';
  function renderTodo(amount) {
    const wrap = $('sum-todo-wrap');
    if (!wrap) return;
    const items = clientTodo(amount).concat(serverTodo);
    $('sum-todo').innerHTML = '';
    items.forEach(t => { const li = document.createElement('li'); li.textContent = t; $('sum-todo').appendChild(li); });
    wrap.hidden = !items.length;
  }

  async function loadRoute(params) {
    try {
      const data = await (await fetch(steps.dataset.url + '?' + params, {headers: {Accept: 'application/json'}})).json();
      steps.innerHTML = '';
      data.steps.forEach(s => { const li = document.createElement('li'); li.textContent = s; steps.appendChild(li); });
      $('sum-notes').innerHTML = '';
      data.notes.forEach(n => { const d = document.createElement('div'); d.textContent = n; $('sum-notes').appendChild(d); });
      serverTodo = data.todo || [];
      renderTodo(num(val('amount')));
    } catch (e) { /* підказка маршруту — необов'язкова */ }
  }

  function update() {
    const amount = num(val('amount')), currency = val('currency') || 'UAH';
    $('sum-amount').textContent = amount === null ? '—' : `${fmt(amount)} ${currency}`;
    if ($('cp-input')) $('sum-cp').textContent = val('cp-input') || '—';
    if ($('expense-input')) $('sum-exp').textContent = val('expense-input') || '—';
    if ($('department')) $('sum-dept').textContent = selectedText('department') || '—';
    if ($('sum-kind') && form.querySelector('input[name=payment_kind]')) {
      $('sum-kind').textContent = (form.querySelector('input[name=payment_kind]:checked + span') || {}).textContent || '—';
    }
    applyKind();
    const formLabel = (form.querySelector('input[name=payment_form]:checked + span') || {}).textContent || '';
    $('sum-pay').textContent = `${fmtDate(val('pay_date'))} · ${formLabel}`;
    linesDiff(amount, currency);
    renderTodo(amount);
    if (!steps) return;
    const params = new URLSearchParams({
      amount: val('amount'), currency, department: val('department'), payment_form: radio('payment_form'),
      payment_kind: radio('payment_kind'),
      organization_id: val('organization_id'), payer: radio('payer'), accountant_type: radio('accountant_type'),
      counterparty_id: val('counterparty_id'), expense_code: val('expense_code'), pay_date: val('pay_date'),
      lines: String(lineRows().length)});
    const key = params.toString();
    if (key === lastKey) return;
    lastKey = key;
    clearTimeout(timer);
    timer = setTimeout(() => loadRoute(key), 350);
  }

  // «Що оплачуємо» — перший крок: доки не вибрано, решта форми прихована (і сервер заявку без нього не прийме)
  form.querySelectorAll('input[name=payment_kind]').forEach(r => r.addEventListener('change', () => {
    if (!form.classList.contains('kind-pending')) return;
    form.classList.remove('kind-pending');
    const next = $('cp-input');
    if (next) next.focus();
  }));

  form.addEventListener('input', update);
  form.addEventListener('change', update);
  // Пікери контрагента й статті змінюють приховані поля без подій — перевіряємо зрідка
  setInterval(update, 1500);
  update();
})();
