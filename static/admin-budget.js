// Адмінка: вікно «Статті бюджету» користувача (прямі / усі відділи / вибрані відділи).
(function () {
  const dialog = document.getElementById('budget-dialog');
  if (!dialog) return;
  const form = dialog.querySelector('form');
  const ITEMS = JSON.parse(document.getElementById('budget-items').textContent || '[]');
  const depsBox = document.getElementById('budget-deps');
  const depList = document.getElementById('budget-dep-list');
  const search = document.getElementById('budget-dep-search');
  const total = document.getElementById('budget-total');
  const depBoxes = () => [...depList.querySelectorAll('input[name=departments]')];

  function word(n) {
    return n % 10 === 1 && n % 100 !== 11 ? 'стаття' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'статті' : 'статей';
  }

  // Живий підрахунок: скільки статей буде доступно з поточними налаштуваннями
  function recount() {
    const direct = form.direct.checked;
    const mode = form.mode.value;
    depsBox.classList.toggle('disabled', mode !== 'selected');
    const deps = new Set(depBoxes().filter(b => b.checked).map(b => b.value));
    let d = 0, a = 0;
    ITEMS.forEach(i => {
      if (i.k === 'direct') { if (direct) d++; }
      else if (mode === 'all' || (mode === 'selected' && i.d.some(x => deps.has(x)))) a++;
    });
    const n = d + a;
    total.className = 'budget-total' + (n ? '' : ' empty');
    total.textContent = n ? `Буде доступно: ${n} ${word(n)} (прямих ${d}, з розподілом ${a})`
                          : 'Користувач не зможе вибрати жодної статті';
  }

  document.querySelectorAll('.budget-edit').forEach(btn => btn.addEventListener('click', () => {
    const u = JSON.parse(btn.dataset.user);
    form.user_id.value = u.id;
    form.name.value = u.name;
    form.email.value = u.email;
    document.getElementById('budget-title').textContent = 'Статті бюджету — ' + u.name;
    form.direct.checked = !!u.direct;
    form.mode.value = u.all ? 'all' : (u.deps.length ? 'selected' : 'none');
    // Відділи, яких уже немає в довіднику, показуємо окремо, щоб їх можна було зняти
    depList.querySelectorAll('.budget-dep.missing').forEach(el => el.remove());
    const known = new Set(depBoxes().map(b => b.value));
    u.deps.filter(d => !known.has(d)).forEach(d => {
      const label = document.createElement('label');
      label.className = 'budget-dep missing';
      label.innerHTML = '<input type="checkbox" name="departments"> <span></span> <span class="muted">немає в довіднику</span>';
      label.querySelector('input').value = d;
      label.querySelector('span').textContent = d;
      depList.prepend(label);
    });
    depBoxes().forEach(b => { b.checked = u.deps.includes(b.value); });
    search.value = '';
    filterDeps();
    recount();
    dialog.showModal();
  }));

  function filterDeps() {
    const q = search.value.trim().toLowerCase();
    depList.querySelectorAll('.budget-dep').forEach(l => { l.hidden = q && !l.textContent.toLowerCase().includes(q); });
  }

  form.addEventListener('change', recount);
  search.addEventListener('input', filterDeps);
  document.getElementById('budget-all').addEventListener('click', () => {
    depBoxes().forEach(b => { if (!b.closest('label').hidden) b.checked = true; });
    recount();
  });
  document.getElementById('budget-none').addEventListener('click', () => {
    depBoxes().forEach(b => { if (!b.closest('label').hidden) b.checked = false; });
    recount();
  });
  // Відмічений відділ автоматично вмикає режим «Вибрані відділи»
  depList.addEventListener('click', e => {
    if (e.target.matches('input') && form.mode.value !== 'selected') { form.mode.value = 'selected'; recount(); }
  });
})();
