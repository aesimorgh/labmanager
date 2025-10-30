from decimal import Decimal
from django.contrib import admin
from django.contrib import messages
from billing.services.lot_allocation import rollback_lot_allocation
from django.core.exceptions import ValidationError
from .models import StageDefault
from core.models import StageTemplate
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django import forms
from decimal import Decimal, InvalidOperation
from .models import Invoice, InvoiceLine, DoctorPayment, PaymentAllocation, Expense
from django.contrib.admin.widgets import FilteredSelectMultiple
from billing.services.lot_allocation import simulate_lot_allocation
from .models import ManualStockIssue

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
    list_display  = ('item', 'shade_code', 'lot_code', 'vendor', 'purchase_date', 'start_use_date', 'end_use_date', 'allocated', 'allocated_at', 'qty_in', 'unit_cost', 'currency', 'expire_date')
    list_filter   = ('purchase_date', 'expire_date', 'start_use_date', 'end_use_date', 'vendor', 'shade_code', 'allocated')
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
        ('وضعیت تخصیص', {
            'fields': (('allocated', 'allocated_at'),),
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
    readonly_fields = ('created_at', 'allocated', 'allocated_at')
    
        # --- اکشن بستن لات و تخصیص خودکار ---
    actions = ['action_allocate_lot_usage', "action_rollback_lot",]

    def action_allocate_lot_usage(self, request, queryset):
        """
        اکشن ادمین: بستن لات و تخصیص خودکار مصرف متریال.
        """
        from billing.services.lot_allocation import allocate_lot_usage
        success_count = 0
        messages = []
        for lot in queryset:
            try:
                result = allocate_lot_usage(lot.id)
                r = result.get("result", {})
                msg = (
                    f"لات {lot.id} → کلید {r.get('stage_key')} | "
                    f"تعداد سفارش‌ها: {r.get('orders_count')} | "
                    f"میانگین هر واحد: {r.get('per_unit_avg')} | "
                    f"جمع تخصیص: {result.get('allocated_qty_sum')} / "
                    f"کل لات: {result.get('lot_qty_in')}"
                )
                messages.append(msg)
                success_count += 1
            except Exception as e:
                messages.append(f"لات {lot.id}: خطا → {e}")
        if success_count:
            self.message_user(request, f"{success_count} لات با موفقیت تخصیص یافت.")
        for m in messages:
            self.message_user(request, m)

    action_allocate_lot_usage.short_description = "بستن لات و تخصیص خودکار مصرف متریال"

    def action_rollback_lot(self, request, queryset):
        """
        ادمین ▶ لغو تخصیص لات
        """
        ok = 0
        none = 0
        errs = 0
        for lot in queryset:
            try:
                res = rollback_lot_allocation(lot.id)
                if res.get("ok"):
                    ok += 1
                else:
                    none += 1
                    messages.info(request, f"لات {lot.id}: {res.get('msg', 'عدم تخصیص فعال')}")
            except ValidationError as ve:
                errs += 1
                messages.error(request, f"لات {lot.id}: خطا → {ve}")
            except Exception as e:
                errs += 1
                messages.error(request, f"لات {lot.id}: خطای غیرمنتظره → {e}")
        if ok:
            messages.success(request, f"لغو تخصیص برای {ok} لات انجام شد.")
        if none:
            messages.info(request, f"{none} لات تخصیص فعالی نداشت.")
        if errs:
            messages.error(request, f"{errs} مورد ناموفق بود.")
    action_rollback_lot.short_description = "لغو تخصیص لات"

    
    # اکشن پیشنمایش بدون ذخیره‌سازی
    def action_simulate_lot(self, request, queryset, *args, **kwargs):
        """
        ادمین ▶ پیشنمایش تخصیص (Dry-Run)
        هیچ تغییری در دیتابیس انجام نمی‌شود؛ فقط گزارش می‌دهد.
        """
        from django.contrib import messages
        from billing.models import MaterialLot

        shown = 0
        for obj in queryset:
            # نرمال‌سازی: مطمئن شو lot آبجکت مدل است نه bytes/int
            lot = obj
            if not isinstance(lot, MaterialLot):
                try:
                    pk = int(lot if not isinstance(lot, bytes) else lot.decode())
                    lot = MaterialLot.objects.select_related('item').get(pk=pk)
                except Exception as e:
                    messages.error(request, f"شناسهٔ نامعتبر برای لات: {obj} → {e}")
                    continue

            try:
                res = simulate_lot_allocation(lot.id)
                if res.get("ok"):
                    stage_key = res.get("stage_key", "")
                    orders = res.get("orders_count", 0)
                    total_units = res.get("total_units", "0.000")
                    per_unit = res.get("per_unit_avg", "0.000")
                    alloc_sum = res.get("allocated_qty_sum", "0.000")
                    lot_qty = res.get("lot_qty_in", "0.000")
                    msg = (
                        f"لات {lot.id} · stage={stage_key} · سفارش‌ها={orders} · "
                        f"جمع واحد={total_units} · میانگین/واحد={per_unit} · "
                        f"پیشنهاد تخصیص={alloc_sum} از {lot_qty}"
                    )
                    messages.info(request, msg)
                    for w in (res.get("warnings") or []):
                        messages.warning(request, f"لات {lot.id}: {w}")
                    shown += 1
                else:
                    messages.error(request, f"لات {lot.id}: پیشنمایش ناموفق.")
            except Exception as e:
                messages.error(request, f"لات {lot.id}: خطا در پیشنمایش → {e}")

        if shown == 0:
            messages.error(request, "هیچ گزارشی برای پیشنمایش نمایش داده نشد.")
    action_simulate_lot.short_description = "پیشنمایش تخصیص (Dry-Run)"
    actions = ['action_allocate_lot_usage', 'action_rollback_lot', 'action_simulate_lot']

    


class StageDefaultAdminForm(forms.ModelForm):
    stage_key = forms.ChoiceField(
        label="کلید مشترک مرحله",
        choices=(),
        widget=forms.Select(attrs={'style': 'min-width: 260px;'})
    )


    class Meta:
        model = StageDefault
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        keys = (StageTemplate.objects
                .exclude(stage_key__isnull=True)
                .exclude(stage_key__exact="")
                .values_list('stage_key', flat=True)
                .distinct()
                .order_by('stage_key'))
        self.fields['stage_key'].choices = [(k, k) for k in keys]

    def clean_stage_key(self):
        val = self.cleaned_data['stage_key']
        if not StageTemplate.objects.filter(stage_key=val).exists():
            raise forms.ValidationError("این کلید در مراحل تولید تعریف نشده است.")
        return val


class StageDefaultBulkAddForm(StageDefaultAdminForm):
    # انتخاب چندمتریالی با ویجت دو ستونه (انتقالی)
    materials = forms.ModelMultipleChoiceField(
        queryset=MaterialItem.objects.filter(is_active=True),
        required=False,
        widget=FilteredSelectMultiple('متریال‌ها', is_stacked=False),
        label="متریال‌ها (انتخاب چندتایی)"
    )

    class Meta(StageDefaultAdminForm.Meta):
        # در حالت افزودن، به‌جای فیلد «material» تکی، از «materials» چندتایی استفاده می‌کنیم
        fields = ['stage_key', 'materials', 'shade_sensitive', 'is_active', 'note']


@admin.register(StageDefault)
class StageDefaultAdmin(admin.ModelAdmin):
    list_display  = ('stage_key', 'material', 'shade_sensitive', 'is_active', 'created_at')
    list_filter   = ('stage_key', 'shade_sensitive', 'is_active')
    search_fields = ('stage_key', 'material__name', 'material__code')
    ordering      = ('stage_key', 'material__name')
    autocomplete_fields = ('material',)
    form = StageDefaultAdminForm

    def get_form(self, request, obj=None, **kwargs):
        """
        در حالت افزودن (obj=None) فرمِ چندتایی را نشان بده؛
        در حالت ویرایش، همان فرم استاندارد تک‌متریالی باقی بماند.
        """
        if obj is None:
            return StageDefaultBulkAddForm
        return super().get_form(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        """
        اگر در حالت افزودن، چند متریال انتخاب شده باشد،
        برای هر کدام یک ردیف StageDefault می‌سازیم و از ذخیرهٔ default شیء صرف‌نظر می‌کنیم.
        """
        if not change and hasattr(form, 'cleaned_data') and form.cleaned_data.get('materials'):
            stage_key = form.cleaned_data['stage_key']
            shade_sensitive = form.cleaned_data.get('shade_sensitive', False)
            is_active = form.cleaned_data.get('is_active', True)
            note = form.cleaned_data.get('note', '')
            created = 0
            for m in form.cleaned_data['materials']:
                # از ایجاد ردیف تکراری جلوگیری می‌کنیم
                _, was_created = StageDefault.objects.get_or_create(
                    stage_key=stage_key,
                    material=m,
                    defaults={
                        'shade_sensitive': shade_sensitive,
                        'is_active': is_active,
                        'note': note,
                    }
                )
                if was_created:
                    created += 1
            self.message_user(request, f"{created} ردیف ثبت شد.")
            return  # از ذخیرهٔ obj پیش‌فرض صرف‌نظر می‌کنیم
        # در حالت ویرایش تک‌رکوردی
        return super().save_model(request, obj, form, change)


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

@admin.register(ManualStockIssue)
class ManualStockIssueAdmin(admin.ModelAdmin):
    """
    ادمین ویژه‌ی «مصرف دستی متریال»:
    - فقط رکوردهای movement_type='issue' را نشان می‌دهد
    - در فرم، نوع حرکت قفل است (issue) تا اشتباهاً چیزی دیگر ثبت نشود
    """
    list_display   = ('item', 'qty', 'unit_cost_effective', 'happened_at', 'order', 'product_code', 'reason')
    list_filter    = ('happened_at', 'item__item_type', 'item__category')
    search_fields  = ('item__code', 'item__name', 'order__id', 'product_code', 'reason')
    date_hierarchy = 'happened_at'
    ordering       = ('-happened_at', '-id')
    raw_id_fields  = ('item', 'lot', 'order')

    # فرم تمیز با بخش «هزینه/لات» اختیاری + نمایش نوع حرکت (قفل‌شده)
    fieldsets = (
        (None, {
            'fields': ('item', 'qty', 'happened_at', 'order', 'product_code', 'reason')
        }),
        ('هزینه / لات (اختیاری)', {
            'fields': ('unit_cost_effective', 'lot'),
        }),
        ('نوع حرکت', {
            'fields': ('movement_type',),
            'classes': ('collapse',),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(movement_type='issue')

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        # نوع حرکت را قفل می‌کنیم تا فقط issue بماند
        if 'movement_type' not in ro:
            ro.append('movement_type')
        return ro

    def save_model(self, request, obj, form, change):
        # هر رکوردی اینجاست «مصرف» است؛ نوع حرکت را تثبیت کن
        obj.movement_type = 'issue'
        super().save_model(request, obj, form, change)
