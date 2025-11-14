# billing/services/pricing_advisor.py
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Optional, List, Dict
from datetime import date

from django.db.models import Q
from core.models import Order
from billing.services.order_pnl import get_order_pnl

QDEC = Decimal

# فلگ سراسری: تا زمانی که منبع قطعی دستمزد را اضافه نکردیم، Labor را صفر نگه می‌داریم
ENABLE_LABOR = True


def _get_order_units(order, pnl: dict) -> QDEC:
    """
    تعداد واحدهای واقعی یک سفارش برای همین product_code.
    اول از خروجی get_order_pnl می‌خوانیم؛ اگر نبود، از فیلدهای رایج Order حدس می‌زنیم.
    هرچه شد، حداقل 1.
    """
    # از خروجی PnL اگر داشت:
    for key in ("units", "unit_count", "order_qty", "quantity"):
        v = pnl.get(key)
        if v is not None:
            try:
                q = QDEC(str(v))
                if q > 0:
                    return q
            except Exception:
                pass

    # از خود مدل Order اگر فیلدی داشت:
    for attr in ("units", "unit_count", "quantity", "count", "tooth_count"):
        v = getattr(order, attr, None)
        if v is not None:
            try:
                q = QDEC(str(v))
                if q > 0:
                    return q
            except Exception:
                pass

    # حداقل 1
    return QDEC("1")


# ـــــــــــ محاسبات بر اساس BOM و میانگین تاریخی لاب دیجیتال ـــــــــــ

def estimate_material_cost_from_bom(product_code: str) -> QDEC:
    """
    محاسبه هزینهٔ مواد استاندارد از جدول BOM:
    جمع qty_standard × unit_cost_effective برای هر ردیف.
    اگر BOM خالی باشد یا خطا دهد، صفر برمی‌گرداند.
    """
    try:
        from core.models import ProductBOM  # مدل BOM استاندارد
    except Exception:
        return QDEC("0")

    try:
        items = ProductBOM.objects.filter(product__code=product_code)
        total = QDEC("0")
        for it in items:
            qty = QDEC(str(getattr(it, "qty_standard", 0) or 0))
            unit_cost = QDEC(str(getattr(it, "unit_cost_effective", 0) or 0))
            total += qty * unit_cost
        return total
    except Exception:
        return QDEC("0")


def estimate_digital_from_history(product_code: str, days: int = 90) -> QDEC:
    """
    میانگین هزینهٔ لاب دیجیتال در N روز اخیر برای همین محصول.
    اگر داده کم باشد، صفر برمی‌گرداند.
    """
    try:
        from datetime import timedelta, date
        from core.models import Order
        from billing.services.order_pnl import get_order_pnl
    except Exception:
        return QDEC("0")

    since = date.today() - timedelta(days=days)
    qs = Order.objects.filter(order_type=product_code, created_at__date__gte=since)
    total = QDEC("0"); n = 0
    for o in qs.only("id"):
        pnl = get_order_pnl(o.id)
        val = QDEC(str(pnl.get("digital_lab_cost", 0) or 0))
        total += val
        n += 1
    return (total / n) if n else QDEC("0")


def estimate_labor_cost(product_code: str) -> QDEC:
    """
    تخمین هزینهٔ دستمزد برای هر واحد از محصول.
    اگر در مدل Product فیلد labor_cost موجود باشد، از آن استفاده می‌کنیم؛
    در غیر این‌صورت مقدار پیش‌فرض 200000 تومان (برای هر واحد) در نظر گرفته می‌شود.
    """
    try:
        from core.models import Product
    except Exception:
        return QDEC("200000")

    try:
        prod = Product.objects.filter(code=product_code).first()
        if not prod:
            return QDEC("200000")
        val = getattr(prod, "labor_cost", None)
        if val is None:
            return QDEC("200000")
        return QDEC(str(val))
    except Exception:
        return QDEC("200000")


def _r(x: Decimal, places: int = 2) -> Decimal:
    if x is None:
        x = QDEC("0")
    q = QDEC(10) ** -places
    return x.quantize(q, rounding=ROUND_HALF_EVEN)


@dataclass
class ProductCostRow:
    order_id: int
    revenue: Decimal
    material_cogs: Decimal
    digital_lab_cost: Decimal
    gross_profit: Decimal
    net_profit: Decimal


