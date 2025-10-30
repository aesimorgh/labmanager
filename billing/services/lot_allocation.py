from decimal import Decimal
from dataclasses import dataclass
from typing import List, Dict
from django.utils import timezone

from django.db import transaction
from django.core.exceptions import ValidationError

import jdatetime  # برای تبدیل تاریخ میلادی لات به جلالیِ قابل مقایسه با done_date

from billing.models import MaterialLot, StageDefault, StockMovement, StockIssue, _q2, _q3
from core.models import StageInstance


@dataclass
class AllocationResult:
    lot_id: int
    stage_key: str
    shade_code: str
    orders_count: int
    total_units: Decimal
    per_unit_avg: Decimal
    assigned_rows: int
    warnings: List[str]


def _g2j(d):
    """تبدیل تاریخ میلادی (datetime.date) به jdatetime.date برای فیلتر روی jDateField."""
    if d is None:
        return None
    return jdatetime.date.fromgregorian(date=d)


@transaction.atomic
def allocate_lot_usage(lot_id: int) -> Dict:
    """
    بستن لات و تخصیص خودکار مصرف/هزینه به سفارش‌ها بر اساس بازه‌ی مصرف لات.
    منطق:
      - stage_key از روی StageDefaultِ همین متریال استخراج می‌شود (باید دقیقاً یکی باشد).
      - سفارش‌هایی که StageInstance با template.stage_key همان کلید را در بازه‌ی start..end تمام کرده‌اند جمع می‌شوند.
      - میانگین هر واحد = qty_in / مجموعِ واحدها
      - برای هر سفارش: issue (خروج) از همین لات + StockIssue ثبت می‌شود.
      - اختلاف رُندینگِ جزئی به آخرین سفارش داده می‌شود تا جمع دقیقاً برابر qty_in شود.
    خروجی: خلاصه‌ی تخصیص برای UI/اکشن ادمین.
    """
    lot = MaterialLot.objects.select_for_update().select_related('item').get(pk=lot_id)
    # اگر قبلاً قفل شده، اجازهٔ تخصیص دوباره نداریم
    if getattr(lot, "allocated", False):
        raise ValidationError("این لات قبلاً تخصیص یافته است (allocated=True).")


    # پیش‌شرط‌ها
    if not lot.start_use_date or not lot.end_use_date:
        raise ValidationError("برای بستن لات، هر دو تاریخ «آغاز مصرف» و «اتمام مصرف» لازم است.")

    # جلوگیری از دوباره‌تخصیص: اگر قبلاً از این لات خروج به سفارش ثبت شده
    already = StockMovement.objects.filter(lot=lot, movement_type='issue', reason='lot_allocation').exists()
    if already:
        raise ValidationError("این لات قبلاً تخصیص داده شده است.")

    # استخراج کلید مرحله از StageDefault برای همین آیتم
    stage_keys = (StageDefault.objects
                  .filter(material=lot.item, is_active=True)
                  .values_list('stage_key', flat=True)
                  .distinct())
    stage_keys = list(stage_keys)
    if len(stage_keys) == 0:
        raise ValidationError("هیچ «کلید مرحله»ای برای این متریال در StageDefault ثبت نشده است.")
    if len(stage_keys) > 1:
        raise ValidationError("برای این متریال بیش از یک «کلید مرحله» ثبت شده است. لطفاً دقیقاً یک کلید تعیین کنید.")

    stage_key = stage_keys[0]

    # اگر این متریال رنگ‌محور است، فقط سفارش‌های با همان رنگِ لات را در نظر بگیریم
    shade_sensitive = StageDefault.objects.filter(stage_key=stage_key, material=lot.item, is_active=True).values_list('shade_sensitive', flat=True).first() or False
    lot_shade = (lot.shade_code or "").strip()

    # بازه‌ی جلالی برای فیلتر StageInstance.done_date (jDateField)
    start_j = _g2j(lot.start_use_date)
    end_j   = _g2j(lot.end_use_date)

    # انتخاب مرحله‌های انجام‌شده‌ی سفارش‌ها با همین stage_key
    # نکته: StageInstance.key یک اسنپ‌شات مستقل است؛ ما به template.stage_key تکیه می‌کنیم.
    qs = (StageInstance.objects
          .select_related('order', 'template')
          .filter(
              status=StageInstance.Status.DONE,
              template__stage_key=stage_key,
              done_date__gte=start_j,
              done_date__lte=end_j,
          ))

    # اگر رنگ‌محور باشد، سفارش‌ها را بر اساس «shade» محدود کن (در این پروژه فیلد Order.shade داریم)
    if shade_sensitive:
        if not lot_shade:
            raise ValidationError("برای این متریال، تیک «وابسته به رنگ» خورده؛ اما shade_code لات خالی است.")
        qs = qs.filter(order__shade=lot_shade)

    instances: List[StageInstance] = list(qs)

    if not instances:
        raise ValidationError("در بازهٔ مصرف این لات، هیچ سفارشِ مرتبطی پیدا نشد.")

    # جمع واحدهای سفارش‌ها (تعداد واحد کار هر سفارش)
    # فرض قطعی پروژه: فیلد تعداد واحد در Order با نام unit_count موجود است.
    total_units = Decimal('0')
    for inst in instances:
        units = Decimal(str(inst.order.unit_count))  # عدد صحیح/اعشاری؛ به Decimal تبدیل می‌کنیم
        total_units += units

    if total_units <= 0:
        raise ValidationError("جمع واحدها صفر است؛ امکان تخصیص وجود ندارد.")

    # میانگین مصرف هر واحد
    per_unit_avg = _q3(Decimal(lot.qty_in) / total_units)

    assigned_rows = 0
    allocated_sum = Decimal('0.000')

    # تخصیص به هر سفارش
    issues_created = []

    for idx, inst in enumerate(instances):
        order = inst.order
        units = Decimal(str(order.unit_count))
        qty_for_order = _q3(per_unit_avg * units)

        # در آخرین سفارش، اختلاف گرد کردن را تصحیح می‌کنیم تا جمع دقیق equals qty_in شود
        is_last = (idx == len(instances) - 1)
        if is_last:
            diff = _q3(Decimal(lot.qty_in) - (allocated_sum + qty_for_order))
            qty_for_order = _q3(qty_for_order + diff)

        if qty_for_order <= 0:
            continue

        # ایجاد حرکت خروج از همین لات
        move = StockMovement(
            item=lot.item,
            lot=lot,
            movement_type=StockMovement.MoveType.ISSUE,
            qty=_q3(-qty_for_order),  # خروج = منفی
            unit_cost_effective=_q2(lot.unit_cost),  # هزینه مؤثر: قیمت واحدِ همین لات
            happened_at=lot.end_use_date,            # زمان ثبت مصرف: پایان بازه
            order=order,
            product_code=order.product.code if getattr(order, 'product', None) else "",
            reason='lot_allocation',
            created_by='system',
        )
        move.save()

        # ثبت رکورد مصرف برای سفارش
        usage = StockIssue.objects.create(
            order=order,
            item=lot.item,
            qty_issued=_q3(qty_for_order),
            happened_at=lot.end_use_date,
            comment=f"تخصیص از لات {lot.id} ({stage_key})"
        )
        usage.linked_moves.add(move)

        allocated_sum = _q3(allocated_sum + qty_for_order)
        assigned_rows += 1
        issues_created.append(usage.id)

    # پس از تخصیص موفقِ همه‌ی سفارش‌ها، لات را قفل کن
    lot.allocated = True
    lot.allocated_at = timezone.now()
    lot.save(update_fields=['allocated', 'allocated_at'])

    res = AllocationResult(
        lot_id=lot.id,
        stage_key=stage_key,
        shade_code=lot_shade,
        orders_count=len(instances),
        total_units=_q3(total_units),
        per_unit_avg=_q3(per_unit_avg),
        assigned_rows=assigned_rows,
        warnings=[]
    )

    return {
        "ok": True,
        "result": res.__dict__,
        "allocated_qty_sum": str(_q3(allocated_sum)),
        "lot_qty_in": str(_q3(lot.qty_in)),
        "issues": issues_created,
    }

