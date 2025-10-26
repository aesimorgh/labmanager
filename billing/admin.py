from decimal import Decimal
from django.contrib import admin
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django import forms
from decimal import Decimal, InvalidOperation
from .models import Invoice, InvoiceLine, DoctorPayment, PaymentAllocation, Expense

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
    list_display = ('doctor', 'date', 'amount', 'method', 'note', 'created_at', 'allocation_status')
    list_filter = ('method', 'date', 'created_at', 'allocation_status')
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


# --- Expense admin (ساده و سازگار با مدل) ---
from django import forms
from django.contrib import admin
from decimal import Decimal, InvalidOperation

from .models import Expense  # مطمئن شو همین ایمپورت موجود است

class ExpenseAdminForm(forms.ModelForm):
    # ورودی مبلغ را متنی می‌کنیم تا فلش بالا/پایین حذف شود و ارقام فارسی/ویرگول پذیرفته شود
    amount = forms.CharField(
        label='مبلغ',
        widget=forms.TextInput(attrs={
            'dir': 'ltr',
            'inputmode': 'decimal',
            'placeholder': 'مثلاً ۲,۵۰۰,۰۰۰'
        })
    )

    class Meta:
        model = Expense
        fields = '__all__'
        widgets = {
            'date': admin.widgets.AdminDateWidget(),
        }

    def clean_amount(self):
        s = str(self.cleaned_data.get('amount', '')).strip()
        # تبدیل ارقام فارسی/عربی + حذف جداکننده هزارگان
        trans = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٬،', '0123456789' + '0123456789' + ',,')
        s = s.translate(trans).replace(',', '')
        if not s:
            raise forms.ValidationError('مبلغ را وارد کنید.')
        try:
            val = Decimal(s)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError('مبلغ نامعتبر است.')
        if val <= 0:
            raise forms.ValidationError('مبلغ باید بزرگ‌تر از صفر باشد.')
        return val

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    form = ExpenseAdminForm

    list_display  = ('date', 'category', 'amount_fa', 'note', 'created_at')
    list_filter   = ('category', 'date', 'created_at')
    search_fields = ('note',)
    date_hierarchy = 'date'
    ordering = ('-date', '-id')

    # نمایش مبلغ با جداکننده هزارگان و ارقام فارسی (مرتب‌سازی همچنان بر اساس فیلد amount)
    def amount_fa(self, obj):
        s = f'{obj.amount:,.2f}'
        s = (s.replace('0','۰').replace('1','۱').replace('2','۲').replace('3','۳').replace('4','۴')
               .replace('5','۵').replace('6','۶').replace('7','۷').replace('8','۸').replace('9','۹'))
        s = s.replace('.', '٫')
        return s
    amount_fa.short_description = 'مبلغ'
    amount_fa.admin_order_field = 'amount'


# =====================[ Inventory Admin ]=====================
from .models import MaterialItem, MaterialLot, StockMovement, BOMRecipe, StockIssue
from django import forms  # اگر قبلاً نیست، همین بالا اضافه کن

class MaterialLotAdminForm(forms.ModelForm):
    class Meta:
        model = MaterialLot
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        item = cleaned.get('item')
        shade = (cleaned.get('shade_code') or '').strip()
        # اگر آیتم رنگ‌دار است، رنگ الزامی است
        if item and getattr(item, 'shade_enabled', False) and not shade:
            raise forms.ValidationError("برای آیتم‌های رنگ‌دار، وارد کردن رنگ (Shade) الزامی است.")
        return cleaned


