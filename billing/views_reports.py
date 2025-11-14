# billing/views_reports.py
from __future__ import annotations
from decimal import Decimal
from typing import Any, Dict, Optional
import re
import jdatetime

from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_GET
from django.shortcuts import render
from django.urls import reverse, NoReverseMatch

# ← مدل‌ها: اگر Product/Doctor در اپ دیگری هستند مسیر را اصلاح کن
from core.models import Doctor, Product

from billing.services.profit_report import profit_summary_by_criteria
from core.models import Order, Doctor
from billing.models import Invoice, InvoiceLine



# --- کمک‌تابع: تبدیل ارقام فارسی/عربی به لاتین ---
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_ARABIC_DIGITS  = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def _to_ascii_digits(s: str) -> str:
    return s.translate(_PERSIAN_DIGITS).translate(_ARABIC_DIGITS)


# --- پارسر تاریخ شمسی "YYYY/MM/DD" یا "YYYY-MM-DD" → date میلادی ---
def _parse_jalali_to_gregorian_date(raw: Optional[str]):
    """
    ورودی: '1404/08/15' یا '1404-08-15' (با ارقام فارسی/عربی هم اوکی است)
    خروجی: datetime.date میلادی یا None
    """
    if not raw:
        return None
    s = _to_ascii_digits(raw.strip())
    s = s.replace("-", "/")
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", s)
    if not m:
        return None
    jy, jm, jd = map(int, m.groups())
    try:
        g = jdatetime.date(jy, jm, jd).togregorian()
        return g
    except Exception:
        return None


def _decimal_to_str(d: Decimal | None) -> str:
    if d is None:
        return "0.00"
    # دو رقم اعشار برای نمایش یکنواخت
    q = Decimal("0.01")
    return str((d or Decimal("0")).quantize(q))


def _serialize_profit_summary(summary) -> Dict[str, Any]:
    """
    profit_summary_by_criteria → دیکشنری JSON-friendly با مقادیر Decimal به‌صورت رشته
    """
    data = summary.to_dict()
    # تبدیل Decimalها به رشته برای خروجی JSON پایدار
    for k, v in list(data.get("totals", {}).items()):
        if isinstance(v, Decimal):
            data["totals"][k] = _decimal_to_str(v)
    rows = data.get("orders", []) or []
    for r in rows:
        for k, v in list(r.items()):
            if isinstance(v, Decimal):
                r[k] = _decimal_to_str(v)
    return data


