// Адмінка → Налаштування → Погодження: підсумок маршруту й правила фіндиректора (до збереження).
(function () {
  const settings = document.getElementById('process-settings');
  const summary = document.getElementById('route-summary');
  if (!settings || !summary) return;
  const rules = document.getElementById('cfo-rules');
  const checked = name => (settings.querySelector(`input[name=${name}]:checked`) || {}).value;

  // Коротко про правила фіндиректора — як cfo_rules.short на сервері
  function rulesShort() {
    const cur = (settings.querySelector('select[name=cfo_currency]') || {}).value || 'USD';
    const rows = [...settings.querySelectorAll('select.cfo-rule')].map(sel => {
      const limit = sel.closest('tr').querySelector('input[type=number]').value;
      return sel.value === 'threshold' ? `від ${Number(limit || 0).toLocaleString('uk-UA')} ${cur}`
        : (sel.value === 'always' ? 'завжди' : 'ніколи');
    });
    const extras = (settings.querySelector('input[name=cfo_new_counterparty]') || {}).checked
      || settings.querySelectorAll('input[name=cfo_expense_codes]:checked').length;
    return rows.length && rows.every(r => r === rows[0]) && !extras ? rows[0] : 'за правилами';
  }

  function update() {
    const mode = checked('approval_mode');
    const head = checked('dept_head_mode');
    const cfoMode = checked('cfo_mode');
    const observe = cfoMode === 'observe';
    // Без фіндиректора порядок «послідовно / паралельно» не діє
    settings.querySelectorAll('input[name=approval_mode]').forEach(r => { r.disabled = observe; });
    const note = document.getElementById('cfo-off-note');
    if (note) note.hidden = !observe;
    if (rules) rules.hidden = cfoMode !== 'threshold';
    settings.querySelectorAll('select.cfo-rule').forEach(sel => {
      sel.closest('tr').querySelector('.cfo-limit').hidden = sel.value !== 'threshold';
    });
    const cur = (settings.querySelector('select[name=cfo_currency]') || {}).value;
    settings.querySelectorAll('.cfo-cur').forEach(s => { if (cur) s.textContent = cur; });
    const steps = head === 'approve' ? ['Керівник відділу'] : [];
    if (observe) {
      summary.textContent = steps.concat('Бухгалтер', 'Оплата').join(' → ') + ' · фіндиректор спостерігає';
      return;
    }
    const cfo = 'Фіндиректор' + (cfoMode === 'threshold' ? ` (${rulesShort()})` : '');
    steps.push(...(mode === 'parallel' ? [`Бухгалтер + ${cfo} (одночасно)`] : ['Бухгалтер', cfo]));
    summary.textContent = steps.concat('Оплата').join(' → ');
  }
  settings.addEventListener('change', update);
  settings.addEventListener('input', e => { if (e.target.type === 'number') update(); });

  // Пошук статей у списку «Статті, які завжди погоджує фіндиректор»
  const search = settings.querySelector('.cfo-codes-search');
  if (search) {
    search.addEventListener('keydown', e => { if (e.key === 'Enter') e.preventDefault(); });
    search.addEventListener('input', () => {
      const q = search.value.trim().toLowerCase();
      settings.querySelectorAll('.cfo-code').forEach(l => { l.hidden = q && !l.textContent.toLowerCase().includes(q); });
    });
    settings.querySelector('.cfo-codes').addEventListener('change', () => {
      const n = settings.querySelectorAll('input[name=cfo_expense_codes]:checked').length;
      settings.querySelector('.cfo-codes summary .muted').textContent = `— вибрано ${n}`;
    });
  }
})();
