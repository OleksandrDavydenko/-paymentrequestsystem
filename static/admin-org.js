// Адмінка: оргструктура — хто бачить заявки відділу; підсумок маршруту в «Налаштуваннях процесу».
(function () {
  // Вікно «Бачать заявки відділу»
  const dialog = document.getElementById('viewers-dialog');
  if (dialog) {
    const form = dialog.querySelector('form');
    const list = document.getElementById('viewers-list');
    const search = document.getElementById('viewers-search');
    const boxes = () => [...list.querySelectorAll('input[name=viewers]')];
    const filter = () => {
      const q = search.value.trim().toLowerCase();
      list.querySelectorAll('.budget-dep').forEach(l => { l.hidden = q && !l.textContent.toLowerCase().includes(q); });
    };
    document.querySelectorAll('.viewers-edit').forEach(btn => btn.addEventListener('click', () => {
      const d = JSON.parse(btn.dataset.dept);
      form.department.value = d.code;
      // Керівника беремо з рядка (і незбережений вибір теж), щоб вікно його не скинуло
      const headSelect = document.querySelector(`select[form="d-${CSS.escape(d.code)}"][name=head]`);
      form.head.value = headSelect ? headSelect.value : '';
      document.getElementById('viewers-title').textContent = 'Бачать заявки відділу — ' + d.name;
      // Люди, яких уже немає в групах ролей, показуємо окремо, щоб їх можна було зняти
      list.querySelectorAll('.budget-dep.missing').forEach(el => el.remove());
      const known = new Set(boxes().map(b => b.value));
      d.viewers.filter(v => !known.has(v.id)).forEach(v => {
        const label = document.createElement('label');
        label.className = 'budget-dep missing';
        label.innerHTML = '<input type="checkbox" name="viewers"> <span></span> <span class="muted">немає в групах ролей</span>';
        label.querySelector('input').value = v.id;
        label.querySelector('span').textContent = v.name;
        list.prepend(label);
      });
      const ids = new Set(d.viewers.map(v => v.id));
      boxes().forEach(b => { b.checked = ids.has(b.value); });
      search.value = '';
      filter();
      dialog.showModal();
    }));
    search.addEventListener('input', filter);
    document.getElementById('viewers-none').addEventListener('click', () => boxes().forEach(b => { b.checked = false; }));
  }

  // Змінений відділ у рядку — підсвітити кнопку «Зберегти» цього рядка
  document.querySelectorAll('.dept-select, .head-select').forEach(sel => sel.addEventListener('change', () => {
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
