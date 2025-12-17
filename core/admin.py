# core/admin.py (تا قبل از OrderAdmin)

from decimal import Decimal
from django.contrib import admin
from django import forms
from django.apps import apps as _apps
# تلاش برای یافتن مدل از billing یا core (هرکدام که موجود بود)
DigitalLabTransfer = None
for _app_label in ('billing', 'core'):
    try:
        DigitalLabTransfer = _apps.get_model(_app_label, 'DigitalLabTransfer')
        if DigitalLabTransfer:
            break
    except Exception:
        pass
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.template.response import TemplateResponse
from jalali_date.admin import ModelAdminJalaliMixin
from jalali_date import date2jalali, datetime2jalali
import jdatetime
from django.contrib import messages
from django.utils import timezone
from .models import Accounting
from django.db import models as dj_models
from django.db.models.fields import DateTimeField
from django.db.models import Sum, F, DecimalField, ExpressionWrapper, Value
from django.db.models.functions import Coalesce, Cast
from django.forms.models import BaseInlineFormSet
from jalali_date.fields import JalaliDateField
from jalali_date.widgets import AdminJalaliDateWidget
from core.utils.normalizers import normalize_jalali_date_str
from django.utils.dateparse import parse_date
from .models import Patient, Order, Material, Accounting
from .models import StageInstance
from .forms import PatientForm, OrderForm, MaterialForm, AccountingForm
from .models import OrderEvent, ProductionEvent
from .models import Product, StageTemplate

# کمکی: تبدیل رقم‌های فارسی/عربی به انگلیسی + فرمت «فارسی با جداکننده»
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

def money_fa_py(value):
    """برمی‌گرداند مثل: ۱٬۲۳۴٬۵۶۷ (جداکنندهٔ فارسی + رقم‌های فارسی)"""
    if value is None or value == "":
        return ""
    try:
        n = int(float(value))
        s = f"{n:,}"  # جداکننده انگلیسی
        return s.replace(",", "٬").translate(FA_DIGITS)  # کامای فارسی + ارقام فارسی
    except Exception:
        return str(value)


# -----------------------------
# Patient Admin
# -----------------------------
@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    form = PatientForm
    list_display = ['name', 'phone', 'email', 'created_at']
    search_fields = ['name', 'phone', 'email']


# -----------------------------
# Filter: وضعیت تسویه (بدهکار/تسویه‌شده)
# -----------------------------
class BalanceStatusFilter(admin.SimpleListFilter):
    title = 'وضعیت تسویه'
    parameter_name = 'balance_status'

    def lookups(self, request, model_admin):
        return [
            ('debt', 'بدهکار (>۰)'),
            ('settled', 'تسویه‌شده (≤۰)'),
        ]

    def queryset(self, request, queryset):
        # Annotate برای محاسبه‌ی مانده (تمیز و سریع)
        zero_dec  = Value(0, output_field=DecimalField(max_digits=14, decimal_places=2))
        unit_dec  = Coalesce(Cast(F('unit_count'), DecimalField(max_digits=14, decimal_places=2)), zero_dec)
        price_dec = Coalesce(Cast(F('price'),      DecimalField(max_digits=14, decimal_places=2)), zero_dec)

        total_expr = ExpressionWrapper(unit_dec * price_dec, output_field=DecimalField(max_digits=14, decimal_places=2))
        qs = queryset.annotate(
            total   = total_expr,
            paid    = Coalesce(Sum('accounting__amount'), zero_dec),
            balance = ExpressionWrapper(F('total') - F('paid'), output_field=DecimalField(max_digits=14, decimal_places=2)),
        )

        val = self.value()
        if val == 'debt':
            return qs.filter(balance__gt=0)
        if val == 'settled':
            return qs.filter(balance__lte=0)
        return qs


# -----------------------------
# FormSet: جلوگیری از پرداختِ بیش از مانده در اینلاین پرداخت‌ها
# -----------------------------
class AccountingInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        order = self.instance  # سفارش والد

        # اگر سفارش هنوز ذخیره نشده، عبور
        if not getattr(order, 'pk', None):
            return

        total       = (order.unit_count or 0) * (order.price or 0)
        paid_in_db  = order.accounting_set.aggregate(s=Sum('amount'))['s'] or 0

        # جمع تغییرات همین نوبت (ایجاد/ویرایش/حذف)
        delta = 0
        for form in self.forms:
            if not hasattr(form, 'cleaned_data'):
                continue

            if form.cleaned_data.get('DELETE'):
                # اگر ردیف موجود حذف می‌شود، مبلغ قبلی را کم کن
                if form.instance.pk:
                    delta -= (form.instance.amount or 0)
                continue

            amount = form.cleaned_data.get('amount') or 0
            if form.instance.pk:
                # ردیف موجود که مبلغش تغییر کرده
                original = form.instance.amount or 0
                delta += (amount - original)
            else:
                # ردیف جدید
                delta += amount

        if paid_in_db + delta > total:
            raise forms.ValidationError("مجموع پرداخت‌ها از مبلغ سفارش بیشتر است. لطفاً مبالغ را اصلاح کنید.")

