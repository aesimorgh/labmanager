from django.urls import path
from .views import (
    InvoiceCreateDraftView,
    InvoiceDetailView,
    InvoiceIssueView,
    InvoiceDeleteDraftView,
    InvoicePrintView,
    # جدیدها:
    DoctorAccountView,          # NEW
    DoctorPaymentCreateView,    # NEW
)

app_name = "billing"

urlpatterns = [
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

    # حساب دکتر (اگر قبلاً اضافه نشده بود)
    path("doctor/<int:doctor_id>/account/", DoctorAccountView.as_view(), name="doctor_account"),  # NEW

    # ثبت پرداخت جدید برای دکتر (گام ۲.۳)
    path("doctor/<int:doctor_id>/payments/create/", DoctorPaymentCreateView.as_view(), name="doctor_payment_create"),  # NEW
]




