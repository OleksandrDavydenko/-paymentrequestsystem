// Картка користувача: «Статті бюджету» (прямі / усі відділи / вибрані відділи) з живим підрахунком.
(function () {
  const root = document.getElementById('budget-inline');
  if (!root) return;
  const form = root.closest('form');
  const ITEMS = JSON.parse(document.getElementById('budget-items').textContent || '[]');
  const depsBox = document.getElementById('budget-deps');
  const depList = document.getElementById('budget-dep-list');
  const search = document.getElementById('budget-dep-search');
  const total = document.getElementById('budget-total');
  const depBoxes = () => [...depList.querySelectorAll('input[name=departments]')];

  function word(n) {
    return n % 10 === 1 && n % 100 !== 11 ? 'стаття' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'статті' : 'статей';
  }

  // Скільки статей буде доступно з поточними налаштуваннями
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
                          : 'Людина не зможе вибрати жодної статті';
  }

  function filterDeps() {
    const q = search.value.trim().toLowerCase();
    depList.querySelectorAll('.budget-dep').forEach(l => { l.hidden = q && !l.textContent.toLowerCase().includes(q); });
  }

  root.addEventListener('change', recount);
  search.addEventListener('input', filterDeps);
  // Enter у пошуку не відправляє всю картку
  search.addEventListener('keydown', e => { if (e.key === 'Enter') e.preventDefault(); });
  document.getElementById('budget-all').addEventListener('click', () => {
    depBoxes().forEach(b => { if (!b.closest('label').hidden) b.checked = true; });
    if (form.mode.value !== 'selected') form.mode.value = 'selected';
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
  recount();
})();
