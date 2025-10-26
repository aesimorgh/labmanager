// static/js/lm-tooth.js
(function (w) {
  function code(q, n){ return String(q*10 + n); }

  function refreshSummaryFrom(hiddenEl, summaryEl){
    if (!hiddenEl || !summaryEl) return;
    const codes = (hiddenEl.value || '').split(',').map(s=>s.trim()).filter(Boolean);
    if (!codes.length){
      summaryEl.textContent = 'هنوز دندانی انتخاب نشده است.';
      summaryEl.classList.add('text-muted');
      return;
    }
    summaryEl.classList.remove('text-muted');
    const g = {1:[],2:[],3:[],4:[]};
    codes.forEach(c=>{
      const q = parseInt(c[0],10), n = parseInt(c.slice(1),10);
      if (g[q] && n>=1 && n<=8) g[q].push(n);
    });
    Object.keys(g).forEach(k=> g[k].sort((a,b)=>a-b));
    const parts = [];
    if (g[1].length) parts.push('بالا راست: ' + g[1].join(', '));
    if (g[2].length) parts.push('بالا چپ: '   + g[2].join(', '));
    if (g[3].length) parts.push('پایین چپ: '  + g[3].join(', '));
    if (g[4].length) parts.push('پایین راست: ' + g[4].join(', '));
    summaryEl.textContent = parts.join('  |  ');
  }

  function prefillFromHidden(hiddenSel, chipSel, summarySel){
    const hidden = document.querySelector(hiddenSel);
    if (!hidden) return;
    const raw = (hidden.value || '').trim();
    if (!raw) { 
      const s = document.querySelector(summarySel);
      if (s) refreshSummaryFrom(hidden, s);
      return;
    }
    const selected = new Set(raw.split(',').map(s=>s.trim()).filter(Boolean));
    document.querySelectorAll(chipSel).forEach(c=>{
      const q = parseInt(c.dataset.q,10);
      const n = parseInt(c.dataset.n,10);
      if (selected.has(code(q,n))) c.classList.add('active');
    });
    const s = document.querySelector(summarySel);
    if (s) refreshSummaryFrom(hidden, s);
  }

  // اختیاری: اگر خواستی تعاملات را هم بده
  function attachInteractions(hiddenSel, chipSel, summarySel){
    const hidden = document.querySelector(hiddenSel);
    if (!hidden) return;
    const chips = document.querySelectorAll(chipSel);
    const summary = document.querySelector(summarySel);

    chips.forEach(ch=>{
      if (ch.dataset.bound === '1') return;
      ch.dataset.bound = '1';
      ch.addEventListener('click', ()=>{
        ch.classList.toggle('active');
        const arr = [];
        document.querySelectorAll(chipSel+'.active').forEach(a=>{
          arr.push(code(parseInt(a.dataset.q,10), parseInt(a.dataset.n,10)));
        });
        arr.sort((a,b)=>parseInt(a,10)-parseInt(b,10));
        hidden.value = arr.join(',');
        if (summary) refreshSummaryFrom(hidden, summary);
      });
    });
  }

  w.LMTooth = { prefillFromHidden, attachInteractions };
})(window);