@transaction.atomic
def rollback_lot_allocation(lot_id: int) -> Dict:
    """
    لغو تخصیص خودکار برای یک لات:
      - حذف StockIssueهای مرتبط با حرکات این لات
      - حذف حرکات issueِ «lot_allocation» پس از ثبت یک حرکتِ معکوس (adjust_pos) برای برگرداندن موجودی
      - برداشتن قفل لات (allocated=False, allocated_at=None)
    خروجی: خلاصهٔ عمل برای نمایش در ادمین.
    """
    lot = MaterialLot.objects.select_for_update().select_related('item').get(pk=lot_id)

    # پیدا کردن تمام issueهای کارتکس که از این لات و با reason=lot_allocation ساخته شده‌اند
    issue_moves = list(
        StockMovement.objects.select_for_update()
        .filter(lot=lot, movement_type=StockMovement.MoveType.ISSUE, reason='lot_allocation')
        .order_by('id')
    )
    if not issue_moves:
        return {"ok": False, "msg": "هیچ تخصیص فعالی برای این لات پیدا نشد.", "rolled_back": 0}

    # StockIssue هایی که به این حرکات لینک شده‌اند
    linked_issues = list(
        StockIssue.objects.filter(linked_moves__in=issue_moves).distinct().select_related('order', 'item')
    )

    # 1) برای هر حرکت issue یک حرکت معکوس ثبت می‌کنیم تا موجودی برگردد
    rolled_back_qty = Decimal('0.000')
    for mv in issue_moves:
        qty_abs = -mv.qty if mv.qty < 0 else mv.qty  # خروج = منفی → قدر مطلق
        # حرکت معکوس: adjust_pos (ورود اصلاحی) با همان هزینهٔ مؤثر
        adj = StockMovement(
            item=mv.item,
            lot=lot,  # می‌توان set_null گذاشت؛ نگه‌داشتن lot برای رهگیری بد نیست
            movement_type=StockMovement.MoveType.ADJ_POS,
            qty=_q3(qty_abs),
            unit_cost_effective=_q2(mv.unit_cost_effective),
            happened_at=timezone.now().date(),
            order=None,  # به سفارش ربط نمی‌دهیم تا در COGS شمرده نشود
            reason='rollback_lot_allocation',
            created_by='system',
        )
        adj.save()
        rolled_back_qty = _q3(rolled_back_qty + qty_abs)

    # 2) حذف StockIssueهای مرتبط (M2M هم خودکار پاک می‌شود)
    deleted_issues = len(linked_issues)
    for si in linked_issues:
        si.delete()

    # 3) حذف خود حرکات issue (بعد از ثبت معکوس)
    deleted_moves = 0
    for mv in issue_moves:
        mv.delete()
        deleted_moves += 1

    # 4) آزاد کردن لات
    lot.allocated = False
    lot.allocated_at = None
    lot.save(update_fields=['allocated', 'allocated_at'])

    return {
        "ok": True,
        "rolled_back_qty": str(_q3(rolled_back_qty)),
        "deleted_issue_moves": deleted_moves,
        "deleted_stock_issues": deleted_issues,
        "msg": "تخصیص این لات با موفقیت لغو شد.",
    }

