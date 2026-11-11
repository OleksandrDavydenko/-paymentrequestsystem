/* Вкладка «Бюджет»: вибір відділу, згортання відділів і груп статей, пошук статті, «лише понад план» і розшифровка факту. */
(function () {
  'use strict';
  const data = JSON.parse((document.getElementById('bg-data') || {}).textContent || '{}');
  const table = document.getElementById('bg-table');

  const dep = document.getElementById('bg-dep');
  if (dep) dep.addEventListener('change', () => dep.form.submit());

  if (table) {
    const search = document.getElementById('bg-search');
    const overOnly = document.getElementById('bg-over-only');
    const overTile = document.getElementById('bg-over-tile');
    const collapsed = new Set();      // згорнуті групи «відділ|група»
    const closedDepts = new Set();    // згорнуті відділи (видно лише рядок відділу)
    // Кілька відділів — спершу огляд: лише рядки відділів, розгортаються кліком
    const deptBodies = table.querySelectorAll('tbody.bg-dept-body');
    if (deptBodies.length > 1) deptBodies.forEach(tb => closedDepts.add(tb.dataset.dept));

    // Видимість: згорнутий відділ чи група — їхніх статей не видно; пошук і «понад план» показують лише статті, що
    // підходять, разом з їхньою групою та відділом. Відділ може бути й без груп (статті одним списком) — тому
    // «чи є що показати у відділі» рахуємо за статтями, а не за рядками груп
    function apply() {
      const q = (search.value || '').trim().toLowerCase();
      const onlyOver = overOnly.checked;
      const filtering = !!q || onlyOver;
      const shown = {}, deptShown = {};
      const deptOf = key => key.slice(0, key.indexOf('|'));
      table.querySelectorAll('tr.bg-item').forEach(tr => {
        const parent = tr.dataset.parent, dept = deptOf(parent);
        const match = (!q || tr.dataset.search.includes(q)) && (!onlyOver || tr.dataset.over === '1');
        if (match) { shown[parent] = true; deptShown[dept] = true; }
        tr.hidden = !match || (!filtering && (collapsed.has(parent) || closedDepts.has(dept)));
      });
      table.querySelectorAll('tr.bg-group').forEach(tr => {
        tr.hidden = filtering ? !shown[tr.dataset.group] : closedDepts.has(deptOf(tr.dataset.group));
        const btn = tr.querySelector('.bg-toggle');
        btn.setAttribute('aria-expanded', String(filtering || !collapsed.has(tr.dataset.group)));
      });
      deptBodies.forEach(tb => {
        tb.hidden = filtering && !deptShown[tb.dataset.dept];
        const btn = tb.querySelector('tr.bg-dept .bg-toggle');
        if (btn) btn.setAttribute('aria-expanded', String(filtering || !closedDepts.has(tb.dataset.dept)));
      });
    }

    table.addEventListener('click', e => {
      const toggle = e.target.closest('.bg-toggle');
      if (!toggle) return;
      const row = toggle.closest('tr');
      if (row.classList.contains('bg-group')) {
        const key = row.dataset.group;
        collapsed.has(key) ? collapsed.delete(key) : collapsed.add(key);
      } else {  // відділ — показати / сховати його групи й статті
        const key = row.closest('tbody').dataset.dept;
        closedDepts.has(key) ? closedDepts.delete(key) : closedDepts.add(key);
      }
      apply();
    });
    // «Згорнути все»: кілька відділів — до рядків відділів, один відділ — до рядків груп. Кнопок немає, коли
    // згортати нічого (один відділ без груп)
    const collapseBtn = document.getElementById('bg-collapse'), expandBtn = document.getElementById('bg-expand');
    if (collapseBtn) collapseBtn.addEventListener('click', () => {
      table.querySelectorAll('tr.bg-group').forEach(tr => collapsed.add(tr.dataset.group));
      if (deptBodies.length > 1) deptBodies.forEach(tb => closedDepts.add(tb.dataset.dept));
      apply();
    });
    if (expandBtn) expandBtn.addEventListener('click', () => { collapsed.clear(); closedDepts.clear(); apply(); });
    search.addEventListener('input', apply);
    overOnly.addEventListener('change', apply);
    if (overTile) overTile.addEventListener('click', () => { overOnly.checked = !overOnly.checked; apply(); });
    apply();
  }

  // ---------------------------------------------------------------- розшифровка факту
  const dlg = document.getElementById('bg-dialog');
  if (!dlg) return;
  const title = document.getElementById('bg-dialog-title');
  const sub = document.getElementById('bg-dialog-sub');
  const body = document.getElementById('bg-dialog-body');
  const fmt = v => Number(v || 0).toLocaleString('uk-UA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const day = s => s ? s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4) : '';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function period(months) {
    const ms = months.split(',').map(Number);
    const name = m => data.months[m - 1].toLowerCase();
    return ms.length === 1 ? name(ms[0]) + ' ' + data.year
      : name(ms[0]) + ' – ' + name(ms[ms.length - 1]) + ' ' + data.year;
  }

  document.addEventListener('click', e => {
    const btn = e.target.closest('.bg-fact');
    if (!btn) return;
    title.textContent = 'Розшифровка: ' + btn.dataset.name;
    const deptRow = btn.closest('tbody') && document.querySelector('#bg-dep option[value="' + CSS.escape(btn.dataset.dep) + '"]');
    sub.textContent = period(btn.dataset.months) + (deptRow ? ' · ' + deptRow.textContent : '') + ' · суми в USD';
    body.innerHTML = '<p class="muted">Завантаження документів з 1С…</p>';
    dlg.showModal();
    const url = data.url + '?' + new URLSearchParams({ dep: btn.dataset.dep, item: btn.dataset.item, y: data.year, m: btn.dataset.months });
    fetch(url, { headers: { 'Accept': 'application/json' } })
      .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
      .then(res => {
        if (res.error) { body.innerHTML = '<p class="err">Не вдалося отримати документи: ' + esc(res.error) + '</p>'; return; }
        if (!res.rows.length) { body.innerHTML = '<p class="muted">Документів немає.</p>'; return; }
        const rows = res.rows.map(r => '<tr>' +
          '<td data-label="Дата розподілу">' + day(r.date) + (r.distribution ? '<div class="muted">№ ' + esc(r.distribution) + '</div>' : '') + '</td>' +
          '<td data-label="Документ"><strong>' + esc(r.doc_type) + '</strong>' + (r.doc_number ? ' № ' + esc(r.doc_number) : '') +
            (r.doc_date ? '<div class="muted">від ' + day(r.doc_date) + '</div>' : '') + '</td>' +
          '<td data-label="Співробітник">' + (esc(r.employee) || '<span class="muted">—</span>') + '</td>' +
          '<td data-label="Примітка">' + esc(r.comment) +
            (r.status && r.status !== 'Прямий (відділ рядка)' ? ' <span class="badge grey">' + esc(r.status.toLowerCase()) + '</span>' : '') + '</td>' +
          '<td class="num" data-label="Сума, USD">' + fmt(r.amount) + '</td></tr>').join('');
        body.innerHTML = '<div class="table-wrap"><table class="stack bg-details"><thead><tr><th>Дата розподілу</th><th>Документ</th>' +
          '<th>Співробітник</th><th>Примітка</th><th class="num">Сума, USD</th></tr></thead><tbody>' + rows +
          '</tbody><tfoot><tr><td colspan="4">Разом: ' + res.rows.length + ' рядк(ів)</td><td class="num">' + fmt(res.total) +
          '</td></tr></tfoot></table></div>';
      })
      .catch(err => { body.innerHTML = '<p class="err">Не вдалося отримати документи: ' + esc(err.message) + '</p>'; });
  });
  document.getElementById('bg-dialog-close').addEventListener('click', () => dlg.close());
  dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
})();
