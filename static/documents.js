// Картка заявки, «Документи»: перетягування файлів, вибір і завантаження одразу, перевірка типу й розміру до відправки.
(function () {
  const zone = document.getElementById('dropzone');
  if (!zone) return;
  const input = document.getElementById('doc-input');
  const error = zone.querySelector('.dz-error');
  const ALLOWED = ['pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'xls', 'xlsx'];
  const MAX = 10 * 1024 * 1024;
  const TOTAL = 25 * 1024 * 1024;

  function send(files) {
    const ok = new DataTransfer();
    const problems = [];
    let total = 0;
    [...files].forEach(f => {
      const ext = (f.name.split('.').pop() || '').toLowerCase();
      if (!ALLOWED.includes(ext)) problems.push(`«${f.name}» — недопустимий тип`);
      else if (f.size > MAX) problems.push(`«${f.name}» — більший за 10 МБ`);
      else { ok.items.add(f); total += f.size; }
    });
    if (total > TOTAL) problems.push('Разом більше 25 МБ — додайте файли частинами');
    error.hidden = !problems.length;
    error.textContent = problems.join('; ');
    if (!ok.files.length || total > TOTAL) return;
    input.files = ok.files;
    zone.classList.add('is-busy');
    zone.querySelector('.dz-idle').hidden = true;
    zone.querySelector('.dz-busy').hidden = false;
    zone.querySelector('.dz-busy').textContent = `Завантаження ${ok.files.length === 1 ? '«' + ok.files[0].name + '»' : ok.files.length + ' файлів'}…`;
    zone.submit();
  }

  zone.querySelector('.dz-pick').addEventListener('click', () => input.click());
  zone.addEventListener('click', e => { if (e.target === zone || e.target.closest('.dz-icon')) input.click(); });
  input.addEventListener('change', () => input.files.length && send(input.files));
  ['dragenter', 'dragover'].forEach(t => zone.addEventListener(t, e => { e.preventDefault(); zone.classList.add('is-over'); }));
  ['dragleave', 'drop'].forEach(t => zone.addEventListener(t, e => { e.preventDefault(); zone.classList.remove('is-over'); }));
  zone.addEventListener('drop', e => e.dataTransfer.files.length && send(e.dataTransfer.files));
  // Файл, кинутий повз зону, браузер не має відкривати замість сторінки
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('drop', e => { if (!zone.contains(e.target)) e.preventDefault(); });
})();