@require_GET
def api_profit_summary(request: HttpRequest) -> JsonResponse:
    """
    API گزارش سود/زیان با ورودی تاریخ‌های شمسی.
    پارامترها:
      d_from, d_to (jalali)  | doctor (exact) | order_type (exact)
      include_expense: '0'|'1'
      settlement: 'realized' | 'unrealized' | 'both'
      basis: 'invoice' | 'delivery' | 'payment'   (فعلاً فقط خوانده می‌شود)
    """
    # --- ورودی‌ها
    d_from_raw   = request.GET.get("d_from") or ""
    d_to_raw     = request.GET.get("d_to") or ""
    doctor_exact = (request.GET.get("doctor") or "").strip() or None
    order_type   = (request.GET.get("order_type") or "").strip() or None
    include_exp  = (request.GET.get("include_expense") or "1").strip() != "0"

    date_from = _parse_jalali_to_gregorian_date(d_from_raw)
    date_to   = _parse_jalali_to_gregorian_date(d_to_raw)

    # --- سرویس اصلی: خروجی پایه (totals + orders اولیه)
    summary = profit_summary_by_criteria(
        date_from=date_from,
        date_to=date_to,
        doctor_exact=doctor_exact,
        order_type_exact=order_type,
        include_period_expense=include_exp,
    )
    payload = _serialize_profit_summary(summary)  # شامل totals و orders (رشته‌ای‌شده)

    # --- پارامترهای حالت محاسبه
    raw_settlement = (request.GET.get("settlement") or "").strip().lower()
    _aliases = {
        "realized":   {"realized","paid","settled","paid_only","تحقق‌یافته","تحقق_یافته","تسویه","تسویه‌شده","تسویه_شده"},
        "unrealized": {"unrealized","open","outstanding","partial","issued","تحقق‌نیافته","تحقق_نیافته","دریافت‌نشده","باز"},
        "both":       {"both","هر_دو","هر-دو"},
        "all": {"all", "همه", "all_orders", "all_order"},
    }
    def _norm_settlement(val: str) -> str:
        if not val: return "all"
        for k, vs in _aliases.items():
            if val in vs: return k
        return "all"
    settlement = _norm_settlement(raw_settlement)

    raw_basis = (request.GET.get("basis") or "").strip().lower()
    basis = raw_basis if raw_basis in {"invoice","delivery","payment"} else "invoice"

    # --- ابزار Decimal امن روی رشته/عدد
    from decimal import Decimal, ROUND_HALF_UP
    def D(x):
        try:
            return Decimal(str(x if x is not None else "0"))
        except Exception:
            return Decimal("0")

    # --- نگاشت سفارش‌ها برای ساخت realized/unrealized
    rows = payload.get("orders") or []
    order_ids = []
    for r in rows:
        oid = r.get("order_id") or r.get("id")
        if oid: order_ids.append(int(oid))
    order_ids = list(set(order_ids))

    # برای جلوگیری از N+1
    invline_qs = InvoiceLine.objects.select_related("invoice").filter(order_id__in=order_ids)
    inv_map = {}
    for il in invline_qs:
        inv = il.invoice
        line_rev = (il.line_total
                    or (D(il.unit_count) * D(il.unit_price) - D(il.discount_amount))
                    or D("0"))
        inv_map[il.order_id] = {
            "line_revenue": line_rev,
            "invoice": {
                "grand_total": D(getattr(inv, "grand_total", 0)),
                "amount_due":  D(getattr(inv, "amount_due",  0)),
                "status":      (str(getattr(inv, "status", "")) or "").lower(),
                "invoice_id":  (inv.id if inv else None),
                "issued_at":   getattr(inv, "issued_at", None),
            },
        }

    ord_qs = Order.objects.filter(id__in=order_ids).only(
        "id","status","shipped_date","unit_count","price","order_type","doctor"
    )
    ord_map = {o.id: o for o in ord_qs}

    realized_rows, unrealized_rows = [], []
    opex_period = D((payload.get("totals") or {}).get("opex_period"))

    # --- تفکیک هر ردیف
    for r in rows:
        oid = r.get("order_id") or r.get("id")
        if not oid:
            continue
        oid = int(oid)

        cogs = D(r.get("material_cogs"))
        dlab = D(r.get("digital_lab_cost"))
        wage = D(r.get("wage_cost"))  # 🆕 هزینه دستمزد این سفارش

        ord_obj  = ord_map.get(oid)
        inv_info = inv_map.get(oid)


        # تحویل‌شده (برای حالت فاکتور نشده ولی delivered)
        is_delivered = False
        if ord_obj is not None:
            try:
                is_delivered = (getattr(ord_obj, "status", None) == "delivered") or bool(getattr(ord_obj, "shipped_date", None))
            except Exception:
                is_delivered = False

        if inv_info:
            line_rev = D(inv_info["line_revenue"])
            gtotal   = D(inv_info["invoice"]["grand_total"])
            amt_due  = D(inv_info["invoice"]["amount_due"])
            inv_stat = inv_info["invoice"]["status"]

            if inv_stat == "paid" or amt_due == D("0"):
                # تحقق‌یافته: فاکتور تسویه شده یا بدهی صفر
                rr = dict(r)
                rev   = line_rev
                gross = rev - (cogs + dlab + wage)  # 🆕 کم‌کردن دستمزد هم در سود ناخالص
                rr["invoice_status"] = inv_stat
                rr["revenue"]        = f"{rev.quantize(Decimal('0.01'))}"
                rr["gross_profit"]   = f"{gross.quantize(Decimal('0.01'))}"
                rr["material_cogs"]    = f"{cogs.quantize(Decimal('0.01'))}"
                rr["digital_lab_cost"] = f"{dlab.quantize(Decimal('0.01'))}"
                rr["wage_cost"]        = f"{wage.quantize(Decimal('0.01'))}"  # 🆕 دستمزد این سفارش
                realized_rows.append(rr)

            else:
                # تحقق‌نیافته: فاکتور صادر ولی هنوز دریافت کامل نشده
                ur = dict(r)
                expected_revenue = line_rev
                receivable_share = ((expected_revenue / gtotal) * amt_due) if gtotal > 0 else expected_revenue
                projected_gross  = expected_revenue - (cogs + dlab + wage)  # 🆕 کم‌کردن دستمزد
                ur["invoice_status"]    = inv_stat
                ur["expected_revenue"]  = f"{expected_revenue.quantize(Decimal('0.01'))}"
                ur["receivable_amount"] = f"{receivable_share.quantize(Decimal('0.01'))}"
                ur["projected_profit"]  = f"{projected_gross.quantize(Decimal('0.01'))}"
                ur["material_cogs"]     = f"{cogs.quantize(Decimal('0.01'))}"
                ur["digital_lab_cost"]  = f"{dlab.quantize(Decimal('0.01'))}"
                ur["wage_cost"]         = f"{wage.quantize(Decimal('0.01'))}"  # 🆕
                unrealized_rows.append(ur)

        else:
            # بدون فاکتور: اگر تحویل/ارسال نهایی شده → تحقق‌نیافته با «درآمد مورد انتظار»
            if is_delivered and ord_obj is not None:
                ur = dict(r)
                expected_revenue = (D(ord_obj.price or 0) * D(getattr(ord_obj, "unit_count", 1) or 1))
                projected_gross  = expected_revenue - (cogs + dlab + wage)  # 🆕
                ur["invoice_status"]    = "delivered"
                ur["expected_revenue"]  = f"{expected_revenue.quantize(Decimal('0.01'))}"
                ur["receivable_amount"] = ur["expected_revenue"]
                ur["projected_profit"]  = f"{projected_gross.quantize(Decimal('0.01'))}"
                ur["material_cogs"]     = f"{cogs.quantize(Decimal('0.01'))}"
                ur["digital_lab_cost"]  = f"{dlab.quantize(Decimal('0.01'))}"
                ur["wage_cost"]         = f"{wage.quantize(Decimal('0.01'))}"  # 🆕
                unrealized_rows.append(ur)


    # --- fallback: اگر realized خالی بود، از خطوط فاکتورهای PAID در همان بازه پر کن
    if settlement == "realized" and not realized_rows:
        q = InvoiceLine.objects.select_related("invoice", "order").filter(invoice__status="paid")
        if date_from:
            q = q.filter(invoice__issued_at__date__gte=date_from)
        if date_to:
            q = q.filter(invoice__issued_at__date__lte=date_to)
        for il in q:
            ord_obj = il.order
            if not ord_obj:
                continue
            cogs = D(getattr(ord_obj, "material_cogs", 0))
            dlab = D(getattr(ord_obj, "digital_lab_cost", 0))
            line_rev = D(il.line_total or (D(il.unit_count) * D(il.unit_price) - D(il.discount_amount)) or 0)
            rr = {
                "order_id": ord_obj.id,
                "doctor_name": getattr(ord_obj, "doctor", None),
                "product_code": getattr(ord_obj, "order_type", None),
                "material_cogs": f"{cogs.quantize(Decimal('0.01'))}",
                "digital_lab_cost": f"{dlab.quantize(Decimal('0.01'))}",
                "revenue": f"{line_rev.quantize(Decimal('0.01'))}",
                "gross_profit": f"{(line_rev - (cogs + dlab)).quantize(Decimal('0.01'))}",
            }
            realized_rows.append(rr)

    # --- جمع کل‌ها
    def sumD(rows_, key):
        s = Decimal("0")
        for _r in rows_:
            s += D(_r.get(key))
        return s

    realized_totals = {
        "revenue":          sumD(realized_rows, "revenue"),
        "material_cogs":    sumD(realized_rows, "material_cogs"),
        "digital_lab_cost": sumD(realized_rows, "digital_lab_cost"),
        "wage_cost":        sumD(realized_rows, "wage_cost"),      # 🆕
        "gross_profit":     sumD(realized_rows, "gross_profit"),
    }

    unrealized_expected_rev = sum(D(x.get("expected_revenue")) for x in unrealized_rows) if unrealized_rows else Decimal("0")
    unrealized_cogs         = sumD(unrealized_rows, "material_cogs")
    unrealized_dlab         = sumD(unrealized_rows, "digital_lab_cost")
    unrealized_wage         = sumD(unrealized_rows, "wage_cost")   # 🆕
    unrealized_proj_gross   = unrealized_expected_rev - (unrealized_cogs + unrealized_dlab + unrealized_wage)
    unrealized_totals = {
        "revenue":          unrealized_expected_rev,
        "material_cogs":    unrealized_cogs,
        "digital_lab_cost": unrealized_dlab,
        "wage_cost":        unrealized_wage,                       # 🆕
        "gross_profit":     unrealized_proj_gross,
    }


    def _fmt(dct):
        q2 = Decimal("0.01")
        out = {}
        for k, v in dct.items():
            vv = v if isinstance(v, Decimal) else D(v)
            out[k] = f"{vv.quantize(q2)}"
        return out

    # --- خروجی نهایی بر اساس settlement
    if settlement == "realized":
        payload["orders"] = realized_rows
        t = realized_totals.copy()
        if include_exp:
            t["opex_period"] = opex_period
            t["net_profit"]  = t["gross_profit"] - opex_period
        payload["totals"] = _fmt(t)

    elif settlement == "unrealized":
        payload["orders"] = unrealized_rows
        t = unrealized_totals.copy()
        if include_exp:
            t["opex_period"] = opex_period
            t["net_profit"]  = t["gross_profit"] - opex_period
        payload["totals"] = _fmt(t)
    
    elif settlement == "all":
        # حالت «همه»: همون خروجی سرویس رو بدون هیچ باکت‌بندی یا محاسبهٔ دوباره برگردون
        payload["orders"] = payload.get("orders", [])
        # totals همون مقادیری هست که profit_summary_by_criteria ساخته؛ دست نزن

    else:
        # حالت قدیمی «both» برای سازگاری
        payload["realized"]   = {"totals": _fmt(realized_totals),   "orders": realized_rows}
        payload["unrealized"] = {"totals": _fmt(unrealized_totals), "orders": unrealized_rows}
        both_totals = {
            "revenue":          realized_totals["revenue"] + unrealized_totals["revenue"],
            "material_cogs":    realized_totals["material_cogs"] + unrealized_totals["material_cogs"],
            "digital_lab_cost": realized_totals["digital_lab_cost"] + unrealized_totals["digital_lab_cost"],
            "wage_cost":        realized_totals["wage_cost"] + unrealized_totals["wage_cost"],
            "gross_profit":     realized_totals["gross_profit"] + unrealized_totals["gross_profit"],
        }
        if include_exp:
            both_totals["opex_period"] = opex_period
            both_totals["net_profit"]  = both_totals["gross_profit"] - opex_period
        payload["totals"] = _fmt(both_totals)
        payload["orders"] = realized_rows + unrealized_rows
    meta = {
        "filters": {
            "d_from": d_from_raw,
            "d_to": d_to_raw,
            "doctor": doctor_exact,
            "order_type": order_type,
            "include_expense": include_exp,
            "settlement": settlement,
            "basis": basis,
        }
    }
    return JsonResponse({"ok": True, "meta": meta, "data": payload},
                        json_dumps_params={"ensure_ascii": False})

