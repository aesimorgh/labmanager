(function() {
  // با انتخاب سفارش، مرحله‌ها رو از سرور بگیر و پر کن
  function loadStages(orderId) {
    const stageSelect = document.getElementById('id_stage_name');
    if (!stageSelect) return;

    stageSelect.innerHTML = '';
    const wait = document.createElement('option');
    wait.value = '';
    wait.textContent = 'در حال بارگذاری مراحل...';
    stageSelect.appendChild(wait);

    // endpoint ساده برای گرفتن مراحل
    const url = `/admin/core/digitallabtransfer/stages/?order_id=${encodeURIComponent(orderId)}`;

    fetch(url, { credentials: 'same-origin' })
      .then(r => r.json())
      .then(data => {
        stageSelect.innerHTML = '';
        if (!data || !data.length) {
          const opt = document.createElement('option');
          opt.value = '';
          opt.textContent = '— مرحله‌ای یافت نشد —';
          stageSelect.appendChild(opt);
          return;
        }
        data.forEach(label => {
          const opt = document.createElement('option');
          opt.value = label;
          opt.textContent = label;
          stageSelect.appendChild(opt);
        });
      })
      .catch(() => {
        stageSelect.innerHTML = '';
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '— خطا در دریافت مرحله‌ها —';
        stageSelect.appendChild(opt);
      });
  }

  document.addEventListener('DOMContentLoaded', function() {
    const orderSelect = document.getElementById('id_order');
    if (!orderSelect) return;

    orderSelect.addEventListener('change', function(e) {
      const val = e.target.value;
      if (val) loadStages(val);
    });
  });
})();
