// Адмінка: оргструктура — відділи, які очолює керівник; підсумок маршруту в «Налаштуваннях процесу».
(function () {
  // Вікно «Відділи керівника»
  const dialog = document.getElementById('head-dialog');
  if (dialog) {
    const form = dialog.querySelector('form');
    const list = document.getElementById('head-dep-list');
    const search = document.getElementById('head-dep-search');
    const boxes = () => [...list.querySelectorAll('input[name=departments]')];
    const filter = () => {
      const q = search.value.trim().toLowerCase();
      list.querySelectorAll('.budget-dep').forEach(l => { l.hidden = q && !l.textContent.toLowerCase().includes(q); });
    };
    document.querySelectorAll('.head-edit').forEach(btn => btn.addEventListener('click', () => {
      const u = JSON.parse(btn.dataset.user);
      form.user_id.value = u.id;
      form.name.value = u.name;
      form.email.value = u.email;
      document.getElementById('head-title').textContent = 'Відділи керівника — ' + u.name;
      // Відділи, яких уже немає в довіднику, показуємо окремо, щоб їх можна було зняти
      list.querySelectorAll('.budget-dep.missing').forEach(el => el.remove());
      const known = new Set(boxes().map(b => b.value));
      u.deps.filter(d => !known.has(d)).forEach(d => {
        const label = document.createElement('label');
        label.className = 'budget-dep missing';
        label.innerHTML = '<input type="checkbox" name="departments"> <span></span> <span class="muted">немає в довіднику</span>';
        label.querySelector('input').value = d;
        label.querySelector('span').textContent = d;
        list.prepend(label);
      });
      boxes().forEach(b => { b.checked = u.deps.includes(b.value); });
      search.value = '';
      filter();
      dialog.showModal();
    }));
    search.addEventListener('input', filter);
    const setAll = on => boxes().forEach(b => { if (!b.closest('label').hidden) b.checked = on; });
    document.getElementById('head-all').addEventListener('click', () => setAll(true));
    document.getElementById('head-none').addEventListener('click', () => setAll(false));
  }

  // Змінений відділ у рядку — підсвітити кнопку «Зберегти» цього рядка
  document.querySelectorAll('.dept-select').forEach(sel => sel.addEventListener('change', () => {
    const btn = document.querySelector(`#${CSS.escape(sel.getAttribute('form'))} button[type=submit]`);
    if (btn) btn.classList.add('btn-primary');
  }));

  // Підсумок маршруту оновлюється одразу при виборі режимів (до збереження)
  const summary = document.getElementById('route-summary');
  const settings = document.getElementById('process-settings');
  if (summary && settings) {
    settings.addEventListener('change', () => {
      const mode = (settings.querySelector('input[name=approval_mode]:checked') || {}).value;
      const head = (settings.querySelector('input[name=dept_head_mode]:checked') || {}).value;
      const steps = head === 'approve' ? ['Керівник відділу'] : [];
      steps.push(...(mode === 'parallel' ? ['Бухгалтер + Фіндиректор (одночасно)'] : ['Бухгалтер', 'Фіндиректор']));
      summary.textContent = steps.concat('Оплата').join(' → ');
    });
  }
})();
