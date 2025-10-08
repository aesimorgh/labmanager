// static/js/toothpicker.experimental.v1.js
;(function (w) {
  const NS = 'LMToothX';

  // — کمکی‌ها
  function fdi(q, n) { return String(q * 10 + n); }
  function parse(csv) { return (csv || '').split(',').map(s => s.trim()).filter(Boolean); }
  function fa2en(s) {
    const fa = '۰۱۲۳۴۵۶۷۸۹', ar = '٠١٢٣٤٥٦٧٨٩';
    return String(s || '')
      .replace(/[۰-۹]/g, d => '' + fa.indexOf(d))
      .replace(/[٠-٩]/g, d => '' + ar.indexOf(d));
  }
  function extractFromNotes(txt) {
    if (!txt) return '';
    const m = fa2en(String(txt)).match(/دندان‌ها\s*:\s*([0-9,\s،]+)/);
    if (!m) return '';
    return m[1].replace(/،/g, ',').split(',').map(s => s.trim()).filter(Boolean).join(',');
  }
  function refreshSummary(hiddenEl, summaryEl) {
    if (!hiddenEl || !summaryEl) return;
    const codes = parse(hiddenEl.value);
    if (!codes.length) {
      summaryEl.textContent = 'هنوز دندانی انتخاب نشده است.';
      summaryEl.classList.add('text-muted');
      return;
    }
    summaryEl.classList.remove('text-muted');
    const g = { 1: [], 2: [], 3: [], 4: [] };
    codes.forEach(c => {
      const q = parseInt(c[0], 10);
      const n = parseInt(c.slice(1), 10);
      if (g[q]) g[q].push(n);
    });
    Object.keys(g).forEach(k => g[k].sort((a, b) => a - b));
    const parts = [];
    if (g[1].length) parts.push('بالا راست: ' + g[1].join(', '));
    if (g[2].length) parts.push('بالا چپ: ' + g[2].join(', '));
    if (g[3].length) parts.push('پایین چپ: ' + g[3].join(', '));
    if (g[4].length) parts.push('پایین راست: ' + g[4].join(', '));
    summaryEl.textContent = parts.join('  |  ');
  }

  // — گارد اجرای یک‌باره روی یک ریشه (root)
  function bindOnce(root) {
    if (root.__tp_bound__) return true;
    root.__tp_bound__ = true;
    return false;
  }

  // — نقطهٔ ورود
  function init(opts) {
    opts = opts || {};
    const root = opts.root ? document.querySelector(opts.root) : document;
    if (!root) return;

    // جلوگیری از دوباره‌بستن رویدادها
    if (bindOnce(root)) return;

    const hidden  = root.querySelector(opts.hidden  || '#teeth_fdi');
    const chips   = root.querySelectorAll(opts.chips || '.tooth-chip');
    const summary = root.querySelector(opts.summary || '#tooth-summary');
    const notes   = root.querySelector(opts.notes   || '#id_notes');

    // Prefill: hidden → data-codes → data-fdi → notes
    let current = hidden && hidden.value ? hidden.value.trim() : '';
    if (!current) {
      const initEl = root.querySelector('#teeth_init');
      if (initEl) current = (initEl.getAttribute('data-codes') || '').trim();
    }
    if (!current) {
      const card = root.querySelector('#tooth-card');
      if (card) current = (card.getAttribute('data-fdi') || '').trim();
    }
    if (!current && notes) current = extractFromNotes(notes.value);

    if (current) {
      const selected = new Set(parse(current));
      chips.forEach(c => {
        const q = parseInt(c.dataset.q, 10);
        const n = parseInt(c.dataset.n, 10);
        if (selected.has(fdi(q, n))) c.classList.add('active');
      });
      if (hidden) {
        hidden.value = Array.from(selected)
          .sort((a, b) => parseInt(a, 10) - parseInt(b, 10))
          .join(',');
      }
    }
    refreshSummary(hidden, summary);

    // کلیک روی چیپ‌ها
    chips.forEach(el => el.addEventListener('click', function () {
      if (!hidden) return;
      el.classList.toggle('active');
      const code = fdi(parseInt(el.dataset.q, 10), parseInt(el.dataset.n, 10));
      let arr = parse(hidden.value);
      if (el.classList.contains('active')) {
        if (!arr.includes(code)) arr.push(code);
      } else {
        arr = arr.filter(c => c !== code);
      }
      arr.sort((a, b) => parseInt(a, 10) - parseInt(b, 10));
      hidden.value = arr.join(',');
      refreshSummary(hidden, summary);
    }));

    // دکمه‌های گروهی
    root.querySelectorAll('[data-bulk]').forEach(btn => btn.addEventListener('click', function () {
      if (!hidden) return;
      const t = btn.getAttribute('data-bulk');
      if (t === 'clear') {
        chips.forEach(c => c.classList.remove('active'));
        hidden.value = '';
        refreshSummary(hidden, summary);
        return;
      }
      const qs = (t === 'upper-all') ? [1, 2] : (t === 'lower-all' ? [3, 4] : []);
      chips.forEach(c => { const q = parseInt(c.dataset.q, 10); if (qs.includes(q)) c.classList.add('active'); });
      const arr = [];
      chips.forEach(c => {
        if (c.classList.contains('active')) {
          const q = parseInt(c.dataset.q, 10);
          const n = parseInt(c.dataset.n, 10);
          arr.push(fdi(q, n));
        }
      });
      arr.sort((a, b) => parseInt(a, 10) - parseInt(b, 10));
      hidden.value = arr.join(',');
      refreshSummary(hidden, summary);
    }));

    // هنگام submit: خط «دندان‌ها: …» را در notes جایگزین کن
    const form = hidden ? hidden.closest('form') : null;
    if (form && notes) {
      form.addEventListener('submit', function () {
        let txt = String(notes.value || '');
        // حذف نسخه‌های قبلی همین خط
        txt = txt.replace(/(^|\n)\s*دندان‌ها\s*:\s*[^\n]*\n?/g, '$1');
        const codes = parse(hidden.value);
        if (codes.length) {
          txt = (txt ? (txt.trim() + '\n') : '') + 'دندان‌ها: ' + codes.join(', ');
        }
        notes.value = txt;
      });
    }
  }

  w[NS] = { init };
})(window);
