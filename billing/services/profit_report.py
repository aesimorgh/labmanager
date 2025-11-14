# billing/services/profit_report.py
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Iterable, List, Dict, Any, Optional
from datetime import date

from django.db import transaction
from django.db.models import Sum, DecimalField

from billing.services.order_pnl import get_order_pnl
from core.models import Order, StageWorkLog
from billing.models import Expense  # هزینه‌های دوره (اجاره/قبوض/پیک/...)


# بالای فایل، کنار سایر importها
try:
    from billing.models import DigitalLabTransfer
except Exception:
    DigitalLabTransfer = None


# گردکردن بانکی
def _r(x: Decimal, places: int = 2) -> Decimal:
    if x is None:
        x = Decimal("0")
    q = Decimal(10) ** -places
    return x.quantize(q, rounding=ROUND_HALF_EVEN)


@dataclass
class OrderRow:
    order_id: int
    doctor_name: str | None
    product_code: str | None
    revenue: Decimal
    material_cogs: Decimal
    digital_lab_cost: Decimal
    wage_cost: Decimal           # 🆕 هزینه دستمزد این سفارش
    allocation_share: Decimal
    gross_profit: Decimal
    net_profit: Decimal


@dataclass
class ProfitSummary:
    # جمع کل‌ها
    revenue_total: Decimal
    material_cogs_total: Decimal
    digital_lab_cost_total: Decimal
    wage_cost_total: Decimal          # 🆕 جمع کل دستمزد
    allocation_total: Decimal
    opex_period_total: Decimal        # هزینه‌های دوره (Expense)
    gross_profit_total: Decimal
    net_profit_total: Decimal
    # ریز سفارش‌ها
    orders: List[OrderRow]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "totals": {
                "revenue": _r(self.revenue_total),
                "material_cogs": _r(self.material_cogs_total),
                "digital_lab_cost": _r(self.digital_lab_cost_total),
                "wage_cost": _r(self.wage_cost_total),          # 🆕
                "allocation": _r(self.allocation_total),
                "opex_period": _r(self.opex_period_total),
                "gross_profit": _r(self.gross_profit_total),
                "net_profit": _r(self.net_profit_total),
            },
            "orders": [
                {
                    "order_id": row.order_id,
                    "doctor_name": row.doctor_name,
                    "product_code": row.product_code,
                    "revenue": _r(row.revenue),
                    "material_cogs": _r(row.material_cogs),
                    "digital_lab_cost": _r(row.digital_lab_cost),
                    "wage_cost": _r(row.wage_cost),              # 🆕
                    "allocation_share": _r(row.allocation_share),
                    "gross_profit": _r(row.gross_profit),
                    "net_profit": _r(row.net_profit),
                }
                for row in self.orders
            ],
        }


