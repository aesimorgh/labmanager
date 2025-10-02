from django.urls import path
from django.views.generic import TemplateView  # برای برخی صفحات ساده مثل هاب گزارش‌ها

from .views import (
    # فاکتورها
    InvoiceCreateDraftView,
    InvoiceDetailView,
    InvoiceIssueView,
    InvoiceDeleteDraftView,
    InvoicePrintView,

    # حساب دکتر
    DoctorAccountView,
    DoctorPaymentCreateView,
    DoctorListView,

    # لیست فاکتورها
    InvoiceListView,

    # گزارش‌ها
    OpenInvoicesReportView,
    MonthlySalesReportView,
    DiscountsReportView,
    AgingReportView,

    # هاب مالی (ویوی جدید)
    FinancialHomeView,
)

app_name = "billing"

urlpatterns = [
    # ✅ ریشهٔ مالی: هاب مالی (اکنون به ویوی واقعی وصل شد)
    path("", FinancialHomeView.as_view(), name="financial_home"),

    # ✅ لیست فاکتورها
    path("invoices/", InvoiceListView.as_view(), name="invoice_list"),

    # ایجاد پیش‌نویس فاکتور
    path("invoices/create/", InvoiceCreateDraftView.as_view(), name="invoice_create_draft"),

    # جزئیات فاکتور
    path("invoices/<int:pk>/", InvoiceDetailView.as_view(), name="invoice_detail"),

    # صدور (Issue) فاکتور
    path("invoices/<int:pk>/issue/", InvoiceIssueView.as_view(), name="invoice_issue"),

    # حذف پیش‌نویس (فقط Draft)
    path("invoices/<int:pk>/delete/", InvoiceDeleteDraftView.as_view(), name="invoice_delete_draft"),

    # نمای چاپ (Print-friendly)
    path("invoices/<int:pk>/print/", InvoicePrintView.as_view(), name="invoice_print"),

    # ✅ لیست پزشک‌ها (برای رفتن به حساب دکتر)
    path("doctors/", DoctorListView.as_view(), name="doctor_list"),

    # ✅ گزارش‌ها (صفحهٔ هاب گزارش‌ها داخل پنل)
    path("reports/", TemplateView.as_view(template_name="billing/reports_home.html"), name="reports_home"),

    # ✅ گزارش مطالبات باز
    path("reports/open/", OpenInvoicesReportView.as_view(), name="report_open_invoices"),

    # ✅ گزارش فروش ماهانه
    path("reports/sales/", MonthlySalesReportView.as_view(), name="report_monthly_sales"),

    # ✅ گزارش مجموع تخفیف‌ها
    path("reports/discounts/", DiscountsReportView.as_view(), name="report_discounts"),

    # ✅ گزارش Aging بدهی‌ها (اتصال به ویوی واقعی)
    path("reports/aging/", AgingReportView.as_view(), name="report_aging"),

    # حساب دکتر
    path("doctor/<int:doctor_id>/account/", DoctorAccountView.as_view(), name="doctor_account"),  # مسیر اصلی
    path("doctor/<int:doctor_id>/", DoctorAccountView.as_view(), name="doctor_account_short"),    # میانبر سازگار با لینک‌های قدیمی

    # ثبت پرداخت جدید برای دکتر
    path("doctor/<int:doctor_id>/payments/create/", DoctorPaymentCreateView.as_view(), name="doctor_payment_create"),
]