@dataclass
class ProductCostSummary:
    product_code: str
    n_orders: int
    avg_revenue: Decimal
    avg_material: Decimal
    avg_dlab: Decimal
    avg_labor: Decimal                 # = هزینهٔ دستمزد (فعلاً صفر چون ENABLE_LABOR=False)
    avg_total_cost: Decimal            # = material + dlab (+ labor اگر فعال شود)
    avg_gross_profit: Decimal
    avg_net_profit: Decimal
    suggested_price_margin: Decimal
    suggested_price_markup: Decimal
    rows: List[ProductCostRow]

    def to_dict(self) -> Dict:
        return {
            "product_code": self.product_code,
            "n_orders": self.n_orders,
            "avg_revenue": str(_r(self.avg_revenue)),
            "avg_material": str(_r(self.avg_material)),
            "avg_dlab": str(_r(self.avg_dlab)),
            "avg_labor": str(_r(self.avg_labor)),
            "avg_total_cost": str(_r(self.avg_total_cost)),
            "avg_gross_profit": str(_r(self.avg_gross_profit)),
            "avg_net_profit": str(_r(self.avg_net_profit)),
            "suggested_price_margin": str(_r(self.suggested_price_margin)),
            "suggested_price_markup": str(_r(self.suggested_price_markup)),
            "rows": [
                {
                    "order_id": r.order_id,
                    "revenue": str(_r(r.revenue)),
                    "material_cogs": str(_r(r.material_cogs)),
                    "digital_lab_cost": str(_r(r.digital_lab_cost)),
                    "gross_profit": str(_r(r.gross_profit)),
                    "net_profit": str(_r(r.net_profit)),
                }
                for r in self.rows
            ],
        }


