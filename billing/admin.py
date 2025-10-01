from decimal import Decimal
from django.contrib import admin
from django.db.models import Sum
from django.db.models.functions import Coalesce

from .models import Invoice, InvoiceLine, DoctorPayment, PaymentAllocation

# LabProfile اختیاری: اگر مدل وجود دارد، در ادمین ثبت می‌کنیم
try:
    from .models import LabProfile
except Exception:
    LabProfile = None


# ----- Inlines -----
class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    fields = ('order', 'description', 'unit_count', 'unit_price', 'discount_amount', 'line_total')
    raw_id_fields = ('order',)


class PaymentAllocationInlineForInvoice(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    fk_name = 'invoice'
    fields = ('payment', 'amount_allocated',)
    raw_id_fields = ('payment',)


class PaymentAllocationInlineForPayment(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    fk_name = 'payment'
    fields = ('invoice', 'amount_allocated',)
    raw_id_fields = ('invoice',)


# ----- Admins -----
@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'doctor', 'status', 'issued_at',
        'subtotal', 'previous_balance', 'payments_applied', 'grand_total', 'amount_due',
        'paid_allocations', 'final_due_preview',
        'created_at',
    )
    list_filter = ('status', 'issued_at', 'created_at')
    search_fields = ('code', 'doctor__name')
    date_hierarchy = 'issued_at'
    inlines = [InvoiceLineInline, PaymentAllocationInlineForInvoice]
    raw_id_fields = ('doctor',)
    fieldsets = (
        (None, {
            'fields': (
                'doctor', 'code', 'status', 'issued_at',
                ('period_from', 'period_to'),
                ('subtotal', 'previous_balance', 'payments_applied', 'grand_total', 'amount_due'),
                'notes',
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    readonly_fields = ('created_at', 'updated_at')

    # مجموع تخصیص‌های پرداختی روی این فاکتور
    def paid_allocations(self, obj):
        val = obj.allocations.aggregate(
            s=Coalesce(Sum('amount_allocated'), Decimal('0'))
        )['s'] or Decimal('0')
        return val
    paid_allocations.short_description = 'پرداخت‌های تخصیصی'

    # پیش‌نمایش مانده نهایی (فقط برای دید بهتر در ادمین)
    def final_due_preview(self, obj):
        paid = self.paid_allocations(obj) or Decimal('0')
        prev = obj.previous_balance or Decimal('0')
        grand = obj.grand_total or Decimal('0')
        return grand - paid + prev
    final_due_preview.short_description = 'ماندهٔ نهایی (پیش‌نمایش)'


@admin.register(InvoiceLine)
class InvoiceLineAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'order', 'unit_count', 'unit_price', 'discount_amount', 'line_total', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('invoice__code', 'order__id')
    raw_id_fields = ('invoice', 'order')


@admin.register(DoctorPayment)
class DoctorPaymentAdmin(admin.ModelAdmin):
    list_display = ('doctor', 'date', 'amount', 'method', 'note', 'created_at')
    list_filter = ('method', 'date', 'created_at')
    search_fields = ('doctor__name', 'note')
    date_hierarchy = 'date'
    inlines = [PaymentAllocationInlineForPayment]
    raw_id_fields = ('doctor',)


@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(admin.ModelAdmin):
    list_display = ('payment', 'invoice', 'amount_allocated', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('payment__doctor__name', 'invoice__code')
    raw_id_fields = ('payment', 'invoice')


# ----- LabProfile (اختیاری) -----
if LabProfile:
    @admin.register(LabProfile)
    class LabProfileAdmin(admin.ModelAdmin):
        list_display = ('name', 'updated_at')
        fieldsets = (
            ('برند', {
                'fields': ('name', 'slogan', 'logo_file', 'logo_static_path'),
            }),
            ('اطلاعات بانکی', {
                'fields': ('card_no', 'iban', 'account_name'),
            }),
            ('سیستمی', {
                'fields': ('updated_at',),
                'classes': ('collapse',),
            }),
        )
        readonly_fields = ('updated_at',)

        # فقط اجازهٔ ساخت یک رکورد بده
        def has_add_permission(self, request):
            return LabProfile.objects.count() < 1

        # حذف را غیرفعال کن (اختیاری)
        def has_delete_permission(self, request, obj=None):
            return False