class AccountingInlineForm(forms.ModelForm):
    # ❶ ورودی را متنی کن تا اسپینر حذف شود و بتوان فارسی هم تایپ کرد
    amount = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'dir': 'ltr',
            'inputmode': 'decimal',   # کیبورد عددی موبایل
            'autocomplete': 'off',
            'style': 'width: 140px;',
            'placeholder': 'مثلاً ۱۲۳٬۴۵۶.۷۸'
        })
    )

    date = JalaliDateField(
        label='تاریخ پرداخت',
        widget=AdminJalaliDateWidget,
        required=False
    )

    class Meta:
        model = Accounting
        fields = ['amount', 'date', 'method']
        widgets = {
            # ❷ حتماً «amount» را از NumberInput به TextInput تغییر بده
            'date': AdminJalaliDateWidget(attrs={'class': 'jalali-date-field'}),
        }

    # ❸ تبدیل ارقام فارسی/عربی و حذف جداکننده‌ها برای ذخیره استاندارد
    def clean_amount(self):
        raw = (self.cleaned_data.get('amount') or '').strip()

        if raw == '':
            return None  # یا 0 اگر می‌خوای خالی‌ها صفر ذخیره شوند

        trans = str.maketrans({
            '۰':'0','۱':'1','۲':'2','۳':'3','۴':'4','۵':'5','۶':'6','۷':'7','۸':'8','۹':'9',
            '٠':'0','١':'1','٢':'2','٣':'3','٤':'4','٥':'5','٦':'6','٧':'7','٨':'8','٩':'9',
            '٬':'', ',':'', ' ':''
        })
        ascii_num = raw.translate(trans)

        from decimal import Decimal, InvalidOperation
        try:
            return Decimal(ascii_num)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError('لطفاً مبلغ معتبر وارد کنید.')




# -----------------------------
# Inline: پرداخت‌های سفارش (Accounting)
# -----------------------------
class AccountingInline(admin.TabularInline):
    model = Accounting
    form = AccountingInlineForm
    formset = AccountingInlineFormSet
    extra = 1
    fields = ['amount', 'date', 'method']


# -----------------------------
# Inline Form: تاریخ‌های جلالی برای StageInstance
# -----------------------------
class StageInstanceInlineForm(forms.ModelForm):
    planned_date = JalaliDateField(
        label='تاریخ برنامه',
        widget=AdminJalaliDateWidget,
        required=False
    )
    started_date = JalaliDateField(
        label='تاریخ شروع',
        widget=AdminJalaliDateWidget,
        required=False
    )
    done_date = JalaliDateField(
        label='تاریخ پایان',
        widget=AdminJalaliDateWidget,
        required=False
    )

    class Meta:
        model = StageInstance
        fields = ('order_index', 'label', 'status',
                  'planned_date', 'started_date', 'done_date', 'notes')
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

# -----------------------------
# Inline: مراحل سفارش (StageInstance)
# -----------------------------
class StageInstanceInline(admin.TabularInline):
    model = StageInstance
    form = StageInstanceInlineForm
    extra = 0
    fields = ('order_index', 'label', 'status', 'planned_date', 'started_date', 'done_date', 'notes')
    ordering = ('order_index', 'id')
    show_change_link = True