def compute_product_pricing_summary(
    *,
    product_code: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    include_open: bool = True,       # سفارش‌های تحویل‌نشده/درحال انجام را هم لحاظ کند
    min_orders: int = 3,
    target_margin_pct: Optional[Decimal] = None,  # مثل 0.6 یعنی 60% مارژین روی قیمت
    markup_multiplier: Optional[Decimal] = None,  # مثل 2.2 یعنی 2.2× نسبت به «هزینه کل»
    rounding_step: Decimal = QDEC("10000"),       # پله‌ی رُند کردن (تومان)
    method: str = "history",                      # "history" | "bom" | "hybrid"
) -> ProductCostSummary:
    """
    - ورودی اصلی: product_code (همان Order.order_type)
    - فیلتر بازه‌ی تاریخی روی order_date (یا در نبود آن، created_at__date)
    - محاسبه میانگین‌ها «به‌ازای هر واحد» بر اساس PnL واقعی هر سفارش (get_order_pnl)
    - «هزینه کل واحد» = material + digital (+ labor اگر ENABLE_LABOR=True)
    """
    qs = Order.objects.filter(order_type=product_code)
    if date_from:
        try:
            qs = qs.filter(order_date__gte=date_from)
        except Exception:
            qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        try:
            qs = qs.filter(order_date__lte=date_to)
        except Exception:
            qs = qs.filter(created_at__date__lte=date_to)

    if not include_open:
        qs = qs.filter(Q(status="delivered") | Q(shipped_date__isnull=False))

    orders = list(qs.only("id"))
    rows: List[ProductCostRow] = []
    if not orders:
        return ProductCostSummary(
            product_code=product_code,
            n_orders=0,
            avg_revenue=QDEC("0"),
            avg_material=QDEC("0"),
            avg_dlab=QDEC("0"),
            avg_labor=QDEC("0"),
            avg_total_cost=QDEC("0"),
            avg_gross_profit=QDEC("0"),
            avg_net_profit=QDEC("0"),
            suggested_price_margin=QDEC("0"),
            suggested_price_markup=QDEC("0"),
            rows=[],
        )

    # جمع‌های «پرواحد»
    rev_s_u = QDEC("0"); mat_s_u = QDEC("0"); dlab_s_u = QDEC("0"); labor_s_u = QDEC("0"); g_s_u = QDEC("0"); n_s_u = QDEC("0")
    units_s = QDEC("0")

    for order in orders:
        pnl = get_order_pnl(order.id)

        units = _get_order_units(order, pnl)
        if units <= 0:
            units = QDEC("1")

        # اعداد «به‌ازای هر واحد»
        rev_u  = QDEC(str(pnl["revenue"]))          / units
        mat_u  = QDEC(str(pnl["material_cogs"]))    / units
        dlab_u = QDEC(str(pnl["digital_lab_cost"])) / units
        g_u    = QDEC(str(pnl["gross_profit"]))     / units
        n_u    = QDEC(str(pnl["net_profit"]))       / units

        # دستمزد (فعلاً صفر اگر ENABLE_LABOR=False)
        if "labor_cost" in pnl:
            labor_u_calc = QDEC(str(pnl.get("labor_cost") or 0)) / units
        else:
            labor_u_calc = estimate_labor_cost(product_code)
        labor_u = labor_u_calc if ENABLE_LABOR else QDEC("0")

        rows.append(ProductCostRow(
            order_id=order.id,
            revenue=rev_u,
            material_cogs=mat_u,
            digital_lab_cost=dlab_u,
            gross_profit=g_u,
            net_profit=n_u,
        ))

        rev_s_u  += rev_u
        mat_s_u  += mat_u
        dlab_s_u += dlab_u
        labor_s_u += labor_u
        g_s_u    += g_u
        n_s_u    += n_u
        units_s  += units

    n = len(rows)

    # میانگین‌ها «به‌ازای هر واحد»
    avg_rev   = (rev_s_u / n) if n else QDEC("0")
    avg_mat   = (mat_s_u / n) if n else QDEC("0")
    avg_dlab  = (dlab_s_u / n) if n else QDEC("0")
    avg_labor = (labor_s_u / n) if n else QDEC("0")
    avg_cost  = avg_mat + avg_dlab + (avg_labor if ENABLE_LABOR else QDEC("0"))
    avg_g     = (g_s_u / n) if n else QDEC("0")
    avg_n     = (n_s_u / n) if n else QDEC("0")

    # ـــــــــــ حالت‌های روش محاسبه (history / bom / hybrid) ـــــــــــ
    if method.lower() == "bom":
        avg_mat = estimate_material_cost_from_bom(product_code)
        avg_dlab = QDEC("0")
        avg_labor = estimate_labor_cost(product_code) if ENABLE_LABOR else QDEC("0")
        avg_cost = avg_mat + avg_dlab + (avg_labor if ENABLE_LABOR else QDEC("0"))
        avg_rev = avg_cost
        avg_g = avg_rev - avg_mat
        avg_n = avg_rev - avg_cost

    elif method.lower() == "hybrid":
        avg_mat = estimate_material_cost_from_bom(product_code)
        avg_dlab = estimate_digital_from_history(product_code)
        avg_labor = estimate_labor_cost(product_code) if ENABLE_LABOR else QDEC("0")
        avg_cost = avg_mat + avg_dlab + (avg_labor if ENABLE_LABOR else QDEC("0"))
        avg_rev = avg_cost
        avg_g = avg_rev - avg_mat
        avg_n = avg_rev - avg_cost

    def _round_up_step(x: Decimal, step: Decimal) -> Decimal:
        if step <= 0:
            return x
        k = (x / step).quantize(QDEC("1"), rounding=ROUND_HALF_EVEN)
        if k * step < x:
            k += 1
        return k * step

    # قیمت پیشنهادی بر اساس target_margin
    suggested_by_margin = QDEC("0")
    if target_margin_pct is not None:
        m = QDEC(str(target_margin_pct))
        if m >= QDEC("0") and m < QDEC("1"):
            denom = (QDEC("1") - m)
            if denom > 0:
                suggested_by_margin = avg_cost / denom

    # قیمت پیشنهادی بر اساس markup multiplier
    suggested_by_markup = QDEC("0")
    if markup_multiplier is not None:
        mu = QDEC(str(markup_multiplier))
        if mu > 0:
            suggested_by_markup = avg_cost * mu

    # رند کردن به پله
    suggested_by_margin = _round_up_step(suggested_by_margin, rounding_step)
    suggested_by_markup = _round_up_step(suggested_by_markup, rounding_step)

    return ProductCostSummary(
        product_code=product_code,
        n_orders=n,
        avg_revenue=avg_rev,
        avg_material=avg_mat,
        avg_dlab=avg_dlab,
        avg_labor=avg_labor,             # ← حتماً پاس داده شود
        avg_total_cost=avg_cost,
        avg_gross_profit=avg_g,
        avg_net_profit=avg_n,
        suggested_price_margin=suggested_by_margin,
        suggested_price_markup=suggested_by_markup,
        rows=rows[: min(n, 25)],
    )