def simulate_lot_allocation(lot_id: int) -> Dict:
    """
    پیشنمایش تخصیص (Dry-Run) برای یک لات:
      - هیچ داده‌ای ذخیره نمی‌شود؛ فقط محاسبه و گزارش برمی‌گردد.
      - خروجی شامل stage_key، شمار سفارش‌ها، مجموع واحدها، میانگینِ هر واحد، و
        جدولِ پیشنهادی تخصیص به تفکیک سفارش (qty و cost هر سفارش) است.
    """
    lot = MaterialLot.objects.select_related('item').get(pk=lot_id)

    if not lot.start_use_date or not lot.end_use_date:
        raise ValidationError("برای پیشنمایش، هر دو تاریخ «آغاز مصرف» و «اتمام مصرف» لازم است.")

    # stage_key همانند allocate_lot_usage
    stage_keys = (StageDefault.objects
                  .filter(material=lot.item, is_active=True)
                  .values_list('stage_key', flat=True)
                  .distinct())
    stage_keys = list(stage_keys)
    if len(stage_keys) == 0:
        raise ValidationError("برای این متریال هیچ «کلید مرحله» در StageDefault ثبت نشده است.")
    if len(stage_keys) > 1:
        raise ValidationError("برای این متریال بیش از یک «کلید مرحله» ثبت شده؛ لطفاً دقیقاً یکی را فعال بگذارید.")
    stage_key = stage_keys[0]

    # رنگ‌محوری
    shade_sensitive = StageDefault.objects.filter(
        stage_key=stage_key, material=lot.item, is_active=True
    ).values_list('shade_sensitive', flat=True).first() or False
    lot_shade = (lot.shade_code or "").strip()
    if shade_sensitive and not lot_shade:
        raise ValidationError("این متریال وابسته به رنگ است، اما shade_code لات خالی است.")

    # بازه‌ی جلالی برای فیلتر StageInstance.done_date
    start_j = _g2j(lot.start_use_date)
    end_j   = _g2j(lot.end_use_date)

    # انتخاب StageInstance های DONE با همین stage_key و در بازه
    qs = (StageInstance.objects
          .select_related('order', 'template')
          .filter(
              status=StageInstance.Status.DONE,
              template__stage_key=stage_key,
              done_date__gte=start_j,
              done_date__lte=end_j,
          ))
    if shade_sensitive:
        qs = qs.filter(order__shade=lot_shade)

    instances = list(qs)
    if not instances:
        return {
            "ok": True,
            "stage_key": stage_key,
            "orders_count": 0,
            "total_units": "0.000",
            "per_unit_avg": "0.000",
            "lot_qty_in": str(_q3(lot.qty_in)),
            "lot_unit_cost": str(_q2(lot.unit_cost)),
            "rows": [],
            "warnings": ["در بازهٔ مصرف، سفارشی پیدا نشد."]
        }

    # مجموع واحدها
    total_units = Decimal('0')
    for inst in instances:
        total_units += Decimal(str(inst.order.unit_count or 0))

    if total_units <= 0:
        raise ValidationError("جمع واحدهای سفارش‌ها صفر است؛ پیشنمایش قابل محاسبه نیست.")

    # میانگین مصرف هر واحد (= مقدار لات / مجموع واحدها)
    per_unit_avg = _q3(Decimal(lot.qty_in) / total_units)

    # آماده‌سازی خروجی ردیف‌ها (بدون save)
    rows = []
    allocated_sum = Decimal('0.000')
    for idx, inst in enumerate(instances):
        order = inst.order
        units = Decimal(str(order.unit_count or 0))
        qty_for_order = _q3(per_unit_avg * units)

        # در Dry-Run هم اختلاف رُندینگ را روی آخرین سفارش اعمال کنیم تا جمع دقیق شود (برای شفافیت گزارش)
        is_last = (idx == len(instances) - 1)
        if is_last:
            diff = _q3(Decimal(lot.qty_in) - (allocated_sum + qty_for_order))
            qty_for_order = _q3(qty_for_order + diff)

        row_cost = _q2(Decimal(qty_for_order) * Decimal(lot.unit_cost))

        rows.append({
            "order_id": order.id,
            "patient": getattr(order, "patient_name", "") or "",
            "doctor": str(getattr(order, "doctor", "") or ""),
            "unit_count": str(units),
            "shade": getattr(order, "shade", "") or "",
            "qty_for_order": str(qty_for_order),
            "row_cost": str(row_cost),
            "happened_at": str(lot.end_use_date),  # همان منطق allocate
        })

        allocated_sum = _q3(allocated_sum + qty_for_order)

    warnings = []
    if getattr(lot, "allocated", False):
        warnings.append("هشدار: این لات قبلاً در وضعیت تخصیص‌یافته است (allocated=True)؛ این فقط پیشنمایش است.")

    return {
        "ok": True,
        "stage_key": stage_key,
        "shade_sensitive": bool(shade_sensitive),
        "shade_filter": lot_shade if shade_sensitive else "",
        "orders_count": len(instances),
        "total_units": str(_q3(total_units)),
        "per_unit_avg": str(_q3(per_unit_avg)),
        "lot_qty_in": str(_q3(lot.qty_in)),
        "lot_unit_cost": str(_q2(lot.unit_cost)),
        "allocated_qty_sum": str(_q3(allocated_sum)),
        "rows": rows,
        "warnings": warnings,
    }