@admin.register(MaterialItem)
class MaterialItemAdmin(admin.ModelAdmin):
    list_display  = ('code', 'name', 'item_type', 'category', 'uom', 'shade_enabled', 'pack_size', 'min_stock', 'is_active', 'updated_at')
    list_filter   = ('item_type', 'category', 'is_active', 'shade_enabled')
    search_fields = ('code', 'name', 'notes')
    ordering      = ('name', 'code')
    fieldsets = (
        (None, {
            'fields': ('code', 'name', 'item_type', 'category', 'uom', 'shade_enabled', 'is_active')
        }),
        ('انبار', {
            'fields': ('min_stock', 'shelf_life_days', 'pack_size', 'notes')
        }),
        ('وضعیت لحظه‌ای (فقط خواندنی)', {
            'fields': ('stock_qty', 'avg_unit_cost'),
            'classes': ('collapse',),
        }),
        ('سیستمی', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    readonly_fields = ('created_at', 'updated_at', 'stock_qty', 'avg_unit_cost')


@admin.register(MaterialLot)
class MaterialLotAdmin(admin.ModelAdmin):
    form = MaterialLotAdminForm  # ⬅️ این خط اضافه شود
    list_display  = ('item', 'shade_code', 'lot_code', 'vendor', 'purchase_date', 'start_use_date', 'end_use_date', 'qty_in', 'unit_cost', 'currency', 'expire_date')
    list_filter   = ('purchase_date', 'expire_date', 'start_use_date', 'end_use_date', 'vendor', 'shade_code')
    search_fields = ('lot_code', 'vendor', 'invoice_no', 'notes', 'item__code', 'item__name')
    date_hierarchy = 'purchase_date'
    ordering      = ('-purchase_date', '-id')
    raw_id_fields = ('item',)
    fieldsets = (
        (None, {
            'fields': ('item', 'shade_code', 'lot_code', 'vendor', ('purchase_date', 'expire_date'))
        }),
        ('بازه مصرف', {
            'fields': (('start_use_date', 'end_use_date'),),
        }),
        ('مقادیر/قیمت', {
            'fields': (('qty_in', 'unit_cost', 'currency'),)
        }),
        ('سند خرید', {
            'fields': ('invoice_no', 'attachment', 'notes')
        }),
        ('سیستمی', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )
    readonly_fields = ('created_at',)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display  = ('movement_type', 'item', 'lot', 'qty', 'unit_cost_effective', 'happened_at', 'order', 'product_code')
    list_filter   = ('movement_type', 'happened_at')
    search_fields = ('item__code', 'item__name', 'lot__lot_code', 'order__id', 'product_code', 'reason', 'created_by')
    date_hierarchy = 'happened_at'
    ordering      = ('-happened_at', '-id')
    raw_id_fields = ('item', 'lot', 'order')


@admin.register(BOMRecipe)
class BOMRecipeAdmin(admin.ModelAdmin):
    list_display  = ('product', 'item', 'qty_per_unit', 'waste_factor', 'is_active', 'updated_at')
    list_filter   = ('is_active', 'product')
    search_fields = ('product__code', 'product__name', 'item__code', 'item__name')
    ordering      = ('product', 'item')
    raw_id_fields = ('product', 'item')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(StockIssue)
class StockIssueAdmin(admin.ModelAdmin):
    list_display  = ('order', 'item', 'qty_issued', 'happened_at', 'created_at')
    list_filter   = ('happened_at',)
    search_fields = ('order__id', 'item__code', 'item__name', 'comment')
    date_hierarchy = 'happened_at'
    ordering      = ('-happened_at', '-id')
    raw_id_fields = ('order', 'item',)
    filter_horizontal = ('linked_moves',)


from .models import Equipment, Repair

@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display  = ('code', 'name', 'category', 'purchase_cost', 'useful_life_m',
                     'monthly_dep_display', 'book_value_display', 'is_active')
    list_filter   = ('category', 'is_active')
    search_fields = ('code', 'name', 'model', 'serial_no', 'vendor', 'location', 'note')
    ordering      = ('name', 'code')
    readonly_fields = ('created_at',)
    fieldsets = (
        (None, {
            'fields': ('code', 'name', 'category', 'is_active', 'location')
        }),
        ('مشخصات', {
            'fields': ('model', 'serial_no', 'vendor')
        }),
        ('مالی / استهلاک', {
            'fields': ('purchase_date', 'purchase_cost', 'salvage_value', 'useful_life_m', 'start_use_date', 'estimated_value')
        }),
        ('ضمیمه/یادداشت', {
            'fields': ('attachment', 'note')
        }),
        ('سیستمی', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )

    def monthly_dep_display(self, obj):
        return f"{obj.monthly_depreciation():,.0f}"
    monthly_dep_display.short_description = "استهلاک ماهانه"

    def book_value_display(self, obj):
        return f"{obj.book_value():,.0f}"
    book_value_display.short_description = "ارزش دفتری"

@admin.register(Repair)
class RepairAdmin(admin.ModelAdmin):
    list_display  = ('equipment', 'title', 'amount', 'occurred_date', 'paid_date', 'payment_method')
    list_filter   = ('payment_method', 'occurred_date', 'paid_date', 'equipment__category')
    search_fields = ('title', 'vendor', 'note', 'equipment__code', 'equipment__name', 'equipment__model', 'equipment__serial_no')
    ordering      = ('-occurred_date', '-id')
    raw_id_fields = ('equipment',)
    readonly_fields = ('created_at',)
