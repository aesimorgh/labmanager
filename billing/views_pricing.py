# billing/views_pricing.py
from __future__ import annotations
from decimal import Decimal
from typing import Optional
import re
import jdatetime

from django.shortcuts import render
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_GET
from django.urls import reverse, NoReverseMatch

from core.models import Product
from billing.services.pricing_advisor import compute_product_pricing_summary

# ـــــــــــ ابزارهای کوچک جلالی ـــــــــــ
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_ARABIC_DIGITS  = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def _to_ascii_digits(s: str) -> str:
    return s.translate(_PERSIAN_DIGITS).translate(_ARABIC_DIGITS)

def _parse_jalali_date(raw: Optional[str]):
    if not raw:
        return None
    s = _to_ascii_digits(raw.strip()).replace("-", "/")
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", s)
    if not m:
        return None
    jy, jm, jd = map(int, m.groups())
    try:
        return jdatetime.date(jy, jm, jd).togregorian()
    except Exception:
        return None

# ـــــــــــ API تست قبلی (برای سلامت مسیر) ـــــــــــ
@require_GET
def api_pricing_summary(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, "msg": "Pricing Advisor API active"}, json_dumps_params={"ensure_ascii": False})


# ـــــــــــ API اصلی گام ۱ ـــــــــــ
@require_GET
def api_pricing_compute(request: HttpRequest) -> JsonResponse:
    """
    ورودی (GET):
      - product_code      (اجباری: همان Order.order_type)
      - d_from, d_to      (شمسی، اختیاری)
      - include_open      (0/1)، پیش‌فرض 1
      - min_orders        (پیش‌فرض 3)
      - target_margin     (مثلاً 0.6 برای 60%، اختیاری)
      - markup            (مثلاً 2.2، اختیاری)
      - rounding_step     (پله رُند کردن قیمت، پیش‌فرض 10000)
      - method            ("history" | "bom" | "hybrid") پیش‌فرض "history"
    """
    pc = (request.GET.get("product_code") or "").strip()
    if not pc:
        return JsonResponse({"ok": False, "error": "product_code الزامی است"}, status=400)

    d_from = _parse_jalali_date(request.GET.get("d_from"))
    d_to   = _parse_jalali_date(request.GET.get("d_to"))

    include_open = (request.GET.get("include_open") or "1").strip() != "0"

    def D(x, default: Optional[Decimal] = None):
        if x is None or x == "":
            return default
        try:
            return Decimal(str(x))
        except Exception:
            return default

    min_orders     = int((request.GET.get("min_orders") or "3").strip())
    target_margin  = D(request.GET.get("target_margin"), None)
    markup         = D(request.GET.get("markup"), None)
    rounding_step  = D(request.GET.get("rounding_step"), Decimal("10000"))

    # NEW: method
    method = (request.GET.get("method") or "history").strip().lower()
    if method not in ("history", "bom", "hybrid"):
        method = "history"

    summary = compute_product_pricing_summary(
        product_code=pc,
        date_from=d_from,
        date_to=d_to,
        include_open=include_open,
        min_orders=min_orders,
        target_margin_pct=target_margin,
        markup_multiplier=markup,
        rounding_step=rounding_step,
        method=method,  # ← پاس دادن روش محاسبه
    )
    return JsonResponse(
        {"ok": True, "data": summary.to_dict(), "method": method},
        json_dumps_params={"ensure_ascii": False}
    )


# ـــــــــــ صفحهٔ UI ـــــــــــ
def pricing_advisor_page(request: HttpRequest):
    try:
        api_url = reverse("api_pricing_compute")
    except NoReverseMatch:
        api_url = "/billing/api/pricing/compute/"

    # فهرست محصولات فعال برای سلکت
    products = Product.objects.filter(is_active=True).order_by("name").values("code", "name")

    ctx = {
        "api_url": api_url,
        "products": list(products),
    }
    return render(request, "billing/pricing_advisor.html", ctx)
