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

import xlsxwriter
from weasyprint import HTML

try:
    import jdatetime
except ImportError:
    jdatetime = None

from .forms import OrderForm, OrderEventForm
from .models import Order, OrderEvent


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


# ============================
# صفحه اصلی / ثبت سفارش
# ============================
def home(request):
    # فرم ثبت سفارش
    order_form = OrderForm(request.POST or None, prefix='order')
    if request.method == "POST":
        if order_form.is_valid():
            order_form.save()
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
    order = get_object_or_404(Order, pk=order_id)
    events = order.events.all().order_by('happened_at', 'id')
    form = OrderEventForm()
    context = {
        'order': order,
        'events': events,
        'event_form': form,
    }
    return render(request, 'core/order_detail.html', context)


def add_order_event(request, order_id):
    order = get_object_or_404(Order, pk=order_id)

    if request.method == "POST":
        form = OrderEventForm(request.POST, request.FILES)
        if form.is_valid():
            ev = form.save(commit=False)
            ev.order = order
            ev.save()

    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or '/'
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









































