// labmanager/static/js/date-utils.experimental.v1.js
;(function (w) {
  const NS = 'LMDateX';

  function fa2en(s) {
    const fa = '۰۱۲۳۴۵۶۷۸۹', ar = '٠١٢٣٤٥٦٧٨٩';
    return String(s || '')
      .replace(/[۰-۹]/g, d => '' + fa.indexOf(d))
      .replace(/[٠-٩]/g, d => '' + ar.indexOf(d));
  }

  // input: 'YYYY-MM-DD'  -> output: 'YYYY/MM/DD' (jalali) or null
  function isoToJalali(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((iso || '').trim());
    if (!m) return null;
    try {
      const gy = +m[1], gm = +m[2], gd = +m[3];
      /* نیاز به persian-date که الان در صفحه‌ات لود می‌شود */
      return new persianDate([gy, gm, gd]).calendar('persian').format('YYYY/MM/DD');
    } catch (e) { return null; }
  }

  // input: 'YYYY/MM/DD' (jalali, with fa/ar digits ok) -> output: 'YYYY-MM-DD' or null
  function jalaliToISO(jal) {
    const s = fa2en(String(jal || '').trim().replace(/-/g, '/'));
    if (!/^\d{4}\/\d{1,2}\/\d{1,2}$/.test(s)) return null;
    try {
      const [jy, jm, jd] = s.split('/').map(Number);
      const pd = new persianDate([jy, jm, jd]);
      if (typeof pd.toCalendar === 'function') {
        return pd.toCalendar('gregorian').format('YYYY-MM-DD');
      }
      if (typeof pd.calendar === 'function') {
        return pd.calendar('gregorian').format('YYYY-MM-DD');
      }
      if (typeof pd.toGregorian === 'function') {
        const g = pd.toGregorian();
        if (g && g.year) {
          const mm = ('0' + g.month).slice(-2);
          const dd = ('0' + g.day).slice(-2);
          return g.year + '-' + mm + '-' + dd;
        }
      }
      return null;
    } catch (e) { return null; }
  }

  w[NS] = { fa2en, isoToJalali, jalaliToISO };
})(window);
