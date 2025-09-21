from django.shortcuts import render, redirect
from django.db.models import Sum, F
from django.http import HttpResponse
from django.template.loader import render_to_string
import io
import xlsxwriter
from weasyprint import HTML

from .forms import OrderForm
from .models import Order

# -----------------------------
# صفحه اصلی / ثبت سفارش
# -----------------------------
def home(request):
    order_form = OrderForm(request.POST or None, prefix='order')

    if request.method == "POST":
        if order_form.is_valid():
            order_form.save()
            return redirect('core:home')

    orders = Order.objects.all().order_by('-created_at')

    # ❌ حذف محاسبه مستقیم روی property
    # for order in orders:
    #     order.total_price = (order.unit_count or 0) * (order.price or 0)

    context = {
        'order_form': order_form,
        'orders': orders,
    }
    return render(request, 'core/home.html', context)


# -----------------------------
# گزارش مالی / حسابداری
# -----------------------------
def accounting_report(request):
    doctor = request.GET.get('doctor', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')

    orders = Order.objects.all()

    if doctor:
        orders = orders.filter(doctor__icontains=doctor)
    if start_date:
        orders = orders.filter(due_date__gte=start_date)
    if end_date:
        orders = orders.filter(due_date__lte=end_date)

    # ❌ حذف محاسبه مستقیم روی property
    # for order in orders:
    #     order.total_price = (order.unit_count or 0) * (order.price or 0)

    # جمع مبلغ کل فاکتور با استفاده از property
    total_invoice = sum(order.total_price for order in orders if order.total_price is not None)

    # لیست دکترها برای dropdown
    doctors = Order.objects.values_list('doctor', flat=True).distinct()

    context = {
        'orders': orders,
        'total_invoice': total_invoice,
        'doctor': doctor,
        'start_date': start_date,
        'end_date': end_date,
        'doctors': doctors,
    }

    # -----------------------------
    # Export Excel
    # -----------------------------
    if 'export_excel' in request.GET:
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet("گزارش مالی")

        headers = ['ID', 'بیمار', 'پزشک', 'نوع سفارش', 'تعداد واحد', 'قیمت واحد', 'قیمت کل', 'تاریخ تحویل', 'تاریخ ثبت']
        for col_num, header in enumerate(headers):
            worksheet.write(0, col_num, header)

        for row_num, order in enumerate(orders, start=1):
            worksheet.write(row_num, 0, order.id)
            worksheet.write(row_num, 1, order.patient_name)
            worksheet.write(row_num, 2, order.doctor)
            worksheet.write(row_num, 3, order.get_order_type_display())
            worksheet.write(row_num, 4, order.unit_count)
            worksheet.write(row_num, 5, float(order.price or 0))
            worksheet.write(row_num, 6, float(order.total_price or 0))  # ✅ استفاده از property
            worksheet.write(row_num, 7, str(order.due_date))
            worksheet.write(row_num, 8, order.created_at.strftime("%Y/%m/%d %H:%M"))

        workbook.close()
        output.seek(0)
        response = HttpResponse(
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response['Content-Disposition'] = 'attachment; filename=accounting_report.xlsx'
        return response

    # -----------------------------
    # Export PDF
    # -----------------------------
    if 'export_pdf' in request.GET:
        html_string = render_to_string('core/accounting_report_pdf.html', context)
        html = HTML(string=html_string)
        pdf = html.write_pdf()
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="accounting_report.pdf"'
        return response

    return render(request, 'core/accounting_report.html', context)
























