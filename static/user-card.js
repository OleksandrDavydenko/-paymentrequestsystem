// Картка користувача в адмінці: підказки до відділу, «Очолює» / «Бачить заявки», незбережені зміни.
(function () {
  const form = document.getElementById('user-card');
  if (!form) return;
  const STAFF = JSON.parse(document.getElementById('dept-staff').textContent || '{}');
  const me = window.UC_PERSON_ID;

  // Пункт 2: хто бачить заявки вибраного відділу
  const dept = document.getElementById('uc-dept');
  const note = document.getElementById('uc-dept-note');
  function deptNote() {
    const s = STAFF[dept.value];
    if (!dept.value) { note.textContent = 'Без відділу заявки людини бачать лише вона сама, погоджувачі та учасники заявки.'; return; }
    if (!s) { note.textContent = ''; return; }
    const head = s.head ? (s.head.id === me ? 'ця людина' : s.head.name) : 'ще не призначено';
    const viewers = (s.viewers || []).filter(v => v.id !== me).map(v => v.name);
    note.textContent = `Керівник відділу: ${head}.` + (viewers.length ? ` Також бачать заявки: ${viewers.join(', ')}.` : '');
  }
  dept.addEventListener('change', deptNote);
  deptNote();

  // Пункт 3: керівник і так бачить заявки — «Бачить заявки» в цьому рядку не потрібне;
  // якщо у відділу інший керівник — попередити, що його буде замінено
  form.querySelectorAll('.uc-head-box').forEach(box => {
    const row = box.closest('tr');
    const view = row.querySelector('.uc-view-box');
    const warn = row.querySelector('.uc-replace');
    const sync = () => {
      view.disabled = box.checked;
      if (box.checked) view.checked = false;
      warn.hidden = !(box.checked && box.dataset.current);
    };
    box.addEventListener('change', sync);
    sync();
  });

  // Пункт 5: які види оплати має вибраний користувач 1С
  // «Користувач 1С» активний лише з роллю бухгалтера (вмикається одразу, як відмітити роль у пункті 1)
  const onecSection = document.getElementById('uc-onec-section');
  const onecFields = document.getElementById('uc-onec-fields');
  const ACC_ROLES = ['acc_cash', 'acc_resident', 'acc_nonresident'];
  const roleBoxes = [...document.querySelectorAll('input[name="roles"]')].filter(b => ACC_ROLES.includes(b.value));
  if (onecSection && onecFields && roleBoxes.length) {
    const sync = () => {
      const on = roleBoxes.some(b => b.checked);
      onecFields.disabled = !on;
      onecSection.classList.toggle('is-disabled', !on);
      document.getElementById('uc-onec-off').hidden = on;
    };
    roleBoxes.forEach(b => b.addEventListener('change', sync));
  }
  // Пункт 1: «Погоджує і оплачує» / «Лише оплачує» — лише коли вибрано роль бухгалтера
  const accMode = document.getElementById('uc-acc-mode');
  if (accMode && roleBoxes.length) {
    roleBoxes.forEach(b => b.addEventListener('change', () => { accMode.hidden = !roleBoxes.some(x => x.checked); }));
  }

  const onec = document.getElementById('uc-onec');
  const onecTypes = document.getElementById('onec-types');
  if (onec && onecTypes) {
    const TYPES = JSON.parse(onecTypes.textContent || '{}');
    const box = document.getElementById('uc-onec-types');
    const show = () => {
      const list = TYPES[onec.value] || [];
      box.textContent = !onec.value ? "Без зв'язку з 1С бухгалтер не бачитиме видів оплати (поле «Вид оплати» буде необов'язковим)."
        : list.length ? `Види оплати в 1С (${list.length}): ${list.join(', ')}` : 'У цього користувача 1С немає видів оплати.';
    };
    onec.addEventListener('change', show);
    show();
  }

  // Незбережені зміни
  const dirty = document.getElementById('uc-dirty');
  let changed = false, submitting = false;
  form.addEventListener('change', () => { changed = true; dirty.hidden = false; });
  form.addEventListener('submit', () => { submitting = true; });
  window.addEventListener('beforeunload', e => {
    if (changed && !submitting) { e.preventDefault(); e.returnValue = ''; }
  });
})();
