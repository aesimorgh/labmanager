# billing/services/order_pnl.py
from decimal import Decimal, ROUND_HALF_EVEN
from django.db.models import Sum, F, DecimalField, ExpressionWrapper, Q
from django.utils import timezone
from django.db.models.functions import Coalesce  # ← بالای فایل اگر نداری، اضافه کن
# تلاش برای ایمپورت مدل‌ها با اتکا به ساختار معرفی‌شده در مستند شما
from billing.models import StockIssue  # مصرف متریال هر سفارش
# اگر AllocationLine دارید و می‌خواهید سهم غیرمستقیم هم لحاظ شود، ایمپورت زیر را باز کنید:
# from billing.models import AllocationLine
from core.models import Order, DigitalLabTransfer  # هزینه‌های لاب دیجیتال مرتبط با سفارش
# دستمزد مراحل (اگر بعداً مدل اضافه شود، این ایمپورت امن است)
try:
    from core.models import StageWorkLog  # مدل لاگ دستمزد هر مرحله
except Exception:
    StageWorkLog = None

from billing.models import Invoice, InvoiceLine  # درآمد سفارش از فاکتورها

QDEC = Decimal  # نام کوتاه برای خوانایی

def _bankers_round(x: Decimal, places: int = 2) -> Decimal:
    if x is None:
        x = QDEC('0')
    q = QDEC(10) ** -places
    return x.quantize(q, rounding=ROUND_HALF_EVEN)

def get_order_pnl(order_id: int) -> dict:
    """
    خروجی:
    {
      'revenue': Decimal,
      'material_cogs': Decimal,
      'digital_lab_cost': Decimal,
      'allocation_share': Decimal,
      'gross_profit': Decimal,   # revenue - material_cogs
      'net_profit': Decimal,     # revenue - (material_cogs + digital_lab_cost + allocation_share)
    }
    """

    # 1) درآمد سفارش (جمع line_total رکوردهای مرتبط با این سفارش)
    from django.db.models.functions import Coalesce

    revenue_qs = (
        InvoiceLine.objects
        .filter(order_id=order_id)
        .aggregate(total=Coalesce(
            Sum('line_total', output_field=DecimalField(max_digits=18, decimal_places=2)),
            Decimal('0.00'),
            output_field=DecimalField(max_digits=18, decimal_places=2)
        ))
    )
    revenue = revenue_qs['total'] or QDEC('0')
    
    # --- Fallback: اگر هنوز فاکتور ندارد، درآمد مورد انتظار = price * unit_count از خود Order
    if revenue == QDEC('0'):
        try:
            o = Order.objects.only('price', 'unit_count').get(id=order_id)
            unit_count = (o.unit_count or 1)
            price = QDEC(str(o.price or '0'))
            revenue = price * QDEC(unit_count)
        except Order.DoesNotExist:
            pass


    # 2) COGS متریال: جمع (qty × unit_cost_effective) روی حرکت‌های پیوندخورده با StockIssueهای این سفارش
    material_qs = (
        StockIssue.objects
        .filter(order_id=order_id)
        .annotate(_row_cost=ExpressionWrapper(
            Coalesce(F('linked_moves__qty'), QDEC('0')) * Coalesce(F('linked_moves__unit_cost_effective'), QDEC('0')),
            output_field=DecimalField(max_digits=18, decimal_places=6)
        ))
        .aggregate(total=Coalesce(
            Sum('_row_cost', output_field=DecimalField(max_digits=18, decimal_places=2)),
            QDEC('0.00'),
            output_field=DecimalField(max_digits=18, decimal_places=2)
        ))
    )
    material_cogs = material_qs['total'] or QDEC('0')


    # 3) هزینه لاب دیجیتال: جمع charge - credit رکوردهای مرتبط با سفارش
    dl_qs = (
        DigitalLabTransfer.objects
        .filter(order_id=order_id)
        .annotate(_net=ExpressionWrapper(
            Coalesce(F('charge_amount'), QDEC('0')) - Coalesce(F('credit_amount'), QDEC('0')),
            output_field=DecimalField(max_digits=18, decimal_places=2)
        ))
        .aggregate(total=Coalesce(
            Sum('_net', output_field=DecimalField(max_digits=18, decimal_places=2)),
            QDEC('0.00'),
            output_field=DecimalField(max_digits=18, decimal_places=2)
        ))
    )
    digital_lab_cost = dl_qs['total'] or QDEC('0')

    # 3.1) هزینه دستمزد مراحل (جمع total_wage لاگ‌های انجام‌شده) — اختیاری
    labor_cost = QDEC('0')
    if StageWorkLog is not None:
        try:
            labor_qs = (
                StageWorkLog.objects
                .filter(order_id=order_id, status='done')
                .aggregate(total=Coalesce(
                    Sum('total_wage', output_field=DecimalField(max_digits=18, decimal_places=2)),
                    QDEC('0.00'),
                    output_field=DecimalField(max_digits=18, decimal_places=2)
                ))
            )
            labor_cost = labor_qs['total'] or QDEC('0')
        except Exception:
            labor_cost = QDEC('0')


    # 4) سهم تخصیص غیرمستقیم (اختیاری)
    allocation_share = QDEC('0')
    # اگر AllocationLine دارید و می‌خواهید لحاظ شود، دو خط زیر را از حالت کامنت خارج کنید:
    # alloc_qs = AllocationLine.objects.filter(order_id=order_id).aggregate(
    #     total=Sum('amount', output_field=DecimalField(max_digits=18, decimal_places=2)))
    # allocation_share = alloc_qs['total'] or QDEC('0')

    gross_profit = revenue - material_cogs
    net_profit = revenue - (material_cogs + digital_lab_cost + allocation_share + labor_cost)


    # گرد کردن بانکی برای نمایش و ثبات عددی
    result = {
        'revenue': _bankers_round(revenue),
        'material_cogs': _bankers_round(material_cogs),
        'digital_lab_cost': _bankers_round(digital_lab_cost),
        'allocation_share': _bankers_round(allocation_share),
        'labor_cost': _bankers_round(labor_cost),  # 🆕 دستمزد مراحل
        'gross_profit': _bankers_round(gross_profit),
        'net_profit': _bankers_round(net_profit),
    }
    return result