@transaction.atomic
def profit_summary_for_orders(
    order_ids: Iterable[int],
    *,
    include_period_expense: bool = True,
    expense_date_from=None,
    expense_date_to=None
) -> ProfitSummary:
    """
    گزارش سود و زیان برای یک مجموعه سفارش مشخص.
    - ورودی: لیست order_id
    - خروجی: جمع کل‌ها + ریز هر سفارش
    - نکته: هزینه‌های دوره (Expense) و دستمزد (StageWorkLog) را، اگر بخواهی،
      در بازهٔ مشخص جمع می‌زنیم.
    """
    ids = [int(x) for x in set(order_ids)]
    if not ids:
        return ProfitSummary(
            revenue_total=Decimal("0"),
            material_cogs_total=Decimal("0"),
            digital_lab_cost_total=Decimal("0"),
            wage_cost_total=Decimal("0"),
            allocation_total=Decimal("0"),
            opex_period_total=Decimal("0"),
            gross_profit_total=Decimal("0"),
            net_profit_total=Decimal("0"),
            orders=[]
        )

    # --- دستمزد هر سفارش بر اساس StageWorkLog ---
    wage_qs = StageWorkLog.objects.filter(
        order_id__in=ids,
        status=StageWorkLog.Status.DONE,
    )
    # از همان بازهٔ زمانی هزینه‌ها برای فیلتر دستمزد استفاده می‌کنیم
    if expense_date_from:
        wage_qs = wage_qs.filter(finished_at__gte=expense_date_from)
    if expense_date_to:
        wage_qs = wage_qs.filter(finished_at__lte=expense_date_to)

    wage_agg = wage_qs.values("order_id").annotate(
        total=Sum('total_wage', output_field=DecimalField(max_digits=18, decimal_places=2))
    )
    wage_map: Dict[int, Decimal] = {
        row["order_id"]: (row["total"] or Decimal("0"))
        for row in wage_agg
    }

    # اطلاعات نمایشی پایه سفارش‌ها
    orders = Order.objects.filter(id__in=ids)  # عمداً ساده نگه داشته شده

    rows: List[OrderRow] = []
    rev_sum = mat_sum = dl_sum = wage_sum = alloc_sum = gp_sum = np_sum = Decimal("0")

    for o in orders:
        pnl = get_order_pnl(o.id)

        wage_cost = wage_map.get(o.id, Decimal("0"))

        row = OrderRow(
            order_id=o.id,
            doctor_name=getattr(o, "doctor", None),        # CharField از Order
            product_code=getattr(o, "order_type", None),   # نوع سفارش از Order
            revenue=pnl["revenue"],
            material_cogs=pnl["material_cogs"],
            digital_lab_cost=pnl["digital_lab_cost"],
            wage_cost=wage_cost,
            allocation_share=pnl["allocation_share"],
            gross_profit=pnl["gross_profit"],
            net_profit=pnl["net_profit"],
        )
        rows.append(row)

        rev_sum += row.revenue
        mat_sum += row.material_cogs
        dl_sum  += row.digital_lab_cost
        wage_sum += row.wage_cost
        alloc_sum += row.allocation_share
        gp_sum += row.gross_profit
        np_sum += row.net_profit

    # هزینه‌های دوره (Expense): اجاره/قبض/پیک/…
    opex_total = Decimal("0")
    if include_period_expense:
        exp_qs = Expense.objects.all()
        if expense_date_from:
            exp_qs = exp_qs.filter(date__gte=expense_date_from)
        if expense_date_to:
            exp_qs = exp_qs.filter(date__lte=expense_date_to)
        opex_total = exp_qs.aggregate(
            total=Sum('amount', output_field=DecimalField(max_digits=18, decimal_places=2))
        )['total'] or Decimal("0")

    # جمع نهایی:
    # سود ناخالص = درآمد − (مواد + لاب دیجیتال + دستمزد)
    gross_total = rev_sum - (mat_sum + dl_sum + wage_sum)
    # سود نهایی = سود ناخالص − (allocation + هزینه‌های دوره)
    net_total = gross_total - (alloc_sum + opex_total)

    return ProfitSummary(
        revenue_total=rev_sum,
        material_cogs_total=mat_sum,
        digital_lab_cost_total=dl_sum,
        wage_cost_total=wage_sum,
        allocation_total=alloc_sum,
        opex_period_total=opex_total,
        gross_profit_total=gross_total,
        net_profit_total=net_total,
        orders=rows,
    )


def profit_summary_by_criteria(
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    doctor_exact: Optional[str] = None,
    order_type_exact: Optional[str] = None,
    include_period_expense: bool = True
) -> ProfitSummary:
    """
    گزارش سود/زیان براساس فیلترهای قطعی:
      - تاریخ سفارش: order_date (در صورت نبود، created_at)
      - نام دکتر: تطابق دقیق روی Order.doctor (CharField)
      - نوع سفارش: تطابق دقیق روی Order.order_type
    خروجی: همان ProfitSummary (جمع کل + ریز سفارش‌ها)
    """
    qs = Order.objects.all()

    # تاریخ: اگر order_date دارید، روی آن فیلتر می‌کنیم؛ در غیر اینصورت fallback به created_at
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

    if doctor_exact:
        qs = qs.filter(doctor=doctor_exact)

    if order_type_exact:
        qs = qs.filter(order_type=order_type_exact)

    ids = list(qs.values_list('id', flat=True))

    # همان بازه را برای هزینه‌های دوره و دستمزد هم استفاده می‌کنیم
    expense_from = date_from
    expense_to   = date_to

    return profit_summary_for_orders(
        ids,
        include_period_expense=include_period_expense,
        expense_date_from=expense_from,
        expense_date_to=expense_to
    )
