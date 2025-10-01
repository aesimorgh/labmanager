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

