# -----------------------------
# Inline: رویدادهای سفارش (OrderEvent) با تاریخ جلالی
# -----------------------------
class OrderEventInlineForm(forms.ModelForm):
    happened_at = JalaliDateField(
        label='تاریخ رویداد',
        widget=AdminJalaliDateWidget,
        required=True
    )

    class Meta:
        model = OrderEvent
        fields = ['event_type', 'direction', 'happened_at', 'notes', 'attachment']
        widgets = {
            'happened_at': AdminJalaliDateWidget(attrs={'class': 'jalali-date-field'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }


class OrderEventInline(admin.TabularInline):
    model = OrderEvent
    form = OrderEventInlineForm
    extra = 0
    fields = ['event_type', 'direction', 'happened_at', 'notes', 'attachment']
    ordering = ('happened_at', 'id')
    show_change_link = True



# -----------------------------
# Order Admin (مرتب، قابل‌سورت، تاریخ‌های فارسی، دکمه‌ها)
# -----------------------------
@admin.register(Order)
class OrderAdmin(ModelAdminJalaliMixin, admin.ModelAdmin):
    form = OrderForm
    inlines = [AccountingInline, OrderEventInline, StageInstanceInline]
    actions = ['settle_balance_action', 'init_stages_from_template_action']

    # ناوبری و نظم
    date_hierarchy = 'created_at'
    list_per_page = 30
    ordering = ('-created_at',)
    list_select_related = ('patient',)
    empty_value_display = '—'
    search_help_text = 'جستجو بر اساس نام بیمار، پزشک، نوع سفارش، سریال یا ID'

    # ستون‌های لیست
    list_display = [
        'id',
        'patient_name_display',
        'doctor',
        'order_type',
        'unit_count',
        'serial_number',
        'teeth_fdi_display',   # ← دندان‌ها
        'total_price_fa',   # سورت‌شونده
        'paid_fa',          # سورت‌شونده
        'balance_badge',    # سورت‌شونده
        'status_badge',
        'due_date_fa',
        'order_date_fa',
        'edit_button',
        'settle_button',
        'undo_button',
    ]
    list_display_links = ('id', 'patient_name_display')
    list_filter = ['status', 'due_date', BalanceStatusFilter]
    search_fields = ['doctor', 'order_type', 'shade', 'serial_number', 'patient__name', 'id']
    readonly_fields = ['total_price_display']

    # استایل (بعداً فایل CSS رو اضافه می‌کنی)
    class Media:
        css = {'all': ('core/admin_order.css',)}
    
    def init_stages_from_template_action(self, request, queryset):
        created = skipped = missing = 0
        for o in queryset:
            code = (o.order_type or '').strip()
            if not code:
                missing += 1
                continue
            product = Product.objects.filter(code=code).first()
            if not product:
                # اگر کُد سفارش با کُد محصول یکی نیست، اینجا گیر می‌کند
                missing += 1
                continue

            templates = StageTemplate.objects.filter(product=product, is_active=True).order_by('order_index', 'id')

            for t in templates:
                # کلید یکتا بر اساس (order, key)؛ اگر قبلاً ساخته شده باشد، skip
                obj, was_created = StageInstance.objects.get_or_create(
                    order=o, key=t.key,
                    defaults=dict(
                        template=t,
                        label=t.label,
                        order_index=t.order_index,
                        # تاریخ‌های برنامه‌ریزی را فعلاً خالی می‌گذاریم؛ بعداً می‌تونیم auto-plan کنیم
                    )
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1

        level = messages.SUCCESS if created else (messages.WARNING if not missing else messages.ERROR)
        self.message_user(
            request,
            f"مرحله‌ها: ایجاد {created}، موجود/رد شده {skipped}، سفارش‌های بی‌محصول/کُد نامعتبر {missing}",
            level=level
        )
    init_stages_from_template_action.short_description = "ایجاد مراحل از قالب محصول"

    # ---------- get_queryset واحد و تمیز (لطفاً فقط همین یکی را نگه دار!) ----------
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('patient')

        zero_dec = Value(0, output_field=DecimalField(max_digits=14, decimal_places=2))
        unit_dec  = Coalesce(Cast(F('unit_count'), DecimalField(max_digits=14, decimal_places=2)), zero_dec)
        price_dec = Coalesce(Cast(F('price'),      DecimalField(max_digits=14, decimal_places=2)), zero_dec)

        total_expr = ExpressionWrapper(unit_dec * price_dec, output_field=DecimalField(max_digits=14, decimal_places=2))
        paid_expr  = Coalesce(Sum('accounting__amount'), zero_dec)

        qs = qs.annotate(
            total=total_expr,
            paid=paid_expr,
            balance=ExpressionWrapper(F('total') - F('paid'), output_field=DecimalField(max_digits=14, decimal_places=2))
        )
        return qs

    # ---------- Helperها ----------
    def _total_raw(self, obj):
        return getattr(obj, 'total', None) if getattr(obj, 'total', None) is not None else (obj.unit_count or 0) * (obj.price or 0)

    def _paid_raw(self, obj):
        val = getattr(obj, 'paid', None)
        if val is None:
            val = obj.accounting_set.aggregate(s=Sum('amount'))['s'] or 0
        return val

    # ---------- فرم (readonly) ----------
    @admin.display(description='قیمت کل (تومان)')
    def total_price_display(self, obj):
        return self._total_raw(obj)

    # ---------- نمایش لیست ----------
    @admin.display(description='نام بیمار')
    def patient_name_display(self, obj):
        return obj.patient.name if obj.patient else getattr(obj, 'patient_name', '—')

    @admin.display(description='قیمت کل (تومان)', ordering='total')
    def total_price_fa(self, obj):
        return money_fa_py(self._total_raw(obj))

    @admin.display(description='پرداخت‌شده (تومان)', ordering='paid')
    def paid_fa(self, obj):
        return money_fa_py(self._paid_raw(obj))

    @admin.display(description='مانده', ordering='balance')
    def balance_badge(self, obj):
        balance = self._total_raw(obj) - self._paid_raw(obj)
        bg = '#ef4444' if balance > 0 else '#16a34a'
        return format_html('<span class="lab-badge" style="background:{}">{}</span>', bg, money_fa_py(balance))

    @admin.display(description='وضعیت', ordering='status')
    def status_badge(self, obj):
        val = (getattr(obj, 'status', '') or '').lower()
        # برچسب انسانی بر اساس choices خود مدل
        try:
            label = dict(getattr(Order, 'STATUS_CHOICES', [])).get(val, val) or '—'
        except Exception:
            label = val or '—'
        # رنگ‌ها مطابق کلیدهای واقعی مدل Order
        color_map = {
            'pending':      '#0ea5e9',  # آبی
            'in_progress':  '#f59e0b',  # نارنجی
            'completed':    '#22c55e',  # سبز روشن
            'delivered':    '#16a34a',  # سبز
            'cancelled':    '#9ca3af',  # خاکستری
        }
        bg = color_map.get(val, '#64748b')
        return format_html('<span class="lab-badge" style="background:{}">{}</span>', bg, label)

    # ---------- تاریخ‌ها: جلالی + ارقام فارسی + / ----------
    @admin.display(description='تاریخ تحویل', ordering='due_date')
    def due_date_fa(self, obj):
        d = getattr(obj, 'due_date', None)
        if not d:
            return '—'
        try:
            # اگر فیلد از نوع jmodels.jDateField باشد، d از نوع jdatetime.date است و خودش جلالی است
            if isinstance(d, jdatetime.date):
                s = d.strftime('%Y/%m/%d')
            else:
                # در غیر اینصورت (date میلادی)، به جلالی تبدیل کن
                s = date2jalali(d).strftime('%Y/%m/%d')
            return s.translate(FA_DIGITS)
        except Exception:
            return str(d)

    @admin.display(description='تاریخ ثبت', ordering='order_date')
    def order_date_fa(self, obj):
        d = getattr(obj, 'order_date', None)
        if not d:
            return '—'
        try:
            # اگر jDateField است، خودش جلالی است
            import jdatetime
            if isinstance(d, jdatetime.date):
                s = d.strftime('%Y/%m/%d')
            else:
                from jalali_date import date2jalali
                s = date2jalali(d).strftime('%Y/%m/%d')
            return s.translate(FA_DIGITS)
        except Exception:
            return str(d)


    @admin.display(description='ویرایش')
    def edit_button(self, obj):
      url = reverse('admin:core_order_change', args=[obj.pk])
      return format_html(
        '<a style="display:inline-block;min-width:92px;text-align:center;'
        'padding:5px 10px;border:1px solid #1d4ed8;border-radius:9999px;'
        'background:#eef2ff;color:#1d4ed8;text-decoration:none;'
        'font-size:12px;line-height:1.2" href="{}">ویرایش</a>',
        url
    )

    @admin.display(description='تسویهٔ کامل')
    def settle_button(self, obj):
      url = reverse('admin:order_settle', args=[obj.pk])
      return format_html(
        '<a style="display:inline-block;min-width:92px;text-align:center;'
        'padding:5px 10px;border:1px solid #047857;border-radius:9999px;'
        'background:#ecfdf5;color:#047857;text-decoration:none;'
        'font-size:12px;line-height:1.2" href="{}">تسویهٔ کامل</a>',
        url
    )

    @admin.display(description='برگرداندن پرداخت')
    def undo_button(self, obj):
      url = reverse('admin:order_undo_last_payment', args=[obj.pk])
      return format_html(
        '<a style="display:inline-block;min-width:92px;text-align:center;'
        'padding:5px 10px;border:1px solid #b91c1c;border-radius:9999px;'
        'background:#fef2f2;color:#b91c1c;text-decoration:none;'
        'font-size:12px;line-height:1.2" href="{}">برگردان</a>',
        url
    )


    @admin.display(description='دندان‌ها')
    def teeth_fdi_display(self, obj):
        val = (getattr(obj, 'teeth_fdi', '') or '').strip()
        if not val:
            # fallback از notes اگر خواستی حفظ شود
            import re
            notes = getattr(obj, 'notes', '') or ''
            m = re.search(r'دندان‌ها\s*:\s*([0-9,\s،]+)', notes)
            if m:
                val = m.group(1).replace('،', ',')
        if not val:
            return '—'
        # نرمال‌سازی کوچک و ارقام فارسی
        val = ', '.join([p.strip() for p in val.split(',') if p.strip()])
        return val.translate(FA_DIGITS)


    # ---------- ستون «عملیات» ----------
    @admin.display(description='عملیات')
    def actions_column(self, obj):
        url_edit   = reverse('admin:core_order_change', args=[obj.pk])
        url_settle = reverse('admin:order_settle', args=[obj.pk])
        url_undo   = reverse('admin:order_undo_last_payment', args=[obj.pk])
        return format_html(
            '<div class="lab-actions">'
            '<a class="lab-btn lab-btn-blue" href="{}">ویرایش</a>'
            '<a class="lab-btn lab-btn-green" href="{}">تسویه</a>'
            '<a class="lab-btn lab-btn-red" href="{}">برگردان</a>'
            '</div>',
            url_edit, url_settle, url_undo
        )

    # ---------- اکشن گروهی: تسویه کامل ----------
    def settle_balance_action(self, request, queryset):
        ok = skipped = errs = 0
        method_value = None
        method_required = False
        date_required = False
        try:
            mf = Accounting._meta.get_field('method')
            choices = list(mf.choices or [])
            if choices:
                method_value = choices[0][0]
            method_required = (not getattr(mf, 'null', True)) and (not getattr(mf, 'blank', True))
        except Exception:
            pass
        try:
            df = Accounting._meta.get_field('date')
            date_required = (not getattr(df, 'null', True)) and (not getattr(df, 'blank', True))
        except Exception:
            pass

        for o in queryset:
            balance = (o.unit_count or 0) * (o.price or 0) - (o.accounting_set.aggregate(s=Sum('amount'))['s'] or 0)
            if balance <= 0:
                skipped += 1
                continue
            kwargs = {'order': o, 'amount': balance}
            if date_required:
                kwargs['date'] = timezone.now().date()
            if method_value is not None or method_required:
                kwargs['method'] = method_value or 'CASH'
            try:
                Accounting.objects.create(**kwargs)
                ok += 1
            except Exception:
                errs += 1

        msg = f"تسویهٔ خودکار: موفق {ok}، رد شده {skipped}، خطا {errs}"
        level = messages.SUCCESS if ok and not errs else (messages.WARNING if not errs else messages.ERROR)
        self.message_user(request, msg, level=level)

    settle_balance_action.short_description = "تسویهٔ کامل (ایجاد پرداخت به مبلغ مانده)"

    # ---------- URLهای اختصاصی دکمه‌ها ----------
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('settle/<int:order_id>/', self.admin_site.admin_view(self.settle_order_view), name='order_settle'),
            path('undo-last-payment/<int:order_id>/', self.admin_site.admin_view(self.undo_last_payment_view), name='order_undo_last_payment'),
        ]
        return custom + urls

    def settle_order_view(self, request, order_id):
        try:
            o = Order.objects.get(pk=order_id)
        except Order.DoesNotExist:
            self.message_user(request, "سفارش پیدا نشد.", level=messages.ERROR)
            return HttpResponseRedirect(reverse('admin:core_order_changelist'))

        balance = (o.unit_count or 0) * (o.price or 0) - (o.accounting_set.aggregate(s=Sum('amount'))['s'] or 0)
        if balance <= 0:
            self.message_user(request, "این سفارش مانده‌ای برای تسویه ندارد.", level=messages.WARNING)
            return HttpResponseRedirect(reverse('admin:core_order_changelist'))

        method_value = None
        method_required = False
        date_required = False
        try:
            mf = Accounting._meta.get_field('method')
            choices = list(mf.choices or [])
            if choices:
                method_value = choices[0][0]
            method_required = (not getattr(mf, 'null', True)) and (not getattr(mf, 'blank', True))
        except Exception:
            pass
        try:
            df = Accounting._meta.get_field('date')
            date_required = (not getattr(df, 'null', True)) and (not getattr(df, 'blank', True))
        except Exception:
            pass

        kwargs = {'order': o, 'amount': balance}
        if date_required:
            kwargs['date'] = timezone.now().date()
        if method_value is not None or method_required:
            kwargs['method'] = method_value or 'CASH'

        try:
            Accounting.objects.create(**kwargs)
            self.message_user(request, f"تسویه انجام شد: {money_fa_py(balance)} تومان.", level=messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"خطا در ثبت پرداخت: {e}", level=messages.ERROR)

        return HttpResponseRedirect(reverse('admin:core_order_changelist'))

    def undo_last_payment_view(self, request, order_id):
        try:
            o = Order.objects.get(pk=order_id)
        except Order.DoesNotExist:
            self.message_user(request, "سفارش پیدا نشد.", level=messages.ERROR)
            return HttpResponseRedirect(reverse('admin:core_order_changelist'))

        last_payment = Accounting.objects.filter(order=o).order_by('-id').first()
        if not last_payment:
            self.message_user(request, "پرداختی برای این سفارش ثبت نشده است.", level=messages.WARNING)
            return HttpResponseRedirect(reverse('admin:core_order_changelist'))

        last_payment.delete()
        self.message_user(request, "آخرین پرداخت حذف شد.", level=messages.SUCCESS)
        return HttpResponseRedirect(reverse('admin:core_order_changelist'))





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

    # صفحه گزارش مالی داخل ادمین (با خروجی Excel/PDF)
    def accounting_report_view(self, request):
        # --- normalize GET params for jalali dates ---
        data = request.GET.copy()  # make it mutable
        for key in ("start_date", "end_date"):
            if data.get(key):
                data[key] = normalize_jalali_date_str(data[key])
        # --- end normalize block ---

        doctor = data.get('doctor', '').strip()
        start_date_str = data.get('start_date', '').strip()
        end_date_str   = data.get('end_date', '').strip()

        # تبدیل قطعی جلالی به میلادی
        import jdatetime
        from django.db.models import DateTimeField as _DTF

        def jalali_to_gregorian(jalali_str):
            if not jalali_str:
                return None
            try:
                y, m, d = [int(p) for p in jalali_str.split('/')]
                return jdatetime.date(y, m, d).togregorian()  # -> datetime.date
            except Exception:
                return None

        start_date = jalali_to_gregorian(start_date_str)
        end_date   = jalali_to_gregorian(end_date_str)

        orders = Order.objects.all()

        # اگر doctor شما ForeignKey است و نام در فیلد name است، این خط را به doctor__name__icontains تغییر بده
        if doctor:
            orders = orders.filter(doctor__icontains=doctor)

        # اگر due_date از نوع DateTimeField باشد، روی part تاریخ فیلتر کنیم
        due_field = Order._meta.get_field("due_date")
        is_datetime = isinstance(due_field, _DTF)

        if start_date:
            if is_datetime:
                orders = orders.filter(due_date__date__gte=start_date)
            else:
                orders = orders.filter(due_date__gte=start_date)

        if end_date:
            if is_datetime:
                orders = orders.filter(due_date__date__lte=end_date)
            else:
                orders = orders.filter(due_date__lte=end_date)

        # جمع کل بر اساس property خواندنی total_price
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
            start_date=start_date_str,  # همان رشته‌ی ورودی کاربر برای نمایش در فرم
            end_date=end_date_str,      # همان رشته‌ی ورودی کاربر برای نمایش در فرم
            doctors=doctors,
        )

        # ----------------------------
        # Export Excel
        # ----------------------------
        if 'export_excel' in request.GET:
            import io
            import xlsxwriter
            from django.http import HttpResponse
            from jalali_date import date2jalali, datetime2jalali

            output = io.BytesIO()
            workbook = xlsxwriter.Workbook(output, {'in_memory': True})
            ws = workbook.add_worksheet("گزارش مالی")

            fmt_header = workbook.add_format({
                'bold': True, 'align': 'center', 'valign': 'vcenter',
                'bg_color': '#E0F2FE', 'border': 1
            })
            fmt_cell = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
            fmt_cell_rtl = workbook.add_format({'align': 'right', 'valign': 'vcenter', 'border': 1})

            headers = ['ID', 'بیمار', 'پزشک', 'نوع سفارش', 'تعداد واحد',
                       'قیمت واحد (تومان)', 'قیمت کل (تومان)', 'تاریخ تحویل', 'تاریخ ثبت']
            for col, h in enumerate(headers):
                ws.write(0, col, h, fmt_header)

            ws.set_column(0, 0, 8)
            ws.set_column(1, 1, 18)
            ws.set_column(2, 2, 18)
            ws.set_column(3, 3, 18)
            ws.set_column(4, 4, 10)
            ws.set_column(5, 6, 18)
            ws.set_column(7, 8, 16)

            row = 1
            for order in orders:
                ws.write(row, 0, order.id, fmt_cell)
                ws.write(row, 1, getattr(order, 'patient_name', '') or "", fmt_cell_rtl)
                ws.write(row, 2, order.doctor or "", fmt_cell_rtl)
                ws.write(row, 3, order.get_order_type_display(), fmt_cell)
                ws.write(row, 4, order.unit_count or 0, fmt_cell)

                ws.write(row, 5, money_fa_py(order.price or 0), fmt_cell_rtl)
                ws.write(row, 6, money_fa_py(getattr(order, 'total_price', 0) or 0), fmt_cell_rtl)

                due = ""
                if getattr(order, 'due_date', None):
                    try:
                        due = date2jalali(order.due_date).strftime("%Y/%m/%d")
                    except Exception:
                        due = str(order.due_date)

                created = ""
                if getattr(order, 'created_at', None):
                    try:
                        created = datetime2jalali(order.created_at).strftime("%Y/%m/%d")
                    except Exception:
                        created = order.created_at.strftime("%Y/%m/%d")

                ws.write(row, 7, due, fmt_cell)
                ws.write(row, 8, created, fmt_cell)
                row += 1

            ws.write(row, 0, 'جمع کل', fmt_header)
            for c in range(1, 5):
                ws.write(row, c, '', fmt_header)
            ws.write(row, 5, '', fmt_header)
            ws.write(row, 6, money_fa_py(total_invoice), fmt_header)
            ws.write(row, 7, '', fmt_header)
            ws.write(row, 8, '', fmt_header)

            workbook.close()
            output.seek(0)
            response = HttpResponse(
                output.read(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            response['Content-Disposition'] = 'attachment; filename=accounting_report.xlsx'
            return response

        # ----------------------------
        # Export PDF
        # ----------------------------
        if 'export_pdf' in request.GET:
            from django.template.loader import render_to_string
            from weasyprint import HTML
            from django.http import HttpResponse

            html_string = render_to_string('core/admin/accounting_report_export.html', {
                **context,
                'export_mode': 'pdf'
            })
            pdf = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
            response = HttpResponse(pdf, content_type='application/pdf')
            response['Content-Disposition'] = 'attachment; filename="accounting_report.pdf"'
            return response

        # نمایش صفحه
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


@admin.register(OrderEvent)
class OrderEventAdmin(admin.ModelAdmin):
    list_display = ('order', 'event_type', 'happened_at', 'direction', 'created_at')
    list_filter = ('event_type', 'direction', 'happened_at')
    search_fields = ('order__patient__name', 'order__doctor', 'notes')


from django.contrib import admin
from .models import Doctor

@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("name", "clinic", "phone", "code", "created_at")
    search_fields = ("name", "clinic", "phone", "code")
    list_per_page = 25


# -----------------------------
# عنوان‌های پنل ادمین
# -----------------------------
admin.site.site_header = "مدیریت لابراتوار"
admin.site.index_title = "داشبورد مدیریت"
admin.site.site_title = "مدیریت لابراتوار"


# =======================================================================
# 🆕 فقط اضافه شد: ثبت «LabSettings» به‌صورت Singleton (اگر مدل موجود باشد)
# =======================================================================
try:
    from django.apps import apps as _apps
    LabSettings = _apps.get_model('core', 'LabSettings')
except Exception:
    LabSettings = None

if LabSettings:
    @admin.register(LabSettings)
    class LabSettingsAdmin(admin.ModelAdmin):
        """ادمین تنظیمات؛ فقط یک رکورد مجاز است و لیست به ویرایش منتقل می‌شود."""

        # فیلدهای فرم را داینامیک از مدل می‌خوانیم (بدون 'id')
        def get_fields(self, request, obj=None):
            return [f.name for f in LabSettings._meta.fields if f.editable and f.name != 'id']

        # ستون‌های لیست را کوتاه و کاربردی نگه می‌داریم
        def get_list_display(self, request):
            fields = self.get_fields(request)
            preferred = [f for f in ('lab_name', 'phone', 'whatsapp', 'currency') if f in fields]
            return tuple(preferred) or tuple(fields[:4])

        # فقط یک رکورد مجاز است
        def has_add_permission(self, request):
            return LabSettings.objects.count() == 0

        # لیست را به «ویرایش اولین رکورد» ریدایرکت می‌کنیم
        def changelist_view(self, request, extra_context=None):
            qs = LabSettings.objects.all()
            if qs.exists():
                obj = qs.first()
                return HttpResponseRedirect(reverse('admin:core_labsettings_change', args=[obj.pk]))
            return HttpResponseRedirect(reverse('admin:core_labsettings_add'))

# --- Inline باید قبل از ProductAdmin باشد ---
class StageTemplateInline(admin.TabularInline):
    model = StageTemplate
    fk_name = 'product'  # صراحتاً می‌گوییم FK کدام است
    extra = 1
    fields = ('order_index', 'key', 'stage_key', 'label', 'default_duration_days', 'base_wage', 'is_active', 'notes')
    ordering = ('order_index',)
    show_change_link = True

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'category', 'default_unit_price', 'is_active', 'created_at')
    list_filter = ('is_active', 'category')
    search_fields = ('name', 'code', 'category')
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {'fields': ('name', 'code', 'category', 'is_active')}),
        ('قیمت و توضیحات', {'fields': ('default_unit_price', 'notes')}),
        ('سیستمی', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    inlines = [StageTemplateInline]

@admin.register(StageTemplate)
class StageTemplateAdmin(admin.ModelAdmin):
    list_display = ('product', 'order_index', 'label', 'key', 'stage_key', 'default_duration_days', 'base_wage', 'is_active')
    list_filter = ('product', 'is_active')
    search_fields = ('label', 'key', 'stage_key', 'product__name', 'product__code')
    ordering = ('product', 'order_index')

# =====================[ Digital Lab Transfer Admin ]=====================
# --- دیجیتال لاب: فرم ادمین با مرحلهٔ انتخابی وابسته به سفارش ---
from django import forms
from django.core.exceptions import ObjectDoesNotExist

# اگر StageInstance / StageTemplate را داری، وارد کن (وجود یکی کافی است)

try:
    from core.models import StageTemplate
except Exception:
    StageTemplate = None

# ==== Digital Lab: ثابتِ گزینه‌های رنگ (Shade) ====
SHADE_CHOICES = [
    ("", "— بدون رنگ —"),
    ("A1", "A1"), ("A2", "A2"), ("A3", "A3"), ("A3.5", "A3.5"), ("A4", "A4"),
    ("B1", "B1"), ("B2", "B2"), ("B3", "B3"), ("B4", "B4"),
    ("C1", "C1"), ("C2", "C2"), ("C3", "C3"), ("C4", "C4"),
    ("D2", "D2"), ("D3", "D3"), ("D4", "D4"),
    ("BL1", "BL1"), ("BL2", "BL2"), ("BL3", "BL3"), ("BL4", "BL4"),
]


def _stage_choices_for_order(order):
    """
    لیست گزینه‌های مرحله برای یک سفارش.
    اول از StageInstance سفارش می‌خوانیم؛ اگر نبود، از StageTemplate محصول.
    مقدار هر گزینه همان متنی است که قبلاً در stage_name ذخیره می‌کردیم.
    """
    choices = []
    if not order:
        return choices

    # 1) از StageInstance سفارش (اگر داریم)
    if StageInstance is not None:
        try:
            qs = StageInstance.objects.filter(order=order).order_by('planned_date', 'started_date', 'done_date', 'id')
            for si in qs:
                # تلاش برای یافتن بهترین برچسب
                label = getattr(si, 'label', None) or getattr(si, 'snapshot_label', None) or getattr(si, 'key', None) or 'مرحله'
                # مقدار ذخیره‌ای همان متنِ مرحله است تا با فیلد CharField سازگار بماند
                choices.append((str(label), str(label)))
        except Exception:
            pass

    # 2) اگر چیزی نبود، از StageTemplate محصول استفاده می‌کنیم (fallback)
    if not choices and StageTemplate is not None:
        try:
            # فرض: Order یک اشاره به نوع محصول دارد (مثل order_type/code)
            product_field = getattr(order, 'order_type', None) or getattr(order, 'product', None) or None
            if product_field:
                qs = StageTemplate.objects.filter(product=product_field).order_by('order_index', 'id')
                for st in qs:
                    label = getattr(st, 'label', None) or getattr(st, 'key', None) or 'مرحله'
                    choices.append((str(label), str(label)))
        except Exception:
            pass

    # اگر باز هم خالی بود، حداقل یک گزینه‌ی خنثی نشان بدهیم تا فرم بشکند
    if not choices:
        choices = [('', '— مرحله‌ای موجود نیست —')]

    return choices


@admin.register(DigitalLabTransfer)
class DigitalLabTransferAdmin(ModelAdminJalaliMixin, admin.ModelAdmin):
    class Media:
        js = ('admin/digital_lab_stage_loader.js',)

    list_display = (
        'id',
        'order_link',
        'lab_name',
        'stage_name',
        'shade_code',
        'status_badge',
        'sent_date_fa',
        'received_date_fa',
        'cost_toman_fa',
        'net_amount_fa',
        'note',
    )
    list_filter = ('status', 'lab_name', 'shade_code')
    search_fields = ('order__id', 'lab_name', 'stage_name')
    ordering = ('-sent_date',)
    date_hierarchy = 'sent_date'

    # =====================[ فیلد مرحله انتخابی بر اساس محصول سفارش ]=====================
    def get_form(self, request, obj=None, **kwargs):
        Form = super().get_form(request, obj, **kwargs)

        from django import forms as dj_forms
        from core.models import Order, StageTemplate

        # ۱) پیدا کردن سفارش انتخاب‌شده
        order_id = (
            request.POST.get('order')
            or request.GET.get('order')
            or (getattr(obj, 'order_id', None) if obj else None)
        )
        order = Order.objects.filter(pk=order_id).first() if order_id else None

        # ۲) ساخت لیست مراحل برای محصول آن سفارش
        choices = [('', '— ابتدا سفارش را انتخاب کنید —')]
        if order and order.order_type:
            qs = StageTemplate.objects.filter(product__code=order.order_type).order_by('order_index', 'id')
            rows = [(st.label, st.label) for st in qs]
            if rows:
                choices = rows
            else:
                choices = [('', '— مرحله‌ای برای این محصول ثبت نشده —')]

        # ۳) تبدیل فیلد به ChoiceField
        Form.base_fields['stage_name'] = dj_forms.ChoiceField(
            label='مرحله مربوطه (مثلاً فریم، میلینگ، طراحی)',
            choices=choices,
            required=True,
        )       

        # ۴) در حالت ویرایش: اگر مقدار قبلی خارج از choices بود، اضافه‌اش کن
        if obj:
            curr = getattr(obj, 'stage_name', None)
            if curr and all(val != curr for val, _ in Form.base_fields['stage_name'].choices):
                Form.base_fields['stage_name'].choices = (
                    [(curr, f'{curr} (قبلاً ذخیره‌شده)')] + list(Form.base_fields['stage_name'].choices)
                )

                # ۳.۱) فیلد رنگ: ChoiceField اختیاری
        Form.base_fields['shade_code'] = dj_forms.ChoiceField(
            label='رنگ (اختیاری)',
            choices=SHADE_CHOICES,
            required=False,
        )

        # اگر در حالت ویرایش، shade قبلی در choices نبود، به ابتدای لیست اضافه‌اش کن
        if obj:
            curr_shade = getattr(obj, 'shade_code', '') or ''
            if curr_shade and all(val != curr_shade for val, _ in Form.base_fields['shade_code'].choices):
                Form.base_fields['shade_code'].choices = (
                    [(curr_shade, f'{curr_shade} (قبلاً ذخیره‌شده)')] + list(Form.base_fields['shade_code'].choices)
                )

        return Form
    
    # وقتی در حالت افزودن رکورد جدید هستیم و سفارش از طریق پارامتر ?order= مشخص شده،
    # مراحل همان محصول را به صورت انتخابی نشان می‌دهیم.
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        order_id = request.GET.get('order')
        if order_id:
            initial['order'] = order_id
        return initial

    # ================================================================================

    @admin.display(description='سفارش')
    def order_link(self, obj):
        if not obj.order_id:
            return '—'
        try:
            url = reverse('admin:core_order_change', args=[obj.order_id])
            return format_html('<a href="{}">#{}</a>', url, obj.order_id)
        except Exception:
            return f'#{obj.order_id}'

    @admin.display(description='وضعیت')
    def status_badge(self, obj):
        color_map = {
            'sent': '#0ea5e9',
            'received': '#16a34a',
            'cancelled': '#9ca3af',
        }
        label = dict(DigitalLabTransfer.Status.choices).get(obj.status, '—')
        bg = color_map.get(obj.status, '#64748b')
        return format_html('<span class="lab-badge" style="background:{}">{}</span>', bg, label)

    @admin.display(description='ارسال')
    def sent_date_fa(self, obj):
        if not obj.sent_date:
            return '—'
        from jalali_date import date2jalali
        s = date2jalali(obj.sent_date).strftime('%Y/%m/%d')
        return s.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

    @admin.display(description='دریافت')
    def received_date_fa(self, obj):
        if not obj.received_date:
            return '—'
        from jalali_date import date2jalali
        s = date2jalali(obj.received_date).strftime('%Y/%m/%d')
        return s.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

    @admin.display(description='هزینه (تومان)')
    def cost_toman_fa(self, obj):
        if obj.cost is None:
            return '—'
        
        return money_fa_py(obj.cost)

        # =====================[ endpoint ساده برای دریافت مراحل محصول انتخابی ]=====================
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom = [
            path(
                'stages/',
                self.admin_site.admin_view(self.fetch_stages),
                name='digital_lab_stages',
            ),
        ]
        return custom + urls

    def fetch_stages(self, request):
        from django.http import JsonResponse
        from core.models import Order, StageTemplate

        order_id = request.GET.get('order_id')
        if not order_id:
            return JsonResponse([], safe=False)

        order = Order.objects.filter(pk=order_id).first()
        if not order or not order.order_type:
            return JsonResponse([], safe=False)

        stages = list(
            StageTemplate.objects.filter(product__code=order.order_type, is_active=True)
            .order_by('order_index', 'id')
            .values_list('label', flat=True)
        )
        return JsonResponse(stages, safe=False)

    @admin.display(description='خالص نوبت (تومان)')
    def net_amount_fa(self, obj):
        
        net = (obj.charge_amount or 0) - (obj.credit_amount or 0)
        # اگر دوست داری صفر را «—» نشان دهی، خط زیر را فعال کن:
        # if not net: return '—'
        return money_fa_py(net)

    def save_model(self, request, obj, form, change):
        """
        اگر StageTemplate قابل دسترسی باشد، موقع ذخیره stage_key را بر اساس label پیدا و ست می‌کنیم.
        """
        try:
            from core.models import Order, StageTemplate
            order = getattr(obj, 'order', None)
            label = getattr(obj, 'stage_name', '') or ''
            if order and getattr(order, 'order_type', None) and label:
                st = StageTemplate.objects.filter(product__code=order.order_type, label=label).values('key').first()
                if st and st.get('key'):
                    obj.stage_key = st['key']
        except Exception:
            pass

        super().save_model(request, obj, form, change)

# =====================[ Wages Admin ]=====================
from jalali_date.fields import JalaliDateField
from jalali_date.widgets import AdminJalaliDateWidget
from .models import Technician, StageRate, StageWorkLog, StageTemplate, WagePayout

@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "is_active", "created_at")
    list_filter = ("is_active", "role")
    search_fields = ("name", "role")
    ordering = ("name",)

class StageRateForm(forms.ModelForm):
    effective_from = JalaliDateField(label='تاریخ شروع اعتبار', widget=AdminJalaliDateWidget, required=True)
    class Meta:
        model = StageRate
        fields = ("stage", "technician", "rate", "effective_from", "note")

@admin.register(StageRate)
class StageRateAdmin(admin.ModelAdmin):
    form = StageRateForm
    list_display = ("stage", "technician", "rate", "effective_from", "created_at")
    list_filter = ("stage", "technician")
    search_fields = ("stage__label", "stage__product__name", "technician__name")
    ordering = ("stage", "technician", "-effective_from")

@admin.register(WagePayout)
class WagePayoutAdmin(ModelAdminJalaliMixin, admin.ModelAdmin):
    """
    مدیریت تسویه‌های دستمزد در پنل ادمین.
    از اینجا می‌تونی تسویه‌های تستی را ببینی، ویرایش یا حذف کنی.
    """
    list_display = (
        "technician",
        "period_start_j",
        "period_end_j",
        "status",
        "gross_total",
        "deductions_total",
        "bonus_total",
        "net_payable",
        "created_at",
    )
    list_filter = ("technician", "status")
    search_fields = ("technician__name", "payment_ref", "note")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")

class StageWorkLogForm(forms.ModelForm):
    started_at  = JalaliDateField(label='تاریخ شروع',  widget=AdminJalaliDateWidget, required=False)
    finished_at = JalaliDateField(label='تاریخ پایان', widget=AdminJalaliDateWidget, required=False)
    class Meta:
        model = StageWorkLog
        fields = ("order", "stage_inst", "stage_tpl", "technician",
                  "started_at", "finished_at",
                  "quantity", "unit_wage", "total_wage",
                  "status", "note")

@admin.register(StageWorkLog)
class StageWorkLogAdmin(admin.ModelAdmin):
    form = StageWorkLogForm
    list_display = ("order", "stage_tpl", "technician", "quantity", "unit_wage", "total_wage", "status", "created_at")
    list_filter = ("status", "stage_tpl", "technician")
    search_fields = ("order__id", "stage_tpl__label", "technician__name")
    ordering = ("-created_at",)
    readonly_fields = ("total_wage",)


@admin.register(ProductionEvent)
class ProductionEventAdmin(ModelAdminJalaliMixin, admin.ModelAdmin):
    list_display = [
        'id', 'order', 'event_type', 'stage_label', 'responsible', 'started_at', 'finished_at', 'created_at'
    ]
    list_filter = ['event_type', 'started_at']
    search_fields = ['order__patient__name', 'order__serial_number', 'stage_label', 'responsible', 'notes']
    autocomplete_fields = ['order']

























