# --- Backward-compat alias (for older imports/urls) ---
report_profit_summary_api = api_profit_summary


def report_profit_summary_page(request: HttpRequest):
    """
    صفحهٔ HTML گزارش سود و زیان بازه‌ای (با تقویم شمسی و فیلترها).
    این صفحه خودش داده‌ها را نمی‌سازد؛
    فقط UI را نشان می‌دهد و از API همین فایل (report_profit_summary_api) داده می‌گیرد.
    """
    # مسیر API به‌صورت مقاوم (با/بدون namespace)
    try:
        api_url = reverse("report_profit_summary_api")
    except NoReverseMatch:
        try:
            api_url = reverse("billing:report_profit_summary_api")
        except NoReverseMatch:
            api_url = "/billing/api/profit-summary/"

    # ← فهرست واقعی دکترها و محصولات از دیتابیس (برای پر کردن سلکت‌ها در تمپلیت)
    doctor_qs = Doctor.objects.order_by("name").values("id", "name")
    product_qs = Product.objects.filter(is_active=True).order_by("name").values("code", "name")

    context = {
        "api_url": api_url,
        "DOCTOR_CHOICES": list(doctor_qs),
        "PRODUCT_CHOICES": list(product_qs),
        # مقادیر اولیه‌ی فرم (اختیاری)
        "default_jalali_from": "",
        "default_jalali_to": "",
        "default_doctor": "",
        "default_order_type": "",
        "default_include_expense": True,
    }
    return render(request, "billing/report_profit_summary.html", context)
