from datetime import date
import io

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from urllib.parse import urlencode
from django.db.models import Q, F, Value, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
import xlsxwriter
from weasyprint import HTML

try:
    import jdatetime
except ImportError:
    jdatetime = None

from .forms import OrderForm, OrderEventForm
from .models import Order, OrderEvent, Doctor
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.db import transaction
import datetime
from .models import Product, StageTemplate, StageInstance
from django.db.models import Q, Sum
from django.views.decorators.clickjacking import xframe_options_exempt
from .models import StageWorkLog

# --- Helpers for seeding stages ---------------------------------------------
def _jalali_add_days(jdate, days: int):
    """
    به تاریخ جلالی days روز اضافه می‌کند.
    اگر jdatetime در دسترس نباشد، None برمی‌گرداند (برای جلوگیری از خطا).
    """
    # اگر jdatetime موجود نباشد، فعلاً از seeding صرف‌نظر می‌کنیم
    try:
        import jdatetime as _jd
    except Exception:
        return None

    if isinstance(jdate, _jd.date):
        g = jdate.togregorian()
    else:
        # اگر جلالی نبود (یا None)، امروزِ جلالی را مبنا بگیر
        base = _jd.date.today()
        g = base.togregorian()

    g2 = g + datetime.timedelta(days=days)
    return _jd.date.fromgregorian(date=g2)


def seed_order_stages(order):
    """
    اگر برای سفارش StageInstance وجود ندارد، از روی StageTemplateهای محصول مرتبط می‌سازد.
    نگاشت: Product.code == order.order_type
    """
    code = (order.order_type or "").strip()
    if not code:
        return

    # جلوگیری از تکرار
    if StageInstance.objects.filter(order=order).exists():
        return

    try:
        product = Product.objects.get(code=code, is_active=True)
    except Product.DoesNotExist:
        return

    templates = (
        StageTemplate.objects
        .filter(product=product, is_active=True)
        .order_by('order_index')
    )
    if not templates.exists():
        return

    # مبنا: تاریخ سفارش، وگرنه امروزِ جلالی
    try:
        import jdatetime as _jd
        base = order.order_date or _jd.date.today()
    except Exception:
        base = None  # اگر jdatetime نبود، seeding را بی‌خطر رد می‌کنیم

    day_acc = 0
    instances = []
    for t in templates:
        dur = int(t.default_duration_days or 0)
        planned = _jalali_add_days(base, day_acc + dur)
        instances.append(StageInstance(
            order=order,
            template=t,
            key=t.key,
            label=t.label,
            order_index=t.order_index,
            planned_date=planned,  # ممکن است None شود اگر jdatetime نباشد
            status=StageInstance.Status.PENDING,
        ))
        day_acc += dur

    StageInstance.objects.bulk_create(instances)
# ---------------------------------------------------------------------------


# ============================
# Helpers (Jalali normalizers)
# ============================
def _normalize_digits(s: str) -> str:
    if not s:
        return ""
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return s.translate(trans).strip()

def _normalize_for_jalali_field(s: str) -> str:
    # "۱۴۰۴/۰۶/۲۵" → "1404-06-25" (فرمت متنی مناسب jDateField)
    s = _normalize_digits(s)
    return s.replace("/", "-")

def _jalali_to_gregorian_date(s: str):
    """
    '۱۴۰۴/۰۶/۱۹' یا '1404/06/19' → datetime.date (میلادی)
    اگر خالی/نامعتبر بود: None
    """
    if not s:
        return None
    s = _normalize_digits(s).replace("-", "/")
    if not jdatetime:
        return None
    try:
        jy, jm, jd = [int(x) for x in s.split("/")]
        g = jdatetime.date(jy, jm, jd).togregorian()
        return date(g.year, g.month, g.day)
    except Exception:
        return None

# ——— Today in Jalali (fallback به میلادی اگر jdatetime نبود) ———
def _today_jdate():
    try:
        # اگر بالای فایل jdatetime را با try/except ایمپورت کرده‌ای، از همان استفاده می‌کنیم
        if jdatetime is not None:
            return jdatetime.date.today()  # مثل 1404-07-23
    except NameError:
        pass
    from datetime import date
    return date.today()  # fallback: 2025-10-15


# ============================
# صفحه اصلی / ثبت سفارش
# ============================
def home(request):
    # فرم ثبت سفارش
    order_form = OrderForm(request.POST or None, prefix='order')
    if request.method == "POST":
        if order_form.is_valid():
            with transaction.atomic():
                order = order_form.save()
                seed_order_stages(order)
            return redirect('core:home')


    # فیلترها
    q       = (request.GET.get('q') or '').strip()
    doctor  = (request.GET.get('doctor') or '').strip()
    status  = (request.GET.get('status') or '').strip()

    orders_qs = Order._base_manager.select_related('patient').all()

    if doctor:
        orders_qs = orders_qs.filter(doctor__icontains=doctor)
    if status:
        orders_qs = orders_qs.filter(status=status)
    if q:
        orders_qs = orders_qs.filter(
            Q(patient__name__icontains=q) |
            Q(doctor__icontains=q) |
            Q(serial_number__icontains=q) |
            Q(shade__icontains=q)
        )

    # مرتب‌سازی (created_at / due_date / total_price)
    sort = (request.GET.get('sort') or '').strip()
    direction = (request.GET.get('dir') or '').strip().lower()  # asc / desc

    valid_sorts = {'created_at': 'created_at', 'due_date': 'due_date', 'total_price': 'total_price'}

    if sort in valid_sorts:
        if sort == 'total_price':
            # total_price محاسباتی است؛ annotate با خروجی DecimalField تا خطای mixed types رفع شود
            orders_qs = orders_qs.annotate(
                total_price_calc=ExpressionWrapper(
                    F('unit_count') * Coalesce(
                        F('price'),
                        Value(0, output_field=DecimalField(max_digits=20, decimal_places=2))
                    ),
                    output_field=DecimalField(max_digits=20, decimal_places=2)
                )
            )
            order_field = 'total_price_calc'
        else:
            order_field = valid_sorts[sort]

        if direction == 'asc':
            orders_qs = orders_qs.order_by(order_field)
        else:
            orders_qs = orders_qs.order_by('-' + order_field)
    else:
        # پیش‌فرض
        orders_qs = orders_qs.order_by('-id')

    # صفحه‌بندی
    paginator = Paginator(orders_qs, 25)
    page_number = request.GET.get('page')
    orders_page = paginator.get_page(page_number)

    # لیست پزشک‌ها برای فیلتر
    doctors = (Order._base_manager
               .exclude(doctor__isnull=True).exclude(doctor='')
               .values_list('doctor', flat=True)
               .distinct()
               .order_by('doctor'))

    context = {
        'order_form': order_form,
        'orders': orders_page,
        'page_obj': orders_page,

        # Echo فیلترها و مرتب‌سازی برای تمپلیت
        'q': q,
        'doctor': doctor,
        'status': status,
        'doctors': doctors,
        'status_choices': Order.STATUS_CHOICES,
        'sort': sort,
        'dir': direction or 'desc',
    }
    return render(request, 'core/home.html', context)


