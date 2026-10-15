// Адмінка: підсумок маршруту в «Налаштуваннях процесу».
(function () {
  // Підсумок маршруту оновлюється одразу при виборі режимів (до збереження)
  const summary = document.getElementById('route-summary');
  const settings = document.getElementById('process-settings');
  if (summary && settings) {
    settings.addEventListener('change', () => {
      const mode = (settings.querySelector('input[name=approval_mode]:checked') || {}).value;
      const head = (settings.querySelector('input[name=dept_head_mode]:checked') || {}).value;
      const observe = (settings.querySelector('input[name=cfo_mode]:checked') || {}).value === 'observe';
      // Без фіндиректора порядок «послідовно / паралельно» не діє
      settings.querySelectorAll('input[name=approval_mode]').forEach(r => { r.disabled = observe; });
      const note = document.getElementById('cfo-off-note');
      if (note) note.hidden = !observe;
      const steps = head === 'approve' ? ['Керівник відділу'] : [];
      if (observe) {
        summary.textContent = steps.concat('Бухгалтер', 'Оплата').join(' → ') + ' · фіндиректор спостерігає';
        return;
      }
      steps.push(...(mode === 'parallel' ? ['Бухгалтер + Фіндиректор (одночасно)'] : ['Бухгалтер', 'Фіндиректор']));
      summary.textContent = steps.concat('Оплата').join(' → ');
    });
  }
})();
