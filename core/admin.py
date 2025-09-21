# core/admin.py (نسخه اصلاح‌شده)
from decimal import Decimal
from django.contrib import admin
from django import forms
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.template.response import TemplateResponse
from jalali_date.admin import ModelAdminJalaliMixin
from .models import Patient, Order, Material, Accounting
from .forms import PatientForm, OrderForm, MaterialForm, AccountingForm


# -----------------------------
# Patient Admin
# -----------------------------
@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    form = PatientForm
    list_display = ['name', 'phone', 'email', 'created_at']
    search_fields = ['name', 'phone', 'email']


# -----------------------------
# Order Admin
# -----------------------------
@admin.register(Order)
class OrderAdmin(ModelAdminJalaliMixin, admin.ModelAdmin):
    form = OrderForm
    list_display = [
        'id', 'patient_name_display', 'doctor', 'order_type', 'unit_count',
        'serial_number', 'price', 'total_price_display', 'shade',
        'status', 'due_date', 'created_at'
    ]
    list_filter = ['status', 'due_date']
    search_fields = ['doctor', 'order_type', 'shade', 'serial_number']
    readonly_fields = ['total_price_display']

    def formfield_for_dbfield(self, db_field, **kwargs):
        if db_field.name == 'price':
            kwargs['widget'] = forms.TextInput(attrs={'dir': 'ltr'})
        return super().formfield_for_dbfield(db_field, **kwargs)

    def patient_name_display(self, obj):
        return obj.patient.name if obj.patient else getattr(obj, 'patient_name', '-')
    patient_name_display.short_description = 'نام بیمار'

    def total_price_display(self, obj):
        try:
            return obj.unit_count * obj.price
        except Exception:
            return None
    total_price_display.short_description = 'قیمت کل (تومان)'


# -----------------------------
# Material Admin
# -----------------------------
@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    form = MaterialForm
    list_display = ['name', 'quantity', 'unit']
    search_fields = ['name']


# -----------------------------
# Accounting Admin با گزارش مستقیم در ادمین
# -----------------------------
@admin.register(Accounting)
class AccountingAdmin(ModelAdminJalaliMixin, admin.ModelAdmin):
    form = AccountingForm
    list_display = ['order', 'amount', 'date', 'method', 'export_buttons']

    # مسیر سفارشی برای پنل گزارش مالی
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'report/',
                self.admin_site.admin_view(self.accounting_report_view),
                name='accounting_report_admin'
            ),
        ]
        return custom_urls + urls

    # صفحه گزارش مالی داخل ادمین
    def accounting_report_view(self, request):
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

        # **توجه**: نباید به property ای که فقط getter دارد مقدار اختصاص دهیم.
        # قبلاً این‌جا می‌گذاشتیم: order.total_price = ...
        # اکنون از خودِ property استفاده می‌کنیم و جمع کل را با Decimal محاسبه می‌کنیم.
        total_invoice = sum(
            (order.total_price for order in orders if order.total_price is not None),
            Decimal('0')
        )
        doctors = Order.objects.values_list('doctor', flat=True).distinct()

        context = dict(
            self.admin_site.each_context(request),
            orders=orders,
            total_invoice=total_invoice,
            doctor=doctor,
            start_date=start_date,
            end_date=end_date,
            doctors=doctors,
        )

        # Export Excel
        if 'export_excel' in request.GET:
            import io
            import xlsxwriter
            from django.http import HttpResponse

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
                # استفاده از property total_price (بدون نوشتن به آن)
                worksheet.write(row_num, 6, float(order.total_price or 0))
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

        # Export PDF
        if 'export_pdf' in request.GET:
            from django.template.loader import render_to_string
            from weasyprint import HTML
            from django.http import HttpResponse

            html_string = render_to_string('core/admin/accounting_report_admin.html', context)
            html = HTML(string=html_string)
            pdf = html.write_pdf()
            response = HttpResponse(pdf, content_type='application/pdf')
            response['Content-Disposition'] = 'attachment; filename="accounting_report.pdf"'
            return response

        return TemplateResponse(request, "core/admin/accounting_report_admin.html", context)

    # ریدایرکت به صفحه گزارش مالی وقتی روی Add یا لیست کلیک شد
    def changelist_view(self, request, extra_context=None):
        return HttpResponseRedirect(reverse('admin:accounting_report_admin'))

    def add_view(self, request, form_url='', extra_context=None):
        return HttpResponseRedirect(reverse('admin:accounting_report_admin'))

    # دکمه‌های خروجی Excel و PDF
    def export_buttons(self, obj):
        excel_url = reverse('admin:accounting_report_admin') + f"?export_excel=1&doctor={obj.order.doctor}"
        pdf_url = reverse('admin:accounting_report_admin') + f"?export_pdf=1&doctor={obj.order.doctor}"
        return format_html(
            '<a class="btn btn-outline-success btn-sm" href="{}" target="_blank">💾 Excel</a> '
            '<a class="btn btn-outline-danger btn-sm" href="{}" target="_blank">📄 PDF</a>',
            excel_url, pdf_url
        )
    export_buttons.short_description = 'خروجی‌ها'
    export_buttons.allow_tags = True


# -----------------------------
# عنوان‌های پنل ادمین
# -----------------------------
admin.site.site_header = "مدیریت لابراتوار"
admin.site.index_title = "داشبورد مدیریت"
admin.site.site_title = "مدیریت لابراتوار"


































































