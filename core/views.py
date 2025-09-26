from django.shortcuts import render, redirect
from django.db.models import Sum, F, Q
from django.http import HttpResponse
from django.template.loader import render_to_string
import io
import xlsxwriter
from weasyprint import HTML
from datetime import date

try:
    import jdatetime
except ImportError:
    jdatetime = None

from .forms import OrderForm
from .models import Order

# === JALALI NORMALIZE HELPERS ===
def _normalize_digits(s: str) -> str:
    if not s:
        return ""
    # فارسی و عربی → انگلیسی
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return s.translate(trans).strip()

def _normalize_for_jalali_field(s: str) -> str:
    # "۱۴۰۴/۰۶/۲۵" → "1404-06-25" (فرمت مورد انتظار django_jalali)
    s = _normalize_digits(s)
    return s.replace("/", "-")
# === /JALALI NORMALIZE HELPERS ===

def _jalali_to_gregorian_date(s: str):
    """
    '۱۴۰۴/۰۶/۱۹' یا '1404/06/19' → datetime.date (میلادی)
    اگر خالی/نامعتبر بود: None
    """
    if not s:
        return None
    # ارقام فارسی/عربی → انگلیسی و یکدست‌سازی جداکننده
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    s = s.translate(trans).strip().replace("-", "/")
    if not jdatetime:
        return None
    try:
        jy, jm, jd = [int(x) for x in s.split("/")]
        g = jdatetime.date(jy, jm, jd).togregorian()
        return date(g.year, g.month, g.day)
    except Exception:
        return None


# -----------------------------
# صفحه اصلی / ثبت سفارش
# -----------------------------
def home(request):
    if request.method == "POST":
        # یک کپی از POST بگیر تا قابل‌ویرایش باشد
        data = request.POST.copy()

        # چون فرم prefix='order' دارد، کلیدهای فیلدها این‌اند:
        # order-order_date  و  order-due_date
        for key in ("order-order_date", "order-due_date"):
            if key in data:
                # "۱۴۰۴/۰۷/۰۵" → "1404-07-05" (ارقام انگلیسی + / به -)
                data[key] = _normalize_for_jalali_field(data.get(key, ""))

        # فرم را با داده‌ی نرمال‌شده بساز
        order_form = OrderForm(data, prefix='order')

        if order_form.is_valid():
            order_form.save()
            return redirect('core:home')
    else:
        # GET
        order_form = OrderForm(prefix='order')

    orders = Order.objects.all().order_by('-created_at')

    context = {
        'order_form': order_form,
        'orders': orders,
    }
    return render(request, 'core/home.html', context)



# -----------------------------
# گزارش مالی / حسابداری
# -----------------------------
def accounting_report(request):
    doctor    = request.GET.get('doctor', '').strip()
    start_raw = request.GET.get('start_date', '').strip()  # مثل "۱۴۰۴/۰۶/۱۹"
    end_raw   = request.GET.get('end_date', '').strip()    # مثل "۱۴۰۴/۰۷/۰۵"

    # برای due_date (jDateField): جلالی نرمال با خط‌تیره (رشته)
    start_j = _normalize_for_jalali_field(start_raw)  # "1404-06-19" یا ""
    end_j   = _normalize_for_jalali_field(end_raw)    # "1404-07-05" یا ""

    # برای created_at__date (میلادی): تبدیل جلالی → میلادی
    start_g = _jalali_to_gregorian_date(start_raw)    # datetime.date یا None
    end_g   = _jalali_to_gregorian_date(end_raw)      # datetime.date یا None

    # از base_manager تا چیزی پنهان نشود
    orders = Order._base_manager.all().order_by('-id')

    # فیلتر پزشک
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
    # اگر هیچ تاریخ نداشتیم: فیلتر تاریخ نزن (همه می‌آیند)

    # جمع مبلغ کل (از property مدل)
    total_invoice = sum((o.total_price or 0) for o in orders)

    # لیست پزشک‌ها برای دراپ‌داون
    doctors = (Order._base_manager
               .exclude(doctor__isnull=True).exclude(doctor='')
               .values_list('doctor', flat=True).distinct().order_by('doctor'))

    context = {
        'orders': orders,
        'total_invoice': total_invoice,
        'doctor': doctor,
        'start_date': start_raw,  # همان که کاربر دیده/وارد کرده
        'end_date': end_raw,
        'doctors': doctors,
    }

    # -----------------------------
    # Export Excel
    # -----------------------------
    if 'export_excel' in request.GET:
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet("گزارش مالی")

        headers = ['ID','بیمار','پزشک','نوع سفارش','تعداد واحد','قیمت واحد','قیمت کل','تاریخ تحویل','تاریخ ثبت']
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
        resp = HttpResponse(output.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp['Content-Disposition'] = 'attachment; filename=accounting_report.xlsx'
        return resp

    # -----------------------------
    # Export PDF
    # -----------------------------
    if 'export_pdf' in request.GET:
        html = render_to_string('core/accounting_report_pdf.html', context)
        pdf = HTML(string=html).write_pdf()
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = 'attachment; filename=accounting_report.pdf'
        return resp

    return render(request, 'core/accounting_report.html', context)




