# ============================
# گزارش مالی / حسابداری
# ============================
def accounting_report(request):
    doctor    = (request.GET.get('doctor') or '').strip()
    start_raw = (request.GET.get('start_date') or '').strip()  # مثل "۱۴۰۴/۰۶/۱۹"
    end_raw   = (request.GET.get('end_date') or '').strip()    # مثل "۱۴۰۴/۰۷/۰۵"

    # برای due_date (jDateField): جلالی نرمال با خط‌تیره (رشته)
    start_j = _normalize_for_jalali_field(start_raw)  # "1404-06-19" یا ""
    end_j   = _normalize_for_jalali_field(end_raw)    # "1404-07-05" یا ""

    # برای created_at__date (میلادی): تبدیل جلالی → میلادی
    start_g = _jalali_to_gregorian_date(start_raw)    # datetime.date یا None
    end_g   = _jalali_to_gregorian_date(end_raw)      # datetime.date یا None

    orders = Order._base_manager.all().order_by('-id')

    if doctor:
        orders = orders.filter(doctor__icontains=doctor)

    # فیلتر تاریخ (OR بین due_date و created_at__date)
    if start_j and end_j and start_g and end_g:
        orders = orders.filter(
            Q(due_date__range=[start_j, end_j]) |
            Q(created_at__date__range=[start_g, end_g])
        )
    elif start_j and start_g:
        orders = orders.filter(
            Q(due_date__gte=start_j) |
            Q(created_at__date__gte=start_g)
        )
    elif end_j and end_g:
        orders = orders.filter(
            Q(due_date__lte=end_j) |
            Q(created_at__date__lte=end_g)
        )

    total_invoice = sum((o.total_price or 0) for o in orders)

    doctors = (Order._base_manager
               .exclude(doctor__isnull=True).exclude(doctor='')
               .values_list('doctor', flat=True).distinct().order_by('doctor'))

    context = {
        'orders': orders,
        'total_invoice': total_invoice,
        'doctor': doctor,
        'start_date': start_raw,
        'end_date': end_raw,
        'doctors': doctors,
    }

    # --- ساخت لینک «صدور فاکتور از این بازه» ---
    query = {}
    if doctor:
        query['doctor'] = doctor
    if start_raw:
        query['period_from'] = start_raw
    if end_raw:
        query['period_to'] = end_raw

    invoice_url = reverse('billing:invoice_create_draft')
    if query:
        invoice_url = f"{invoice_url}?{urlencode(query)}"

    context['invoice_url'] = invoice_url

    # خروجی Excel
    if 'export_excel' in request.GET:
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet("گزارش مالی")

        headers = ['ID', 'بیمار', 'پزشک', 'نوع سفارش', 'تعداد واحد', 'قیمت واحد',
                   'قیمت کل', 'تاریخ تحویل', 'تاریخ ثبت']
        for c, h in enumerate(headers):
            ws.write(0, c, h)

        for r, o in enumerate(orders, start=1):
            ws.write(r, 0, o.id)
            ws.write(r, 1, o.patient_name)
            ws.write(r, 2, o.doctor)
            ws.write(r, 3, o.get_order_type_display())
            ws.write(r, 4, o.unit_count)
            ws.write(r, 5, float(o.price or 0))
            ws.write(r, 6, float(o.total_price or 0))
            ws.write(r, 7, str(o.due_date))
            ws.write(r, 8, o.created_at.strftime("%Y/%m/%d"))

        wb.close()
        output.seek(0)
        resp = HttpResponse(
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        resp['Content-Disposition'] = 'attachment; filename=accounting_report.xlsx'
        return resp

    # خروجی PDF
    if 'export_pdf' in request.GET:
        html = render_to_string('core/accounting_report_pdf.html', context)
        pdf = HTML(string=html).write_pdf()
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = 'attachment; filename=accounting_report.pdf'
        return resp

    return render(request, 'core/accounting_report.html', context)


# ============================
# Order detail + timeline
# ============================
def order_detail(request, order_id):
    from decimal import Decimal  # برای محاسبهٔ مبلغ مصرف خودکار
    from billing.services.order_pnl import get_order_pnl  # محاسبهٔ درآمد/COGS/سود سفارش

    order = get_object_or_404(Order, pk=order_id)
    events = order.events.all().order_by('happened_at', 'id')
    form = OrderEventForm(order=order)

    # --- محاسبهٔ مصرف خودکار: مبلغ هر ردیف و جمع کل ---
    stock_issues_qs = (
        order.stock_issues
        .select_related('item')
        .prefetch_related('linked_moves')
        .order_by('happened_at')
    )

    issue_costs_total = Decimal('0.00')
    for si in stock_issues_qs:
        row_cost = Decimal('0.00')
        for mv in si.linked_moves.all():
            if mv.movement_type == 'issue':
                qty_abs = (-mv.qty) if mv.qty < 0 else mv.qty  # خروج = منفی → قدر مطلق
                row_cost += Decimal(qty_abs) * Decimal(mv.unit_cost_effective)
        # ضمیمه‌کردن مبلغ هر ردیف روی آبجکت برای استفاده در قالب
        si.row_cost = row_cost.quantize(Decimal('0.01'))
        issue_costs_total += si.row_cost

    issue_costs_total = issue_costs_total.quantize(Decimal('0.01'))

    # --- محاسبهٔ درآمد/هزینه/سود سفارش (سرویس گام ۱) ---
    pnl = get_order_pnl(order.id)

    # --- دستمزد مراحل همین سفارش ---
    wage_logs = (
        StageWorkLog.objects
        .filter(order=order)
        .select_related("stage_tpl", "technician")
        .order_by("-created_at", "-id")
    )
    wage_total = wage_logs.aggregate(s=Sum("total_wage"))["s"] or 0

    wages_by_stage = (
        wage_logs.values("stage_tpl__label")
        .annotate(total=Sum("total_wage"))
        .order_by()
    )
    wages_by_tech = (
        wage_logs.values("technician__name")
        .annotate(total=Sum("total_wage"))
        .order_by()
    )

    from django.urls import reverse
    try:
        workbench_url = reverse("core:core_workbench_order", args=[order.id])
    except Exception:
        # اگر namespacing متفاوت بود، حداقل خالی نشه
        workbench_url = ""


    context = {
        'order': order,
        'events': events,
        'event_form': form,
        'stock_issues': stock_issues_qs,
        'issue_costs_total': issue_costs_total,
        'digital_lab_cost': order.digital_lab_cost,
        'digital_lab_transfers': order.digital_lab_transfers.order_by('-sent_date', '-id'),
        'order_pnl': pnl,
        'order_revenue': pnl['revenue'],
        'order_material_cogs': pnl['material_cogs'],
        'order_digital_lab_cost_calc': pnl['digital_lab_cost'],
        'order_allocation_share': pnl['allocation_share'],
        'order_gross_profit': pnl['gross_profit'],
        'order_net_profit': pnl['net_profit'],
        'wage_logs': wage_logs,
        'wage_total': wage_total,
        'wages_by_stage': wages_by_stage,
        'wages_by_tech': wages_by_tech,
        'workbench_url': workbench_url,
    }
    return render(request, 'core/order_detail.html', context)


from django.contrib import messages
# ...

def add_order_event(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:home")

    if request.method != "POST":
        return redirect(next_url)

    # نرمال‌سازی تاریخ جلالی
    data = request.POST.copy()
    raw = (data.get("happened_at") or "").strip()
    data["happened_at"] = _normalize_for_jalali_field(raw)  # "1404/07/19" → "1404-07-19"

    # --- Edit 3.A: نگاشت «علت» به stage + سازگاری + پر کردن خودکار مسیر ---
    CAUSE_LABELS = {
        "components_announce": "اعلام قطعات",
        "components_received": "دریافت قطعات",
        "waxrim_record_bite": "waxrim & record bite",
        "dural_try_in": "امتحان دورالی",
        "frame_try_in": "امتحان فریم",
        "porcelain_try_in": "امتحان پرسلن",
        "framework_design": "طراحی فریم",
        "custom_abutment": "کاستومایز اباتمنت",
        "qc_check": "بررسی کیفی",
        "other": "سایر",
    }
    cause_choice = (data.get("cause_choice") or "").strip()
    cause_text   = (data.get("cause_text") or "").strip()
    if cause_choice:
        data["stage"] = cause_text if cause_choice == "other" else CAUSE_LABELS.get(cause_choice, cause_choice)

    # سازگاری: «بازگشت از مطب» → «دریافت در لابراتوار»
    if data.get("event_type") == OrderEvent.EventType.RETURNED_FROM_CLINIC:
        data["event_type"] = OrderEvent.EventType.RECEIVED_IN_LAB

    # اگر direction نیامده بود، از نوع رویداد پر شود
    ev_type = (data.get("event_type") or "").strip()
    if not data.get("direction"):
        dir_map = {
            OrderEvent.EventType.SENT_TO_CLINIC:        OrderEvent.Direction.LAB_TO_CLINIC,
            OrderEvent.EventType.RECEIVED_IN_LAB:       OrderEvent.Direction.CLINIC_TO_LAB,
            OrderEvent.EventType.SENT_TO_DIGITAL:       OrderEvent.Direction.LAB_TO_DIGITAL,
            OrderEvent.EventType.RECEIVED_FROM_DIGITAL: OrderEvent.Direction.DIGITAL_TO_LAB,
            OrderEvent.EventType.FINAL_SHIPMENT:        OrderEvent.Direction.LAB_TO_CLINIC,
            OrderEvent.EventType.DELIVERED:             OrderEvent.Direction.LAB_TO_CLINIC,
        }
        data["direction"] = dir_map.get(ev_type, OrderEvent.Direction.INTERNAL)
    # --- /Edit 3.A ---

    # فرم با کانتکست سفارش (برای محدودکردن مرحله‌ها)
    form = OrderEventForm(data, request.FILES, order=order)
    if form.is_valid():
        ev = form.save(commit=False)
        ev.order = order

        # --- Guard: stage_instance باید متعلق به همین سفارش باشد + پیش‌فرض‌گذاری stage ---
        si = form.cleaned_data.get('stage_instance')
        if si and si.order_id != order.id:
            si = None
            messages.error(request, "مرحلهٔ انتخاب‌شده متعلق به این سفارش نیست و نادیده گرفته شد.")

        ev.stage_instance = si
        if si and not ((ev.stage or '').strip()):
            ev.stage = si.label
        # --- /Guard ---

        ev.save()

        # همگام‌سازی سفارش با رویدادهای کلیدی
        try:
            if ev.event_type == OrderEvent.EventType.FINAL_SHIPMENT:
                order.shipped_date = ev.happened_at
                order.status = 'delivered'
                order.save(update_fields=['shipped_date', 'status'])
            elif ev.event_type in (
                OrderEvent.EventType.RECEIVED_IN_LAB,
                OrderEvent.EventType.RECEIVED_FROM_DIGITAL,
                OrderEvent.EventType.ADJUSTMENT,
            ):
                if order.status == 'delivered' and order.shipped_date and ev.happened_at and ev.happened_at >= order.shipped_date:
                    order.status = 'in_progress'
                    order.save(update_fields=['status'])
                    if not (ev.notes or '').strip():
                        ev.notes = "بازگشت پس از ارسال نهایی (اصلاح)"
                        ev.save(update_fields=['notes'])
        except Exception:
            pass

        # به‌روزرسانی وضعیت StageInstance بر اساس رویداد
        try:
            from .models import StageInstance  # ایمپورت محلی
            si = ev.stage_instance
            if si:
                changed_fields = []
                if ev.event_type in (OrderEvent.EventType.SENT_TO_CLINIC,
                                     OrderEvent.EventType.SENT_TO_DIGITAL):
                    if not si.started_date:
                        si.started_date = ev.happened_at
                        changed_fields.append('started_date')
                    if si.status != StageInstance.Status.IN_PROGRESS:
                        si.status = StageInstance.Status.IN_PROGRESS
                        changed_fields.append('status')
                elif ev.event_type in (OrderEvent.EventType.RECEIVED_IN_LAB,
                                       OrderEvent.EventType.RECEIVED_FROM_DIGITAL):
                    if not si.done_date:
                        si.done_date = ev.happened_at
                        changed_fields.append('done_date')
                    if si.status != StageInstance.Status.DONE:
                        si.status = StageInstance.Status.DONE
                        changed_fields.append('status')
                if changed_fields:
                    si.save(update_fields=changed_fields)
        except Exception:
            pass

        messages.success(request, "رویداد با موفقیت ثبت شد.")
    else:
        err = "; ".join([f"{k}: {', '.join(v)}" for k, v in form.errors.items()])
        messages.error(request, f"ثبت رویداد ناموفق بود — {err}")

    return redirect(next_url)

from django.views.decorators.http import require_POST

@require_POST
def add_order_event_bulk(request):
    """
    ثبت رویداد گروهی برای چند سفارش.
    ورودی:
      - order_id: می‌تواند چندبار تکرار شود (checkboxها) یا یک رشته‌ی comma-separated
      - event_type, happened_at, direction (اختیاری؛ اگر خالی بود auto-fill می‌کنیم)
      - cause_choice (+ cause_text برای «other») → نگاشت به فیلد متنی 'stage'
      - notes, attachment
    خروجی: پیام موفقیت/خطا + redirect به next
    """
    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or (reverse("core:home") + "#transfer-tab-pane")

    # ---- گردآوری IDها
    ids = request.POST.getlist('order_id') or request.POST.getlist('order_ids') or []
    if not ids:
        raw = (request.POST.get('order_ids') or '').strip()
        if raw:
            ids = [x.strip() for x in raw.split(',') if x.strip()]
    # تبدیل به int و حذف موارد نامعتبر
    order_ids = []
    for x in ids:
        try:
            order_ids.append(int(x))
        except Exception:
            pass
    if not order_ids:
        messages.error(request, "هیچ سفارشی انتخاب نشده است.")
        return redirect(next_url)

    # ---- آماده‌سازی داده‌ی مشترک فرم
    data = request.POST.copy()

    # ۱) نرمال‌سازی تاریخ جلالی
    raw_date = (data.get("happened_at") or "").strip()
    data["happened_at"] = _normalize_for_jalali_field(raw_date)

    # ۲) نگاشت علت → stage (متن)
    CAUSE_LABELS = {
        "components_announce": "اعلام قطعات",
        "components_received": "دریافت قطعات",
        "waxrim_record_bite": "waxrim & record bite",
        "dural_try_in": "امتحان دورالی",
        "frame_try_in": "امتحان فریم",
        "porcelain_try_in": "امتحان پرسلن",
        "framework_design": "طراحی فریم",
        "custom_abutment": "کاستومایز اباتمنت",
        "qc_check": "بررسی کیفی",
        "other": "سایر",
    }
    cause_choice = (data.get("cause_choice") or "").strip()
    cause_text   = (data.get("cause_text") or "").strip()
    if cause_choice:
        data["stage"] = cause_text if cause_choice == "other" else CAUSE_LABELS.get(cause_choice, cause_choice)

    # ۳) سازگاری: بازگشت از مطب → دریافت در لابراتوار
    if data.get("event_type") == OrderEvent.EventType.RETURNED_FROM_CLINIC:
        data["event_type"] = OrderEvent.EventType.RECEIVED_IN_LAB

    # ۴) پر کردن خودکار direction اگر خالی بود
    ev_type = (data.get("event_type") or "").strip()
    if not data.get("direction"):
        dir_map = {
            OrderEvent.EventType.SENT_TO_CLINIC:        OrderEvent.Direction.LAB_TO_CLINIC,
            OrderEvent.EventType.RECEIVED_IN_LAB:       OrderEvent.Direction.CLINIC_TO_LAB,
            OrderEvent.EventType.SENT_TO_DIGITAL:       OrderEvent.Direction.LAB_TO_DIGITAL,
            OrderEvent.EventType.RECEIVED_FROM_DIGITAL: OrderEvent.Direction.DIGITAL_TO_LAB,
            OrderEvent.EventType.FINAL_SHIPMENT:        OrderEvent.Direction.LAB_TO_CLINIC,
            OrderEvent.EventType.DELIVERED:             OrderEvent.Direction.LAB_TO_CLINIC,
        }
        data["direction"] = dir_map.get(ev_type, OrderEvent.Direction.INTERNAL)

    # ---- اجرا برای هر سفارش
    orders = Order.objects.filter(id__in=order_ids).select_related('patient')
    ok, fail = 0, 0

    # فایل پیوست را (اگر هست) یکسان برای همه استفاده می‌کنیم
    files = request.FILES

    for order in orders:
        try:
            # برای هر سفارش، فرم مستقل می‌سازیم تا ولیدیشن جدا انجام شود
            form = OrderEventForm(data, files, order=order)
            if form.is_valid():
                ev = form.save(commit=False)
                ev.order = order

                # اگر stage خالی است ولی علت از مراحل سفارش انتخاب شده بود (در آینده)، اینجا می‌شد پر کرد.
                # فعلاً همان stage متنی استفاده می‌شود.

                ev.save()

                # --- همگام‌سازی وضعیت سفارش برای رویدادهای کلیدی (مثل روالش در add_order_event) ---
                try:
                    if ev.event_type == OrderEvent.EventType.FINAL_SHIPMENT:
                        order.shipped_date = ev.happened_at
                        order.status = 'delivered'
                        order.save(update_fields=['shipped_date', 'status'])
                    elif ev.event_type in (
                        OrderEvent.EventType.RECEIVED_IN_LAB,
                        OrderEvent.EventType.RECEIVED_FROM_DIGITAL,
                        OrderEvent.EventType.ADJUSTMENT,
                    ):
                        if order.status == 'delivered' and order.shipped_date and ev.happened_at and ev.happened_at >= order.shipped_date:
                            order.status = 'in_progress'
                            order.save(update_fields=['status'])
                            if not (ev.notes or '').strip():
                                ev.notes = "بازگشت پس از ارسال نهایی (اصلاح)"
                                ev.save(update_fields=['notes'])
                except Exception:
                    pass
                # -----------------------------------------------------------------------

                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1

    if ok and not fail:
        messages.success(request, f"رویداد برای {ok} سفارش ثبت شد.")
    elif ok and fail:
        messages.warning(request, f"رویداد برای {ok} سفارش ثبت شد؛ {fail} مورد ناموفق بود.")
    else:
        messages.error(request, "ثبت گروهی ناموفق بود. ورودی‌ها را بررسی کنید.")

    return redirect(next_url)

@require_POST
def deliver_order(request, order_id):
    """
    میان‌بر «ارسال شد»: تاریخ ارسال واقعی را می‌گیرد،
    وضعیت سفارش را delivered می‌کند،
    و یک OrderEvent از نوع final_shipment ثبت می‌کند.
    """
    order = get_object_or_404(Order, pk=order_id)

    shipped_raw = (request.POST.get("shipped_date") or "").strip()
    shipped_norm = _normalize_for_jalali_field(shipped_raw)  # "1404-07-10" یا ""

    if shipped_norm:
        # 1) ذخیره در سفارش
        order.shipped_date = shipped_norm
        order.status = 'delivered'
        order.save()

        # 2) رویداد تایم‌لاین
        OrderEvent.objects.create(
            order=order,
            event_type=OrderEvent.EventType.FINAL_SHIPMENT,
            happened_at=shipped_norm,
            direction=OrderEvent.Direction.LAB_TO_CLINIC,
            notes="ارسال نهایی (میان‌بر از لیست)"
        )

    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:home")
    return redirect(next_url)

def order_edit(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if request.method == "POST":
        form = OrderForm(request.POST, instance=order)
        if form.is_valid():
            obj = form.save(commit=False)

            # 🔹 خواندن فیلد پنهان Tooth Picker و ذخیره روی مدل
            t_fdi = (request.POST.get('order-teeth_fdi') or '').strip()
            obj.teeth_fdi = t_fdi  # خالی = پاک کردن انتخاب‌ها

            obj.save()
            if hasattr(form, 'save_m2m'):
                form.save_m2m()

            messages.success(request, "سفارش با موفقیت ویرایش شد.")
            next_url = request.GET.get("next") or (reverse("core:orders_home") + "#list-tab-pane")
            return redirect(next_url)
        else:
            messages.error(request, "لطفاً خطاهای فرم را برطرف کنید.")
    else:
        form = OrderForm(instance=order)

    return render(request, "core/order_edit.html", {"form": form})
    # GET: نمایش فرم ویرایش
    form = OrderForm(instance=order)
    return render(request, "core/order_edit.html", {"form": form})


from datetime import date
from django.utils import timezone
from django.shortcuts import render
from django.apps import apps
from django.db.models import Q

def dashboard(request):
    """
    داشبورد برنامه: KPIهای سفارش‌ها + آخرین سفارش‌ها + لیست‌های خام برای مودال‌ها.
    منطق شمارش/لیست ماه جاری و امروز:
      - اگر jdatetime موجود باشد، بازهٔ «ماه جلالی فعلی» را می‌سازد
      - سپس OR می‌کند: (order_date داخل ماه جلالی) OR (created_at__date داخل همان بازه به میلادی)
      - برای امروز هم همین‌طور: (order_date == امروز جلالی) OR (created_at__date == امروز میلادی)
    """
    # jdatetime را محلی ایمپورت می‌کنیم تا تابع مستقل باشد
    try:
        import jdatetime
    except Exception:
        jdatetime = None

    try:
        Order = apps.get_model('core', 'Order')
    except Exception:
        Order = None

    today_g = timezone.localdate()  # تاریخ امروز میلادی
    tomorrow_g = date.fromordinal(today_g.toordinal() + 1)

    # ---------- آماده‌سازی ----------
    kpis = {
        'orders_today': 0,
        'orders_month': 0,
        'in_progress': 0,
        'done': 0,
        'overdue': 0,
        'deliveries_today': 0,
        'deliveries_tomorrow': 0,
        'open_invoices': None,
    }
    orders_today_list = []
    orders_month_list = []
    in_progress_list = []
    delivered_list = []
    overdue_list = []
    deliveries_today_list = []
    deliveries_tomorrow_list = []
    latest_orders = []

    if Order is not None:
        qs_all = Order._base_manager.all()
        fns = {f.name for f in Order._meta.get_fields()}

        # بازهٔ ماه جاری (جلالی + معادل میلادی)
        if jdatetime:
            jt_today = jdatetime.date.fromgregorian(date=today_g)
            jt_tomorrow = jdatetime.date.fromgregorian(date=tomorrow_g)
            j_month_start = jdatetime.date(jt_today.year, jt_today.month, 1)
            j_month_end = (
                jdatetime.date(jt_today.year + 1, 1, 1)
                if jt_today.month == 12
                else jdatetime.date(jt_today.year, jt_today.month + 1, 1)
            ) - jdatetime.timedelta(days=1)
            g_month_start = j_month_start.togregorian()
            g_month_end = j_month_end.togregorian()
        else:
            # اگر jdatetime نداریم، از ماه میلادی استفاده می‌کنیم
            jt_today = jt_tomorrow = None
            j_month_start = j_month_end = None
            g_month_start = today_g.replace(day=1)
            # آخر ماه میلادی
            if today_g.month == 12:
                g_month_end = date(today_g.year + 1, 1, 1) - timezone.timedelta(days=1)
            else:
                g_month_end = date(today_g.year, today_g.month + 1, 1) - timezone.timedelta(days=1)

        # ---------- امروز (OR: order_date == امروز جلالی  یا  created_at__date == امروز میلادی) ----------
        q_today = qs_all
        if 'order_date' in fns and jdatetime:
            q_today = q_today.filter(Q(order_date=jt_today) | Q(created_at__date=today_g))
        else:
            # بدون jdatetime فقط بر مبنای created_at
            q_today = q_today.filter(created_at__date=today_g)

        kpis['orders_today'] = q_today.count()
        orders_today_list = list(q_today.order_by('-id')[:200])

        # ---------- ماه جاری (OR: order_date داخل ماه جلالی  یا  created_at__date داخل همان بازهٔ میلادی) ----------
        q_month = qs_all
        if 'order_date' in fns and jdatetime:
            q_month = q_month.filter(
                Q(order_date__gte=j_month_start, order_date__lte=j_month_end) |
                Q(created_at__date__gte=g_month_start, created_at__date__lte=g_month_end)
            )
        else:
            # بدون jdatetime فقط created_at (ماه میلادی)
            q_month = q_month.filter(
                created_at__date__gte=g_month_start, created_at__date__lte=g_month_end
            )

        # عدد KPI و لیست هر دو از همین q_month ساخته شوند تا «۱۲ ولی لیست ۳تا» پیش نیاید
        kpis['orders_month'] = q_month.count()
        orders_month_list = list(q_month.order_by('-id')[:500])

        # ---------- وضعیت‌ها ----------
        if 'status' in fns:
            q_inp  = qs_all.filter(status__iexact='in_progress')
            q_delv = qs_all.filter(status__iexact='delivered')
            kpis['in_progress'] = q_inp.count()
            kpis['done']        = q_delv.count()
            in_progress_list    = list(q_inp.order_by('-id')[:300])
            delivered_list      = list(q_delv.order_by('-id')[:300])

        # ---------- معوق / تحویل‌های امروز/فردا ----------
        if 'due_date' in fns:
            if jdatetime:
                # مقایسه با جلالی
                q_over = qs_all.filter(due_date__lt=jt_today)
                if 'status' in fns:
                    q_over = q_over.exclude(status__iexact='delivered')
                kpis['overdue'] = q_over.count()
                overdue_list    = list(q_over.order_by('-id')[:300])

                q_dt = qs_all.filter(due_date=jt_today)
                q_tm = qs_all.filter(due_date=jt_tomorrow)
                if 'status' in fns:
                    q_dt = q_dt.exclude(status__iexact='delivered')
                    q_tm = q_tm.exclude(status__iexact='delivered')
                kpis['deliveries_today']    = q_dt.count()
                kpis['deliveries_tomorrow'] = q_tm.count()
                deliveries_today_list       = list(q_dt.order_by('-id')[:300])
                deliveries_tomorrow_list    = list(q_tm.order_by('-id')[:300])
            else:
                # مقایسه با میلادی
                q_over = qs_all.filter(due_date__lt=today_g)
                if 'status' in fns:
                    q_over = q_over.exclude(status__iexact='delivered')
                kpis['overdue'] = q_over.count()
                overdue_list    = list(q_over.order_by('-id')[:300])

                q_dt = qs_all.filter(due_date=today_g)
                q_tm = qs_all.filter(due_date=tomorrow_g)
                if 'status' in fns:
                    q_dt = q_dt.exclude(status__iexact='delivered')
                    q_tm = q_tm.exclude(status__iexact='delivered')
                kpis['deliveries_today']    = q_dt.count()
                kpis['deliveries_tomorrow'] = q_tm.count()
                deliveries_today_list       = list(q_dt.order_by('-id')[:300])
                deliveries_tomorrow_list    = list(q_tm.order_by('-id')[:300])

        # ---------- آخرین سفارش‌ها ----------
        # همون روال ساده: جدیدترین‌ها بر اساس created_at در دسترس‌تر است
        order_by = '-created_at' if 'created_at' in fns else '-id'
        latest_orders = list(qs_all.order_by(order_by)[:8])

    # فاکتورهای باز (اگر app billing باشد)
    try:
        Invoice = apps.get_model('billing', 'Invoice')
        issued_val = getattr(Invoice.Status, 'ISSUED', 'issued')
        kpis['open_invoices'] = Invoice.objects.filter(status=issued_val).count()
    except Exception:
        pass
    
        # --- Lab profile برای هدر ---
    try:
        LabProfile = apps.get_model('billing', 'LabProfile')
        lab_profile = LabProfile.objects.first()
    except Exception:
        lab_profile = None

    return render(request, 'core/dashboard.html', {
        'kpis': kpis,
        'latest_orders': latest_orders,
        'orders_today_list': orders_today_list,
        'orders_month_list': orders_month_list,
        'in_progress_list': in_progress_list,
        'delivered_list': delivered_list,
        'overdue_list': overdue_list,
        'deliveries_today_list': deliveries_today_list,
        'deliveries_tomorrow_list': deliveries_tomorrow_list,
        'lab_profile': lab_profile,
    })

# ============================
# APIs for quick in/out panel
# ============================

@require_GET
def api_doctors(request):
    """
    لیست دکترها برای دراپ‌دان + جستجو با q
    پاسخ: [{id, name}]
    """
    q = (request.GET.get('q') or '').strip()
    qs = Doctor.objects.all()
    if q:
        qs = qs.filter(name__icontains=q)
    data = [{'id': d.id, 'name': d.name} for d in qs.order_by('name')[:100]]
    return JsonResponse({'results': data})


@require_GET
def api_orders_by_doctor(request):
    doc_id = request.GET.get('doctor_id')
    q = (request.GET.get('q') or '').strip()

    if not doc_id:
        return JsonResponse({'results': []})

    try:
        doctor = Doctor.objects.get(pk=doc_id)
    except Doctor.DoesNotExist:
        return JsonResponse({'results': []})

    # فقط سفارش‌های جاری: pending / in_progress
    qs = (Order._base_manager
          .select_related('patient')
          .filter(doctor=doctor.name, status__in=['pending', 'in_progress'])
          .order_by('-id'))

    if q:
        qs = qs.filter(patient__name__icontains=q)

    results = []
    for o in qs[:300]:
        results.append({
            'id': o.id,
            'patient_name': (o.patient.name if o.patient_id else ''),
            'serial_number': o.serial_number or '',
            'due_date': (str(o.due_date).replace('-', '/') if o.due_date else ''),
            "doctor": o.doctor,
            "product_code": getattr(o, "order_type", None),  # ← کلید اصلی: کُد محصول (مثلاً implant_pfm, implant_zirconia, ...)
        })
    return JsonResponse({'results': results})


@require_GET
def api_products(request):
    """
    لیست محصولات فعال برای Dropdown/Javascript
    GET /api/products?q=
    پاسخ: {"results": [{"code": "...", "name": "..."}]}
    """
    q = (request.GET.get('q') or '').strip()
    qs = Product.objects.filter(is_active=True)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
    data = [{"code": p.code, "name": p.name} for p in qs.order_by("name")[:200]]
    return JsonResponse({"results": data})

# ============================
# API: stages for a specific order
# ============================
from django.views.decorators.http import require_GET  # اگر بالاتر داری، تکرار نکن

@require_GET
def api_order_stages(request):
    """
    GET /api/order-stages?order_id=123
    پاسخ: {"results":[{"label":"امتحان فریم"}, {"label":"امتحان پرسلن"}, ...]}
    """
    order_id_raw = (request.GET.get("order_id") or "").strip()
    if not order_id_raw:
        return JsonResponse({"results": []})

    # نرمال‌سازی: ارقام فارسی/عربی → انگلیسی + حذف هرچیز غیرعددی (مثل <> و فاصله)
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    order_id_norm = order_id_raw.translate(trans)

    import re
    order_id_digits = re.sub(r"[^\d]", "", order_id_norm)

    if not order_id_digits:
        return JsonResponse({"results": []})

    try:
        oid = int(order_id_digits)
    except ValueError:
        return JsonResponse({"results": []})

    try:
        order = Order.objects.get(pk=oid)

    except Order.DoesNotExist:
        return JsonResponse({"results": []})

    # همهٔ مراحل را بده تا optgroup خالی نشود
    stages = order.stages.all().order_by("order_index", "id")
    data = [{"id": s.id, "label": s.label} for s in stages]
    return JsonResponse({"results": data})

# core/views.py
from django.shortcuts import render  # بالاتر هست، اگر نبود نگهش دار

def transfer_gate(request):
    """
    صفحهٔ «ورود/خروج سریع سفارش‌ها» (کنسول ترنسفر)
    """
    return render(request, 'core/transfer_gate.html')


from django.shortcuts import render  # احتمالاً بالای فایل داری
from django.db.models import Q

def workbench(request):
    """
    Workbench مراحل با:
      - status: active(پیش‌فرض) | done | all
      - q: جستجو روی بیمار/دکتر/مرحله/سریال/ID سفارش
      - overdue=1: فقط مراحل عقب‌افتاده (planned_date < today & done_date IS NULL)
      - sort & dir: مرتب‌سازی ستونی
      - صفحه‌بندی: page, ps
      + KPI: شمارش مرحله‌ها و جمع واحدها (unit_count) + Top stages/products
    """
    from .models import StageInstance

    # ---- فیلتر وضعیت + overdue (برای جدول) ----
    status_filter = (request.GET.get('status') or 'active').strip().lower()
    overdue_flag = (request.GET.get('overdue') or '').strip().lower() in ('1', 'true', 'yes', 'on')

    if overdue_flag:
        statuses = [
            StageInstance.Status.PENDING,
            StageInstance.Status.IN_PROGRESS,
            StageInstance.Status.BLOCKED,
        ]
    else:
        if status_filter == 'done':
            statuses = [StageInstance.Status.DONE]
        elif status_filter == 'all':
            statuses = [
                StageInstance.Status.PENDING,
                StageInstance.Status.IN_PROGRESS,
                StageInstance.Status.DONE,
                StageInstance.Status.BLOCKED,
            ]
        else:
            statuses = [StageInstance.Status.PENDING, StageInstance.Status.IN_PROGRESS]

    qs = (
        StageInstance.objects
        .select_related('order', 'order__patient')
        .filter(status__in=statuses)
    )

    # ---- جستجو (search_q را نگه می‌داریم تا برای KPI هم استفاده کنیم) ----
    q = (request.GET.get('q') or '').strip()
    search_q = Q()
    if q:
        trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        q_norm = q.translate(trans)

        oid_filter = Q()
        if q_norm.isdigit():
            try:
                oid_filter = Q(order__id=int(q_norm)) | Q(order__serial_number__icontains=q_norm)
            except Exception:
                oid_filter = Q(order__serial_number__icontains=q_norm)

        search_q = (
            oid_filter |
            Q(order__patient__name__icontains=q) |
            Q(order__doctor__icontains=q) |
            Q(label__icontains=q) |
            Q(order__serial_number__icontains=q_norm)
        )
        qs = qs.filter(search_q)

    # ---- مرتب‌سازی جدول ----
    sort = (request.GET.get('sort') or '').strip().lower()
    direction = (request.GET.get('dir') or 'asc').strip().lower()
    desc = (direction == 'desc')

    sort_map = {
        'order':   ('order__id',),
        'patient': ('order__patient__name', 'order__id'),
        'doctor':  ('order__doctor', 'order__id'),
        'label':   ('label', 'order__id'),
        'planned': ('planned_date', 'order__id'),
        'started': ('started_date', 'order__id'),
        'done':    ('done_date', 'order__id'),
        'status':  ('status', 'order_index', 'order__id'),
    }
    if sort in sort_map:
        order_fields = [('-' + f) if desc else f for f in sort_map[sort]]
        qs = qs.order_by(*order_fields)
    else:
        qs = qs.order_by('planned_date', 'order__id', 'order_index', 'id')

    # ---- صفحه‌بندی جدول ----
    try:
        ps = int((request.GET.get('ps') or 50))
        if ps < 10: ps = 10
        if ps > 200: ps = 200
    except Exception:
        ps = 50

    paginator = Paginator(qs, ps)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # ---- برچسب عقب‌افتادگی روی آیتم‌های همین صفحه ----
    today = _today_jdate()

    def _ymd_int(d):
        try:
            return (d.year * 10000) + (d.month * 100) + d.day
        except Exception:
            return None

    today_i = _ymd_int(today)

    stages = list(page_obj.object_list)
    for s in stages:
        pd_i = _ymd_int(s.planned_date)
        dd_i = _ymd_int(s.done_date)
        s.is_overdue = bool(pd_i and not dd_i and today_i and (today_i > pd_i))

    if overdue_flag:
        # اگر فقط عقب‌افتاده‌ها برای جدول می‌خواهم
        stages = [s for s in stages if getattr(s, 'is_overdue', False)]

    # =========================
    # KPI (روی دیتاستِ جستجو شده، اما بدون محدودیت status/overdue)
    # =========================
    kpi_base = (
        StageInstance.objects
        .select_related('order', 'order__patient')
        .filter(search_q)  # فقط q اعمال می‌شود تا همهٔ وضعیت‌ها را بشماریم
    )

    # شمارش هر وضعیت
    kpi_count_in_progress = kpi_base.filter(status=StageInstance.Status.IN_PROGRESS).count()
    kpi_count_pending     = kpi_base.filter(status=StageInstance.Status.PENDING).count()
    kpi_count_blocked     = kpi_base.filter(status=StageInstance.Status.BLOCKED).count()
    kpi_count_done        = kpi_base.filter(status=StageInstance.Status.DONE).count()
    kpi_count_active      = kpi_count_pending + kpi_count_in_progress

    # عقب‌افتاده (planned < today و done_date تهی، صرف‌نظر از status=done)
    kpi_overdue_qs        = kpi_base.filter(planned_date__lt=today, done_date__isnull=True).exclude(status=StageInstance.Status.DONE)
    kpi_count_overdue     = kpi_overdue_qs.count()

    # جمع «واحد»ها
    kpi_units_in_progress = kpi_base.filter(status=StageInstance.Status.IN_PROGRESS).aggregate(u=Sum('order__unit_count'))['u'] or 0
    kpi_units_overdue     = kpi_overdue_qs.aggregate(u=Sum('order__unit_count'))['u'] or 0

    # Top stages by units (in progress)
    kpi_units_by_stage_in_progress = list(
        kpi_base
        .filter(status=StageInstance.Status.IN_PROGRESS)
        .values('label')
        .annotate(units=Sum('order__unit_count'))
        .order_by('-units', 'label')[:8]
    )

    # Top (product × stage) by units (in progress)
    kpi_units_by_prod_stage_in_progress = list(
        kpi_base
        .filter(status=StageInstance.Status.IN_PROGRESS)
        .values('order__order_type', 'label')
        .annotate(units=Sum('order__unit_count'))
        .order_by('-units', 'order__order_type', 'label')[:8]
    )

    context = {
        'stages': stages,
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'status_filter': status_filter,
        'q': q,
        'overdue': overdue_flag,
        'sort': sort,
        'dir': ('desc' if desc else 'asc'),
        'ps': ps,

        # --- KPI payload ---
        'kpi': {
            'count': {
                'active':      kpi_count_active,
                'in_progress': kpi_count_in_progress,
                'blocked':     kpi_count_blocked,
                'done':        kpi_count_done,
                'overdue':     kpi_count_overdue,
            },
            'units': {
                'in_progress_total': kpi_units_in_progress,
                'overdue_total':     kpi_units_overdue,
            },
            'top_stages_in_progress': kpi_units_by_stage_in_progress,           # [{label, units}, ...]
            'top_prod_stage_in_progress': kpi_units_by_prod_stage_in_progress,  # [{order__order_type, label, units}, ...]
        }
    }
    return render(request, 'core/workbench.html', context)


from django.views.decorators.http import require_POST

@require_POST
def stage_start_now(request, stage_id):
    """شروع امروزِ یک StageInstance + ثبت رویداد داخلی"""
    from .models import StageInstance  # import محلی تا چرخه ایجاد نشود
    si = get_object_or_404(StageInstance, pk=stage_id)
    today = _today_jdate()

    update_fields = []
    if not si.started_date:
        si.started_date = today
        update_fields.append('started_date')
    if si.status != StageInstance.Status.IN_PROGRESS:
        si.status = StageInstance.Status.IN_PROGRESS
        update_fields.append('status')
    if update_fields:
        si.save(update_fields=update_fields)

    # رویداد داخلی: «شروع مرحله»
    OrderEvent.objects.create(
        order=si.order,
        event_type=OrderEvent.EventType.IN_PROGRESS,
        happened_at=today,
        direction=OrderEvent.Direction.INTERNAL,
        stage=si.label,
        stage_instance=si,
        notes="شروع مرحله از Workbench"
    )

    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:workbench")
    messages.success(request, f"مرحله «{si.label}» شروع شد.")
    return redirect(next_url)


@require_POST
def stage_done_today(request, stage_id):
    """اتمام امروزِ یک StageInstance + ثبت رویداد داخلی"""
    from .models import StageInstance
    si = get_object_or_404(StageInstance, pk=stage_id)
    today = _today_jdate()

    update_fields = []
    if not si.done_date:
        si.done_date = today
        update_fields.append('done_date')
    if si.status != StageInstance.Status.DONE:
        si.status = StageInstance.Status.DONE
        update_fields.append('status')
    if update_fields:
        si.save(update_fields=update_fields)

    # رویداد داخلی: «اتمام مرحله»
    OrderEvent.objects.create(
        order=si.order,
        event_type=OrderEvent.EventType.NOTE,  # یادداشت داخلی برای پایان مرحله
        happened_at=today,
        direction=OrderEvent.Direction.INTERNAL,
        stage=si.label,
        stage_instance=si,
        notes="اتمام مرحله از Workbench"
    )

    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:workbench")
    messages.success(request, f"مرحله «{si.label}» تمام شد.")
    return redirect(next_url)

from django.views.decorators.http import require_POST

@require_POST
def stage_bulk_done_today(request):
    """
    اتمام گروهی مراحل (امروز) + ثبت OrderEvent داخلی برای هر مورد.
    انتظار ورودی:
      - stage_id (تکرارشونده): stage_id=12&stage_id=15&...
      - یا stage_ids (تکرارشونده): stage_ids=12&stage_ids=15&...
      - یا stage_ids (CSV): stage_ids=12,15,20
    """
    from .models import StageInstance  # import محلی
    today = _today_jdate()

    # --- جمع‌آوری IDها از POST (با نرمال‌سازی ارقام فارسی/عربی) ---
    raw_ids = []
    # 1) name=stage_id (multi)
    raw_ids += request.POST.getlist("stage_id")
    # 2) name=stage_ids (multi)
    raw_ids += request.POST.getlist("stage_ids")
    # 3) name=stage_ids (CSV)
    csv_blob = (request.POST.get("stage_ids") or "").strip()
    if csv_blob:
        raw_ids += [p.strip() for p in csv_blob.split(",") if p.strip()]

    # نرمال‌سازی به عدد صحیح
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    ids = []
    for r in raw_ids:
        if not r:
            continue
        s = str(r).translate(trans)
        s = "".join(ch for ch in s if ch.isdigit())
        if s:
            try:
                ids.append(int(s))
            except ValueError:
                pass
    ids = list(dict.fromkeys(ids))  # یکتا

    if not ids:
        messages.error(request, "هیچ مرحله‌ای برای اتمام گروهی انتخاب نشده است.")
        next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:workbench")
        return redirect(next_url)

    # --- اعمال تغییرات ---
    qs = StageInstance.objects.filter(pk__in=ids)
    updated = 0
    for si in qs:
        changed_fields = []
        if not si.done_date:
            si.done_date = today
            changed_fields.append('done_date')
        if si.status != StageInstance.Status.DONE:
            si.status = StageInstance.Status.DONE
            changed_fields.append('status')
        if changed_fields:
            si.save(update_fields=changed_fields)
            updated += 1

        # OrderEvent داخلی برای ثبت پایان مرحله
        OrderEvent.objects.create(
            order=si.order,
            event_type=OrderEvent.EventType.NOTE,  # ثابت نگه می‌داریم (یادداشت داخلی)
            happened_at=today,
            direction=OrderEvent.Direction.INTERNAL,
            stage=si.label,
            stage_instance=si,
            notes="اتمام مرحله (گروهی) از Workbench"
        )

    messages.success(request, f"{updated} مرحله علامت‌گذاری شد.")
    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:workbench")
    return redirect(next_url)

from django.views.decorators.http import require_POST

@require_POST
def stage_bulk_start_today(request):
    """
    شروع گروهی مراحل (امروز) + ثبت OrderEvent داخلی برای هر مورد.
    ورودی‌های قابل‌قبول:
      - stage_id (تکراری): stage_id=12&stage_id=15&...
      - stage_ids (تکراری): stage_ids=12&stage_ids=15&...
      - stage_ids (CSV): stage_ids=12,15,20
    """
    from .models import StageInstance  # import محلی
    today = _today_jdate()

    # --- جمع‌آوری IDها از POST (مثل اتمام گروهی) ---
    raw_ids = []
    raw_ids += request.POST.getlist("stage_id")
    raw_ids += request.POST.getlist("stage_ids")
    csv_blob = (request.POST.get("stage_ids") or "").strip()
    if csv_blob:
        raw_ids += [p.strip() for p in csv_blob.split(",") if p.strip()]

    # نرمال‌سازی به اعداد انگلیسی و تبدیل به int
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    ids = []
    for r in raw_ids:
        if not r: 
            continue
        s = str(r).translate(trans)
        s = "".join(ch for ch in s if ch.isdigit())
        if s:
            try:
                ids.append(int(s))
            except ValueError:
                pass
    ids = list(dict.fromkeys(ids))  # یکتا

    if not ids:
        messages.error(request, "هیچ مرحله‌ای برای شروع گروهی انتخاب نشده است.")
        next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:workbench")
        return redirect(next_url)

    # --- اعمال تغییرات ---
    qs = StageInstance.objects.filter(pk__in=ids)
    updated = 0
    for si in qs:
        changed_fields = []
        if not si.started_date:
            si.started_date = today
            changed_fields.append('started_date')
        if si.status != StageInstance.Status.IN_PROGRESS:
            si.status = StageInstance.Status.IN_PROGRESS
            changed_fields.append('status')
        if changed_fields:
            si.save(update_fields=changed_fields)
            updated += 1

        # رویداد داخلی: «شروع مرحله»
        OrderEvent.objects.create(
            order=si.order,
            event_type=OrderEvent.EventType.IN_PROGRESS,
            happened_at=today,
            direction=OrderEvent.Direction.INTERNAL,
            stage=si.label,
            stage_instance=si,
            notes="شروع مرحله (گروهی) از Workbench"
        )

    messages.success(request, f"{updated} مرحله شروع شد.")
    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:workbench")
    return redirect(next_url)

from django.views.decorators.http import require_POST

@require_POST
def stage_bulk_plan_date(request):
    """
    تنظیم برنامه (planned_date) به‌صورت گروهی برای StageInstance ها.
    ورودی:
      - planned_date: تاریخ (جلالی) مثل 1404/07/25 یا 1404-07-25
      - stage_id / stage_ids: دقیقاً مثل bulk های قبلی (تک/چند/CSV)
    """
    from .models import StageInstance  # import محلی تا چرخه ایجاد نشود

    # --- تاریخ برنامه ---
    raw_date = (request.POST.get("planned_date") or request.POST.get("date") or "").strip()
    if not raw_date:
        messages.error(request, "تاریخ برنامه را وارد کنید.")
        next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:workbench")
        return redirect(next_url)

    # نرمال‌سازی تاریخ جلالی به قالب قابل ذخیره (مثل 1404-07-25)
    planned_norm = raw_date
    try:
        planned_norm = _normalize_for_jalali_field(raw_date)  # اگر هلسپر قبلاً داری، ازش استفاده می‌کنیم
    except Exception:
        pass  # اگر نبود، همان raw استفاده می‌شود (برای jDateField معمولاً کافی است)

    # --- جمع‌آوری IDها (همان الگوی bulk قبلی) ---
    raw_ids = []
    raw_ids += request.POST.getlist("stage_id")
    raw_ids += request.POST.getlist("stage_ids")
    csv_blob = (request.POST.get("stage_ids") or "").strip()
    if csv_blob:
        raw_ids += [p.strip() for p in csv_blob.split(",") if p.strip()]

    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    ids = []
    for r in raw_ids:
        if not r:
            continue
        s = str(r).translate(trans)
        s = "".join(ch for ch in s if ch.isdigit())
        if s:
            try:
                ids.append(int(s))
            except ValueError:
                pass
    ids = list(dict.fromkeys(ids))

    if not ids:
        messages.error(request, "هیچ مرحله‌ای برای برنامه‌ریزی انتخاب نشده است.")
        next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:workbench")
        return redirect(next_url)

    # --- اعمال تغییرات ---
    qs = StageInstance.objects.filter(pk__in=ids)
    updated = 0
    for si in qs:
        if si.planned_date != planned_norm:
            si.planned_date = planned_norm
            si.save(update_fields=['planned_date'])
            updated += 1

            # رویداد داخلی برای ثبت برنامه‌ریزی
            OrderEvent.objects.create(
                order=si.order,
                event_type=OrderEvent.EventType.NOTE,
                happened_at=planned_norm,
                direction=OrderEvent.Direction.INTERNAL,
                stage=si.label,
                stage_instance=si,
                notes=f"برنامه‌ریزی مرحله (گروهی) — تاریخ: {planned_norm}"
            )

    messages.success(request, f"برنامه‌ریزی {updated} مرحله به تاریخ {planned_norm} انجام شد.")
    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:workbench")
    return redirect(next_url)


from django.views.decorators.http import require_POST
from .models import StageWorkLog, Technician
import jdatetime
from decimal import Decimal
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from .models import StageInstance, OrderEvent, StageWorkLog, Technician


@require_POST
def stage_bulk_claim(request):
    """
    ثبت دستمزد گروهی برای StageInstanceهای انتخاب‌شده.
    ورودی‌ها (POST):
      - stage_id / stage_ids  : یک یا چند ID از StageInstance (لیست/CSV)
      - technician_name       : نام تکنسین (رشته؛ باید با Technician.name تطابق داشته باشد)
      - unit_wage             : مبلغ توافقی واحد (اختیاری؛ اعداد فارسی/عربی هم اوکی)
      - finished_at           : تاریخ جلالی YYYY/MM/DD (اختیاری؛ خالی = امروز جلالی)
    منطق:
      - اگر unit_wage خالی باشد، مدل StageWorkLog خودش از StageRate/base_wage نرخ را resolve می‌کند.
      - ضدتکرار: برای همان (مرحله، تکنسین، تاریخ پایان، DONE) دوباره ثبت نمی‌کنیم.
      - پس از ساخت WorkLog، اگر مرحله هنوز done نشده باشد، done_date و status را به‌روز می‌کنیم.
    """

    # --- helper: نرمال‌سازی ارقام ---
    def _norm_num(s: str | None) -> str:
        if not s:
            return ""
        trans = str.maketrans({
            '۰':'0','۱':'1','۲':'2','۳':'3','۴':'4','۵':'5','۶':'6','۷':'7','۸':'8','۹':'9',
            '٠':'0','١':'1','٢':'2','٣':'3','٤':'4','٥':'5','٦':'6','٧':'7','٨':'8','٩':'9',
            '٬':'', ',':'', ' ':''})
        return s.translate(trans).strip()

    # --- stage_ids: لیست + CSV ---
    raw_ids = []
    raw_ids += request.POST.getlist("stage_id")
    raw_ids += request.POST.getlist("stage_ids")
    csv_blob = (request.POST.get("stage_ids") or "").strip()
    if csv_blob:
        raw_ids += [p.strip() for p in csv_blob.split(",") if p.strip()]

    ids = []
    for r in raw_ids:
        s = _norm_num(str(r))
        if s.isdigit():
            ids.append(int(s))
    ids = list(dict.fromkeys(ids))  # یونیک

    if not ids:
        messages.error(request, "هیچ مرحله‌ای انتخاب نشده است.")
        next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:station_panel")
        return redirect(next_url)

    # --- تکنسین (اجباری) ---
    tech_name = (request.POST.get("technician_name") or "").strip()
    if not tech_name:
        messages.error(request, "نام تکنسین را وارد کنید.")
        next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:station_panel")
        return redirect(next_url)

    tech = Technician.objects.filter(name=tech_name).first()
    if not tech:
        messages.error(request, f"تکنسین «{tech_name}» پیدا نشد.")
        next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:station_panel")
        return redirect(next_url)

    # --- مبلغ واحد (اختیاری) ---
    unit_wage_raw = _norm_num(request.POST.get("unit_wage") or "")
    try:
        unit_wage = Decimal(unit_wage_raw) if unit_wage_raw else None
    except Exception:
        unit_wage = None  # مدل خودش resolve می‌کند

    # --- تاریخ پایان: ورودی جلالی یا امروز جلالی ---
    raw_finished = (request.POST.get("finished_at") or "").strip()
    if raw_finished:
        try:
            y, m, d = [int(p) for p in raw_finished.replace("-", "/").split("/")[:3]]
            finished_at = jdatetime.date(y, m, d)
        except Exception:
            finished_at = jdatetime.date.today()
    else:
        finished_at = jdatetime.date.today()

    # --- اجرای Claim ---
    qs = StageInstance.objects.select_related('order', 'template').filter(pk__in=ids)
    created, skipped, errors = 0, 0, 0

    for si in qs:
        try:
            # ضدتکرار: همان مرحله/تکنسین/تاریخ/وضعیت DONE
            duplicate = StageWorkLog.objects.filter(
                stage_inst=si,
                technician=tech,
                finished_at=finished_at,
                status=StageWorkLog.Status.DONE
            ).exists()
            if duplicate:
                skipped += 1
                continue

            log = StageWorkLog(
                order=si.order,
                stage_inst=si,
                stage_tpl=si.template if si.template_id else None,
                technician=tech,
                quantity=Decimal(si.order.unit_count or 1),
                finished_at=finished_at,
                status=StageWorkLog.Status.DONE,
            )
            if unit_wage is not None:
                # اگر کاربر نرخ توافقی داد، همین را ثبت کن؛ وگرنه مدل خودش از StageRate/base_wage پر می‌کند
                log.unit_wage = unit_wage

            log.save()  # unit_wage (در صورت None) و total_wage در مدل محاسبه می‌شود

            # همگام‌سازی مرحله
            changed_fields = []
            if not si.done_date:
                si.done_date = finished_at
                changed_fields.append('done_date')
            if si.status != StageInstance.Status.DONE:
                si.status = StageInstance.Status.DONE
                changed_fields.append('status')
            if changed_fields:
                si.save(update_fields=changed_fields)

            created += 1

        except Exception as e:
            # برای از دست نرفتن دیتا، یک NOTE ثبت کنیم
            try:
                OrderEvent.objects.create(
                    order=si.order,
                    event_type=OrderEvent.EventType.NOTE,
                    happened_at=finished_at,
                    direction=OrderEvent.Direction.INTERNAL,
                    stage=si.label,
                    stage_instance=si,
                    notes=f"CLAIM FAILED — تکنسین: {tech_name} | دستمزد واحد: {unit_wage_raw or '—'} | خطا: {e}"
                )
            except Exception:
                pass
            errors += 1

    # پیام نهایی
    if created and not errors:
        messages.success(request, f"ثبت دستمزد برای {created} مرحله انجام شد." + (f" ({skipped} مورد تکراری رد شد)" if skipped else ""))
    elif created or skipped:
        messages.warning(request, f"ثبت دستمزد: موفق {created}، تکراری {skipped}، خطا {errors}.")
    else:
        messages.error(request, "هیچ موردی ثبت نشد. ورودی‌ها را بررسی کنید.")

    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or reverse("core:station_panel")
    return redirect(next_url)

from .models import StageInstance, StageTemplate, Doctor, Product


def station_panel(request):
    """
    پنل مسئول مرحله (Station):
      پارامترها:
        - key: StageTemplate.key
        - status: active|done|all (پیش‌فرض active)
        - q: جستجو
        - doctor: نام دقیق دکتر (Order.doctor)
        - product: کُد محصول (Order.order_type = Product.code)
        - page, ps: صفحه‌بندی
    """
    from .models import StageInstance, StageTemplate, Doctor, Product, Technician
    from django.db.models import Q
    from django.core.paginator import Paginator

    # 1) پارامترها را قبل از هر استفاده‌ای بخوان
    key           = (request.GET.get('key') or '').strip()
    q             = (request.GET.get('q') or '').strip()
    status_filter = (request.GET.get('status') or 'active').strip().lower()
    doctor_name   = (request.GET.get('doctor') or '').strip()
    product_code  = (request.GET.get('product') or '').strip()

    # 2) دراپ‌داون‌ها
    # 2.1) کلیدهای مرحله را بر اساس محصول (اگر انتخاب شده) محدود کن
    keys_qs = StageTemplate.objects.filter(is_active=True)
    if product_code:
        keys_qs = keys_qs.filter(product__code=product_code)
    keys_qs = keys_qs.order_by('product__name', 'order_index')
    keys = list(keys_qs.values_list('key', 'label').distinct())

    # 2.2) دکترها و محصولات
    doctors  = list(Doctor.objects.order_by('name').values_list('name', flat=True))
    products = list(Product.objects.filter(is_active=True).order_by('name').values('code', 'name'))
    technicians = list(Technician.objects.filter(is_active=True)
                   .order_by('name').values_list('name', flat=True))

    # 3) جدول مراحل
    stages_qs = StageInstance.objects.none()
    if key:
        stages_qs = (
            StageInstance.objects
            .select_related('order', 'order__patient', 'template')
            .filter(key=key)
            .exclude(order__status='delivered')   # سفار‌ش‌های تحویل‌شده را نشان نده
        )

        # اگر محصول انتخاب شده، از هر دو مسیر فیلتر کن (Template و Order)
        if product_code:
            stages_qs = stages_qs.filter(template__product__code=product_code)
            stages_qs = stages_qs.filter(order__order_type=product_code)

        # فیلتر وضعیت
        if status_filter == 'done':
            stages_qs = stages_qs.filter(status=StageInstance.Status.DONE)
        elif status_filter == 'all':
            pass
        else:
            stages_qs = stages_qs.filter(
                status__in=[StageInstance.Status.PENDING, StageInstance.Status.IN_PROGRESS]
            )

        # فیلتر دکتر
        if doctor_name:
            stages_qs = stages_qs.filter(order__doctor=doctor_name)

        # جستجو
        if q:
            trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
            q_norm = q.translate(trans)
            oid_filter = Q()
            if q_norm.isdigit():
                try:
                    oid_filter = Q(order__id=int(q_norm)) | Q(order__serial_number__icontains=q_norm)
                except Exception:
                    oid_filter = Q(order__serial_number__icontains=q_norm)
            stages_qs = stages_qs.filter(
                oid_filter |
                Q(order__patient__name__icontains=q) |
                Q(order__doctor__icontains=q) |
                Q(label__icontains=q) |
                Q(order__serial_number__icontains=q_norm)
            )

        # مرتب‌سازی
        stages_qs = stages_qs.order_by('status', 'planned_date', 'order__id', 'order_index', 'id')

    # 4) صفحه‌بندی
    try:
        ps = int((request.GET.get('ps') or 50))
        if ps < 10: ps = 10
        if ps > 200: ps = 200
    except Exception:
        ps = 50
    paginator   = Paginator(stages_qs, ps)
    page_number = request.GET.get('page')
    page_obj    = paginator.get_page(page_number)
    stages      = list(page_obj.object_list)

    # 5) برچسب عقب‌افتاده
    today = _today_jdate()
    def _ymd_int(d):
        try: return d.year*10000 + d.month*100 + d.day
        except: return None
    today_i = _ymd_int(today)
    for s in stages:
        pd_i = _ymd_int(s.planned_date)
        dd_i = _ymd_int(s.done_date)
        s.is_overdue = bool(pd_i and not dd_i and today_i and (today_i > pd_i))

    context = {
        'keys': keys,
        'key': key,
        'q': q,
        'status_filter': status_filter,
        'ps': ps,
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'stages': stages,
        # فیلترهای جدید در UI
        'doctors': doctors,
        'products': products,
        'doctor': doctor_name,
        'product': product_code,
        'technicians': technicians,
    }
    return render(request, 'core/station.html', context)

# ===[ Digital Lab: Create Transfer in User Panel ]=========================
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from core.models import DigitalLabTransfer, Order
import jdatetime


def _to_decimal_safe(s):
    try:
        if s is None:
            return Decimal('0')
        return Decimal(str(s))
    except Exception:
        return Decimal('0')


def _parse_jalali_to_gregorian(s):
    """تبدیل تاریخ شمسی (در قالب 1404/08/10) به میلادی (datetime.date)"""
    if not s:
        return None
    try:
        s = s.strip().replace("-", "/")
        parts = [int(x) for x in s.split("/") if x.strip()]
        if len(parts) != 3:
            return None
        j = jdatetime.date(parts[0], parts[1], parts[2])
        return j.togregorian()  # خروجی datetime.date میلادی
    except Exception:
        return None


class DigitalLabTransferForm(forms.ModelForm):
    class Meta:
        model = DigitalLabTransfer
        fields = ['lab_name', 'status', 'note']
        widgets = {
            'lab_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'نام لاب دیجیتال…'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'توضیح (اختیاری)…'}),
        }

@xframe_options_exempt
def digital_lab_transfer_create(request, order_id=None):
    """
    فرم ثبت/ویرایش ارسال/دریافت لاب دیجیتال.
    - هم از مسیر عمومی /digital-lab/new/ و هم از /orders/<id>/digital-lab/new/ کار می‌کند.
    - فقط بر اساس order_ids[] ارسالی از فرم ذخیره می‌کند (نه پارامتر URL).
    """
    ctx_order = None
    if order_id:
        ctx_order = get_object_or_404(Order, pk=order_id)

    if request.method == 'POST':
        order_ids = request.POST.getlist('order_ids[]') or request.POST.getlist('order_ids')
        if not order_ids:
            messages.error(request, 'حداقل یک سفارش را انتخاب کنید.')
            return redirect('core:digital_lab_new_global' if not order_id else 'core:digital_lab_new', order_id=order_id)

        # --- فیلدهای مشترک ---
        lab_name = (request.POST.get('lab_name') or '').strip()
        stage_name = (request.POST.get('stage_name') or '').strip()
        shade_code = (request.POST.get('shade_code') or '').strip()
        status = (request.POST.get('status') or DigitalLabTransfer.Status.SENT).strip()

        # اضافه شود (برای شرط‌گذاری بعدی):
        is_received = (status == DigitalLabTransfer.Status.RECEIVED)
        
        # --- تاریخ‌ها ---
        sent_date = request.POST.get('sent_date') or None
        received_date = request.POST.get('received_date') or None

        # تبدیل تاریخ‌ها از شمسی به میلادی
        sent_date = _parse_jalali_to_gregorian(sent_date)
        received_date = _parse_jalali_to_gregorian(received_date)

        if status != DigitalLabTransfer.Status.RECEIVED:
            received_date = None

        # --- مبالغ ---
        unit_price = _to_decimal_safe(request.POST.get('charge_unit'))
        credit_amount = _to_decimal_safe(request.POST.get('credit_amount'))
        note = (request.POST.get('note') or '').strip()

        # --- دریافت تعداد اباتمنت (در صورت وجود) ---
        units_override_raw = request.POST.get('units_override') or ''
        units_override = None
        if units_override_raw.strip():
            try:
                units_override = Decimal(str(units_override_raw.strip()))
            except Exception:
                units_override = None

        # --- ذخیره ---
        first_oid = None
        created = 0

        for oid in order_ids:
            try:
                o = Order.objects.get(pk=int(oid))
            except (ValueError, Order.DoesNotExist):
                continue

            if first_oid is None:
                first_oid = o.id

            # --- محاسبه تعداد برای ضرب ---
            # اگر units_override ارسال شده و سفارش ایمپلنت + مرحله abutment.select باشه
            is_implant = (
                hasattr(o, 'product_code') and 
                str(o.product_code).lower() in ['implant_pfm', 'implant_zirconia']
            ) or (
                hasattr(o, 'order_type') and 
                str(o.order_type).lower() in ['implant_pfm', 'implant_zirconia']
            ) or (
                hasattr(o, 'product_kind') and 
                str(o.product_kind).lower() in ['implant_pfm', 'implant_zirconia']
            )
            
            # جدید (هر مرحله‌ای که شامل «abutment» یا «اباتمنت» باشد):
            stage_norm = stage_name.lower().replace(' ', '').replace('.', '').replace('-', '').replace('_', '')
            is_abutment_stage = ('abutment' in stage_norm) or ('اباتمنت' in stage_norm)



            if is_implant and is_abutment_stage and units_override is not None:
                # حالت استثنا: تعداد اباتمنت
                qty = units_override
            else:
                # حالت عادی: تعداد واحد سفارش
                units = getattr(o, 'unit_count', None) or getattr(o, 'units', None) or 1
                try:
                    qty = Decimal(str(units))
                except Exception:
                    qty = Decimal('1')

            charge_amount = unit_price * qty

            DigitalLabTransfer.objects.create(
                order=o,
                lab_name=lab_name,
                stage_name=stage_name,
                shade_code=shade_code,
                status=status,
                sent_date=sent_date,
                received_date=received_date,
                charge_amount=charge_amount,
                credit_amount=credit_amount,
                note=note,
            )
            created += 1

        if created:
            messages.success(request, f'ثبت لاب دیجیتال برای {created} سفارش انجام شد.')
            return redirect('core:order_detail', order_id=first_oid)
        else:
            messages.error(request, 'عملیات انجام نشد.')
            return redirect('core:digital_lab_new_global' if not order_id else 'core:digital_lab_new', order_id=order_id)

    # --- GET ---
    form = DigitalLabTransferForm(initial={'status': DigitalLabTransfer.Status.SENT})
    return render(request, 'core/digital_lab_form.html', {
        'order': ctx_order,
        'form': form,
    })

# ===[ /Digital Lab ]======================================================

# ===[ Digital Lab: List & Filters ]========================================
from django.db.models import Sum, F, Value as V
from django.db.models.functions import Coalesce

def _parse_jalali_to_date_or_none(s):
    """ورودی شمسی 'YYYY/MM/DD' → date میلادی یا None (از همان منطق قبلی استفاده می‌کند)"""
    if not s:
        return None
    try:
        s = s.strip().replace("-", "/")
        y, m, d = [int(x) for x in s.split("/") if x.strip()]
        return jdatetime.date(y, m, d).togregorian()
    except Exception:
        return None
@xframe_options_exempt
def digital_lab_transfer_list(request):
    """
    لیست کامل ارسال/دریافت‌های لاب دیجیتال + فیلترها + جمع مبالغ
    فیلترها (GET):
      q               : متن آزاد (بیمار/یادداشت/نام لاب/مرحله)
      doctor          : نام دکتر (رشته کامل)
      lab             : نام لاب دیجیتال (exact/icontains)
      order_id        : شناسه سفارش
      status          : SENT / RECEIVED / CANCELED
      date_from/date_to: بازه تاریخ (شمسی 'YYYY/MM/DD')
    """
    qs = (DigitalLabTransfer.objects
          .select_related('order', 'order__patient')
          .order_by('-sent_date', '-id'))

    q = (request.GET.get('q') or '').strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(order__patient__name__icontains=q) |
            Q(lab_name__icontains=q) |
            Q(stage_name__icontains=q) |
            Q(note__icontains=q)
        )

    doctor = (request.GET.get('doctor') or '').strip()
    if doctor:
        qs = qs.filter(order__doctor=doctor)

    lab = (request.GET.get('lab') or '').strip()
    if lab:
        qs = qs.filter(lab_name__icontains=lab)

    order_id = (request.GET.get('order_id') or '').strip()
    if order_id.isdigit():
        qs = qs.filter(order_id=int(order_id))

    status = (request.GET.get('status') or '').strip()
    # وضعیت‌های مجاز را از خود TextChoices مدل بخوان
    try:
        allowed_status = {c.value for c in DigitalLabTransfer.Status}
    except Exception:
        # اگر به هر دلیل Status از نوع TextChoices نبود، به مقدارهای رایج برگردیم
        allowed_status = {'SENT', 'RECEIVED'}

    if status in allowed_status:
        qs = qs.filter(status=status)


    df = _parse_jalali_to_date_or_none(request.GET.get('date_from'))
    dt = _parse_jalali_to_date_or_none(request.GET.get('date_to'))
    if df:
        qs = qs.filter(sent_date__gte=df)
    if dt:
        qs = qs.filter(sent_date__lte=dt)

    # تجمع مبالغ
    agg = qs.aggregate(
        total_charge=Coalesce(
            Sum('charge_amount'),
            V(0, output_field=DecimalField(max_digits=18, decimal_places=2)),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ),
        total_credit=Coalesce(
            Sum('credit_amount'),
            V(0, output_field=DecimalField(max_digits=18, decimal_places=2)),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ),
    )

    sum_charge = agg['total_charge'] or Decimal('0.00')
    sum_credit = agg['total_credit'] or Decimal('0.00')
    net_amount = sum_charge - sum_credit

    context = {
        'filters': {
            'q': q, 'doctor': doctor, 'lab': lab,
            'order_id': order_id, 'status': status,
            'date_from': request.GET.get('date_from') or '',
            'date_to': request.GET.get('date_to') or '',
        },
        'rows': qs[:500],           # محدودیت موقت برای نمایش
        'count': qs.count(),
        'sum_charge': sum_charge,
        'sum_credit': sum_credit,
        'sum_net': net_amount,
    }
    return render(request, 'core/digital_lab_list.html', context)
# ===[ /Digital Lab List ]==================================================

# core/views.py
from decimal import Decimal
from django.db.models import Sum, Count, Value as V, DecimalField, CharField
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import render
# فرض بر این است که DigitalLabTransfer و Order از قبل import شده‌اند
# و تابع _parse_jalali_to_date_or_none قبلاً در فایل هست.

def digital_lab_report(request):
    """
    گزارش تحلیلی لاب دیجیتال (مالی‌محور + چند شاخص عملکردی)
    فیلترها (GET):
      q, doctor, lab, status (SENT/RECEIVED), date_from, date_to
    """
    qs = (DigitalLabTransfer.objects
          .select_related('order')
          .order_by('-sent_date', '-id'))

    # ----- فیلترها -----
    q = (request.GET.get('q') or '').strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(order__patient_name__icontains=q) |  # اگر این فیلد در Order هست
            Q(lab_name__icontains=q) |
            Q(stage_name__icontains=q) |
            Q(note__icontains=q) |
            Q(order__serial_number__icontains=q)
        )

    doctor = (request.GET.get('doctor') or '').strip()
    if doctor:
        qs = qs.filter(order__doctor=doctor)

    lab = (request.GET.get('lab') or '').strip()
    if lab:
        qs = qs.filter(lab_name__icontains=lab)

    status = (request.GET.get('status') or '').strip()
    try:
        allowed_status = {c.value for c in DigitalLabTransfer.Status}
    except Exception:
        allowed_status = {'SENT','RECEIVED'}
    if status in allowed_status:
        qs = qs.filter(status=status)

    date_from = _parse_jalali_to_date_or_none(request.GET.get('date_from'))
    date_to   = _parse_jalali_to_date_or_none(request.GET.get('date_to'))
    if date_from:
        qs = qs.filter(sent_date__gte=date_from)
    if date_to:
        qs = qs.filter(sent_date__lte=date_to)

    # ----- KPI های اصلی -----
    agg = qs.aggregate(
        total_charge = Coalesce(Sum('charge_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2))),
        total_credit = Coalesce(Sum('credit_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2))),
        rows_count   = Coalesce(Count('id'), V(0)),
    )
    sum_charge = agg['total_charge'] or Decimal('0.00')
    sum_credit = agg['total_credit'] or Decimal('0.00')
    sum_net    = (sum_charge - sum_credit)

    # تعداد سفارش‌های یکتایی که در گزارش دیده می‌شوند
    try:
        # اگر DB اجازه distinct('field') نده، fallback به set در template می‌تونیم بدهیم.
        unique_orders_count = qs.values('order_id').distinct().count()
    except Exception:
        unique_orders_count = 0

    # ----- ریزِ لاب‌ها (Top labs) -----
    per_lab = (
        qs.values('lab_name')
          .annotate(
              lab_charge=Coalesce(Sum('charge_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2))),
              lab_credit=Coalesce(Sum('credit_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2))),
              rows=Count('id'),
          )
          .annotate(lab_net=(Coalesce(Sum('charge_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2)))
                             - Coalesce(Sum('credit_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2)))))
          .order_by('-lab_net', '-lab_charge')[:20]
    )

    # ----- ریزِ مرحله‌ها (Top stages) -----
    per_stage = (
        qs.values('stage_name')
          .annotate(
              st_charge=Coalesce(Sum('charge_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2))),
              st_credit=Coalesce(Sum('credit_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2))),
              rows=Count('id'),
          )
          .annotate(st_net=(Coalesce(Sum('charge_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2)))
                            - Coalesce(Sum('credit_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2)))))
          .order_by('-st_net', '-st_charge')[:20]
    )

    # ----- سری ماهانه ۶ ماه اخیر (بر اساس sent_date) -----
    monthly = (
        qs.annotate(m=TruncMonth('sent_date'))
          .values('m')
          .annotate(
              m_charge=Coalesce(Sum('charge_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2))),
              m_credit=Coalesce(Sum('credit_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2))),
          )
          .annotate(m_net=(Coalesce(Sum('charge_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2)))
                           - Coalesce(Sum('credit_amount'), V(0, output_field=DecimalField(max_digits=18, decimal_places=2)))))
          .order_by('m')
    )

    # داده‌های جدول (نمونه محدود برای مشاهده سریع)
    rows = qs.select_related('order').only(
        'id','lab_name','stage_name','shade_code','status','sent_date','received_date',
        'charge_amount','credit_amount','order_id'
    )[:500]

    context = {
        'filters': {
            'q': q, 'doctor': doctor, 'lab': lab, 'status': status,
            'date_from': request.GET.get('date_from') or '',
            'date_to':   request.GET.get('date_to') or '',
        },
        'kpi': {
            'sum_charge': sum_charge,
            'sum_credit': sum_credit,
            'sum_net':    sum_net,
            'rows_count': agg['rows_count'] or 0,
            'unique_orders': unique_orders_count,
        },
        'per_lab': list(per_lab),
        'per_stage': list(per_stage),
        'monthly': list(monthly),
        'rows': rows,
    }
    return render(request, 'core/digital_lab_report.html', context)





























