// Нові дизайни картки заявки («Налаштування → Дизайн»): «Покроковий майстер» — один крок на екрані, «Назад» / «Далі»;
// «Чек-лист» — розділи на одній сторінці, заповнений згортається в рядок-підсумок. Поля ті самі, що й у класичній
// картці (тип, список статей і блок рахунків перемикає request-summary.js); тут — переходи, перевірка кроку й підсумки.
(function () {
  const form = document.getElementById('request-form');
  if (!form || !['wizard', 'checklist'].includes(form.dataset.design)) return;
  const wizard = form.dataset.design === 'wizard';
  const $ = id => document.getElementById(id);
  const val = id => ($(id) || {}).value || '';
  const radio = name => form.querySelector(`input[name="${name}"]:checked`);
  const num = s => { const n = parseFloat(String(s || '').replace(/[\s  ]/g, '').replace(',', '.')); return isNaN(n) ? null : n; };
  const fmt = n => n.toLocaleString('uk-UA', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const lineRows = () => [...document.querySelectorAll('#lines-body tr')].filter(tr => tr.id !== 'no-lines');
  const text = el => !el ? '' : el.tagName === 'SELECT' ? ((el.options[el.selectedIndex] || {}).text || '') : el.value;
  const chosen = name => {  // підпис вибраної кнопки (у двоярусній — лише назва)
    const r = radio(name), span = r && r.closest('label').querySelector('span');
    return span ? (span.querySelector('b') || span).textContent.trim() : '';
  };
  const sections = [...form.querySelectorAll('section[data-step]')];
  const stepInput = form.querySelector('input[name=step]');
  const errorBox = $('wiz-error');

  // Крок пропускається: блоку немає (рахунки без прямої витрати) або тип визначено без питання
  function skipped(sec) {
    if (sec.hidden) return true;
    if (sec.dataset.step === 'kind') { const ch = $('kind-choice'); return !ch || ch.hidden; }
    return false;
  }
  const active = () => sections.filter(s => !skipped(s));
  const key = sec => sec.dataset.step;

  // Чого бракує на кроці: [підпис, елемент для фокусу]
  const CHECKS = {
    kind: () => radio('payment_kind') ? [] : [['Що оплачуємо', form.querySelector('input[name=payment_kind]')]],
    who: () => {
      const miss = [];
      if ($('cp-input') && !val('counterparty_id')) miss.push(['Контрагент', $('cp-input')]);
      if ($('expense-input') && !val('expense_code')) miss.push(['Стаття витрат', $('expense-input')]);
      return miss;
    },
    lines: () => ($('block-lines') && $('block-lines').querySelector('.req-mark') && !lineRows().length
      ? [['Рахунок — кнопка «Підбір рахунків»', $('pick-invoices')]] : []),
    amount: () => {
      const miss = [];
      if (!(num(val('amount')) > 0)) miss.push(['Сума', $('amount')]);
      if (!val('pay_date')) miss.push(['Дата оплати', $('pay_date')]);
      const acc = form.querySelector('input[name=accountant_type]'), payer = form.querySelector('input[name=payer]');
      if (acc && !radio('accountant_type')) miss.push(['Бухгалтер', acc]);
      if (payer && !radio('payer')) miss.push(['Платник', payer]);
      if ($('organization_id') && !val('organization_id') && (radio('payment_form') || {}).value === 'bank') miss.push(['Організація', $('organization_id')]);
      return miss;
    },
    note: () => [],
    review: () => [],
  };

  // Підсумок кроку одним рядком (чек-лист, сторінка перевірки)
  const payerText = () => chosen('accountant_type') || chosen('payer') || ($('organization_id') && val('organization_id') ? text($('organization_id')) : '');
  const linesText = () => { const r = lineRows(); return r.length ? `${r.length} рахунк. · ${($('lines-total') || {}).textContent || ''}` : 'рахунків не додано'; };
  const SUMMARY = {
    kind: () => chosen('payment_kind'),
    who: () => [val('cp-input'), val('expense-input'), $('department') ? text($('department')) : val('department-text')].filter(Boolean).join(' · '),
    lines: linesText,
    amount: () => {
      const a = num(val('amount'));
      return [a ? `${fmt(a)} ${val('currency')}` : '', val('pay_date') ? val('pay_date').split('-').reverse().join('.') : '',
              chosen('payment_form'), payerText()].filter(Boolean).join(' · ');
    },
    note: () => val('note').trim().slice(0, 90) || 'без примітки',
  };

  function showError(miss) {
    errorBox.textContent = 'Заповніть: ' + miss.map(m => m[0]).join(', ');
    errorBox.hidden = false;
    miss.forEach(([, el]) => { const f = el && el.closest('.field'); if (f) f.classList.add('need'); });
    const el = miss[0][1];
    if (el) { el.focus({preventScroll: true}); el.scrollIntoView({block: 'center', behavior: 'smooth'}); }
  }
  function hideError() {
    errorBox.hidden = true;
    form.querySelectorAll('.field.need').forEach(f => f.classList.remove('need'));
  }
  form.addEventListener('input', () => { if (!errorBox.hidden) hideError(); });
  form.addEventListener('change', () => { if (!errorBox.hidden) hideError(); });
  const focusFirst = sec => {
    const el = sec.querySelector('input:not([type=hidden]):not([type=radio]):not([readonly]), select, textarea');
    if (el && !el.value) el.focus({preventScroll: true});
  };

  // ---------- Покроковий майстер
  let current = null;
  function paintWizard() {
    const act = active(), i = act.indexOf(current);
    document.querySelectorAll('#wiz-steps li').forEach(li => {
      const sec = sections.find(s => key(s) === li.dataset.goto);
      li.hidden = !sec || skipped(sec);
      if (li.hidden) return;
      const j = act.indexOf(sec);
      li.classList.toggle('is-first', j === 0);  // без лінії зліва
      li.querySelector('.ws-num').textContent = j + 1;
      li.classList.toggle('is-current', sec === current);
      li.classList.toggle('is-done', j < i || (key(sec) !== 'review' && j !== i && !CHECKS[key(sec)]().length && sec.dataset.seen === '1'));
    });
    $('wiz-progress-text').textContent = `Крок ${i + 1} з ${act.length} · ${current.dataset.title}`;
    $('wiz-bar').style.width = `${Math.round((i + 1) / act.length * 100)}%`;
    $('wiz-back').hidden = i <= 0;
    const last = key(current) === 'review';
    $('wiz-next').hidden = last;
    $('wiz-next').textContent = key(act[i + 1] || current) === 'review' ? 'Перевірити заявку →' : 'Далі →';
    form.classList.toggle('on-review', last);
    const docs = $('documents');
    if (docs) docs.hidden = !last;
    if (last) {
      if ($('sum-payer')) $('sum-payer').textContent = payerText() || '—';
      if ($('sum-lines')) $('sum-lines').textContent = linesText();
      const linesFact = form.querySelector('.review-lines');
      if (linesFact) linesFact.hidden = !$('block-lines') || skipped($('block-lines'));
    }
  }
  function show(sec, scroll) {
    current = sec;
    sec.dataset.seen = '1';
    sections.forEach(s => s.classList.toggle('is-current', s === sec));
    if (stepInput) stepInput.value = key(sec);
    hideError();
    paintWizard();
    if (scroll) form.scrollIntoView({behavior: 'smooth', block: 'start'});
    focusFirst(sec);
  }
  // Уперед — лише через заповнені кроки (перший незаповнений відкривається з підказкою); назад — завжди
  function go(target) {
    const act = active(), from = act.indexOf(current), to = act.indexOf(target);
    for (let k = Math.max(from, 0); k < to; k++) {
      const miss = CHECKS[key(act[k])]();
      if (miss.length) { if (act[k] !== current) show(act[k], true); showError(miss); return; }
    }
    show(target, true);
  }

  // ---------- Чек-лист
  function paintChecklist() {
    let done = 0, total = 0;
    sections.forEach(sec => {
      const sk = skipped(sec);
      sec.classList.toggle('is-skipped', sk);
      if (sk) return;
      const optional = sec.dataset.optional === '1';
      const ok = !CHECKS[key(sec)]().length;
      sec.classList.toggle('is-done', ok && (!optional || !!val('note').trim()));
      if (!optional) { total++; if (ok) done++; }
      const sum = sec.querySelector('.cl-sum');
      if (sum) sum.textContent = SUMMARY[key(sec)] ? SUMMARY[key(sec)]() : '';
    });
    $('cl-count').textContent = done;
    $('cl-total').textContent = total;
    $('cl-bar').style.width = total ? `${Math.round(done / total * 100)}%` : '0';
  }
  function open(sec, scroll) {
    sections.forEach(s => {
      s.classList.toggle('is-open', s === sec);
      const h = s.querySelector('.cl-head');
      if (h) h.setAttribute('aria-expanded', s === sec ? 'true' : 'false');
    });
    if (stepInput) stepInput.value = sec ? key(sec) : '';
    hideError();
    if (sec) {
      if (scroll) sec.scrollIntoView({behavior: 'smooth', block: 'start'});
      focusFirst(sec);
    }
  }
  function continueFrom(sec) {
    const miss = CHECKS[key(sec)]();
    if (miss.length) { showError(miss); return; }
    paintChecklist();
    const act = active(), i = act.indexOf(sec);
    const next = act.slice(i + 1).find(s => !s.classList.contains('is-done') && s.dataset.optional !== '1')
      || act.slice(0, i).find(s => !s.classList.contains('is-done') && s.dataset.optional !== '1')
      || (act[i + 1] && act[i + 1].dataset.optional === '1' && !act[i + 1].classList.contains('is-done') ? act[i + 1] : null);
    open(next, true);
    if (!next) form.querySelector('.cl-bar').scrollIntoView({behavior: 'smooth', block: 'end'});
  }

  // ---------- Події
  if (wizard) {
    $('wiz-next').addEventListener('click', () => { const act = active(); go(act[act.indexOf(current) + 1] || current); });
    $('wiz-back').addEventListener('click', () => { const act = active(); show(act[Math.max(act.indexOf(current) - 1, 0)], true); });
    document.querySelectorAll('#wiz-steps li button').forEach(b => b.addEventListener('click', () => {
      const sec = sections.find(s => key(s) === b.closest('li').dataset.goto);
      if (sec && !skipped(sec)) go(sec);
    }));
    form.querySelectorAll('.wiz-goto').forEach(b => b.addEventListener('click', () => {
      const sec = sections.find(s => key(s) === b.dataset.goto);
      if (sec && !skipped(sec)) show(sec, true);
    }));
    // Вибір типу на першому кроці — одразу далі
    form.querySelectorAll('input[name=payment_kind]').forEach(r => r.addEventListener('change', () => {
      if (current && key(current) === 'kind') setTimeout(() => go(active()[active().indexOf(current) + 1]), 250);
    }));
  } else {
    sections.forEach(sec => {
      const h = sec.querySelector('.cl-head');
      if (h) h.addEventListener('click', () => open(sec.classList.contains('is-open') ? null : sec, false));
      const b = sec.querySelector('.cl-continue');
      if (b) b.addEventListener('click', () => continueFrom(sec));
    });
    form.querySelectorAll('input[name=payment_kind]').forEach(r => r.addEventListener('change', () => {
      const sec = sections.find(s => key(s) === 'kind');
      if (sec && sec.classList.contains('is-open')) setTimeout(() => continueFrom(sec), 250);
    }));
  }
  // Enter у полі — не відправляти заявку, а перейти далі
  form.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.target.tagName === 'TEXTAREA' || e.target.type === 'submit' || e.target.type === 'button') return;
    if (e.target.closest('.exp-picker, .participant-add')) return;  // пікери самі обробляють Enter
    e.preventDefault();
    if (wizard) { if (key(current) !== 'review') $('wiz-next').click(); }
    else { const sec = e.target.closest('section[data-step]'); if (sec) continueFrom(sec); }
  });
  // Тип, відділ, стаття змінюють, які кроки є (request-summary.js) — перемалювати після нього
  const repaint = () => setTimeout(() => {
    if (wizard) { if (current && skipped(current)) show(active()[0]); else if (current) paintWizard(); }
    else paintChecklist();
  }, 0);
  form.addEventListener('change', repaint);
  form.addEventListener('input', repaint);
  setInterval(repaint, 1500);  // пікери контрагента й статті змінюють приховані поля без подій

  // ---------- Старт: крок із помилкою сервера / після «Записати» / перевірка збереженої / перший
  const start = sections.find(s => key(s) === form.dataset.start && !skipped(s));
  if (wizard) {
    sections.forEach(s => { if (form.dataset.start === 'review') s.dataset.seen = '1'; });
    show(start || active()[0], false);
  } else {
    paintChecklist();
    const firstOpen = start || (form.dataset.start === 'review' ? active().find(s => !s.classList.contains('is-done') && s.dataset.optional !== '1')
                                                                : active()[0]);
    open(firstOpen || null, false);
  }
})();
