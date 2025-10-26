from django import forms
from core.models import Doctor

# تقویم شمسی
from jalali_date.fields import JalaliDateField
from jalali_date.widgets import AdminJalaliDateWidget


class InvoiceDraftFilterForm(forms.Form):
    """
    فرم انتخاب دکتر و بازه‌ی زمانی بر اساس shipped_date
    - فهرست دکترها مستقیماً از مدل core.Doctor پر می‌شود.
    """
    doctor = forms.ModelChoiceField(
        queryset=Doctor.objects.all().order_by('name'),
        label='دکتر',
        required=False,  # فعلاً اختیاری بماند تا اگر Doctor خالی است فرم کار کند
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    period_from = JalaliDateField(
        label='از تاریخ (shipped_date)',
        required=True,
        widget=AdminJalaliDateWidget
    )

    period_to = JalaliDateField(
        label='تا تاریخ (shipped_date)',
        required=True,
        widget=AdminJalaliDateWidget
    )

    include_already_invoiced = forms.BooleanField(
        label='نمایش سفارش‌های قبلاً فاکتور شده',
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def clean(self):
        cleaned = super().clean()
        f = cleaned.get('period_from')
        t = cleaned.get('period_to')
        if f and t and f > t:
            self.add_error('period_to', 'بازه‌ی تاریخ نامعتبر است (تا تاریخ باید پس از از تاریخ باشد).')
        return cleaned

# =====================[ Inventory Forms ]=====================
from decimal import Decimal, InvalidOperation
from django.core.files.uploadedfile import UploadedFile

from billing.models import MaterialItem, MaterialLot, StockMovement
from core.models import Order


def _fa_to_en_decimal(s: str) -> Decimal:
    """
    تبدیل ورودی کاربر به Decimal مثبت:
    - ارقام فارسی/عربی → انگلیسی
    - حذف جداکننده‌های هزارگان: '٬'، '،'، ','، فاصله
    - ممیز فارسی '٫' → '.'
    - مدیریت نقطه:
        * بیش از یک نقطه: همه حذف (هزارگان فرض)
        * یک نقطه:
            - اگر بعد از نقطه دقیقا 3 رقم و قبل/بعد فقط رقم بود → هزارگان (حذف نقطه)
            - در غیر این صورت → اعشار (نگه‌دار)
    """
    if s is None:
        raise forms.ValidationError('عدد نامعتبر.')

    s = str(s).strip()
    if not s:
        raise forms.ValidationError('عدد خالی است.')

    # نگاشت دستی ارقام (بدون maketrans)
    digit_map = {
        '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
        '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
        '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
    }
    s = ''.join(digit_map.get(ch, ch) for ch in s)

    # ممیز فارسی → نقطه
    s = s.replace('٫', '.')

    # حذف جداکننده‌های هزارگانِ رایج
    for sep in ('٬', '،', ',', ' '):
        s = s.replace(sep, '')

    # مدیریت نقطه‌ها
    dot_count = s.count('.')
    if dot_count > 1:
        s = s.replace('.', '')
    elif dot_count == 1:
        before, after = s.split('.', 1)
        if before.isdigit() and after.isdigit() and len(after) == 3:
            # هزارگان
            s = before + after
        else:
            # اعشار (نگه‌دار)
            pass

    try:
        v = Decimal(s)
    except (InvalidOperation, ValueError):
        raise forms.ValidationError('عدد نامعتبر.')

    if v <= 0:
        raise forms.ValidationError('عدد باید بزرگ‌تر از صفر باشد.')
    return v



class MaterialPurchaseForm(forms.Form):
    """ثبت خرید/لات برای متریال و ابزار مصرفی با تبدیل واحد و دو حالت قیمت‌گذاری (قیمت واحد / قیمت کل)."""

    # ----- فیلتر نوع آیتم -----
    item_type_filter = forms.ChoiceField(
        label='نوع آیتم',
        choices=(('all','همه'), ('material','متریال'), ('tool','ابزار مصرفی')),
        required=False,
        initial='all',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    # ----- آیتم -----
    item = forms.ModelChoiceField(
        queryset=MaterialItem.objects.all().order_by('name'),
        label='آیتم',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    # ----- مشخصات لات -----
    lot_code = forms.CharField(label='کد لات/سری', required=False,
                               widget=forms.TextInput(attrs={'class': 'form-control'}))
    vendor = forms.CharField(label='تأمین‌کننده', required=False,
                             widget=forms.TextInput(attrs={'class': 'form-control'}))

    # ----- تاریخ‌ها -----
    purchase_date = JalaliDateField(label='تاریخ خرید', required=True, widget=AdminJalaliDateWidget)
    start_use_date = JalaliDateField(label='تاریخ آغاز مصرف (اختیاری)', required=False, widget=AdminJalaliDateWidget)
    end_use_date   = JalaliDateField(label='تاریخ اتمام مصرف (اختیاری)', required=False, widget=AdminJalaliDateWidget)
    expire_date    = JalaliDateField(label='انقضا (اختیاری)', required=False, widget=AdminJalaliDateWidget)

    # ----- رنگ (برای آیتم‌های دارای رنگ) -----
    shade_code = forms.CharField(
        label='رنگ (Shade)',
        required=False,
        help_text='فقط برای آیتم‌های رنگ‌دار مثل پرسلن لازم است.',
        widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'A1/A2/BL...'})
    )

    # ----- مقدار ورودی + واحد ورودی -----
    qty_in = forms.CharField(
        label='مقدار خرید',
        widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'مثل 2.5 یا 100'})
    )
    input_uom = forms.ChoiceField(
        label='واحد مقدار',
        choices=(('g','گرم'), ('kg','کیلوگرم'), ('ml','میلی‌لیتر'), ('l','لیتر'), ('pcs','عدد'), ('box','باکس')),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    # ----- حالت قیمت‌گذاری -----
    price_mode = forms.ChoiceField(
        label='حالت قیمت‌گذاری',
        choices=(('unit','قیمت واحد'), ('total','قیمت کل')),
        initial='unit',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    unit_cost = forms.CharField(
        label='قیمت واحد (براساس واحدِ بالا)',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'مثلاً قیمت هر kg / box / pcs'})
    )
    total_cost = forms.CharField(
        label='قیمت کل',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'اگر قیمت کل داری، اینجا بنویس'})
    )

    currency  = forms.CharField(label='ارز', required=False, initial='IRR',
                                widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr'}))

    invoice_no = forms.CharField(label='شماره فاکتور', required=False,
                                 widget=forms.TextInput(attrs={'class': 'form-control'}))
    attachment = forms.FileField(label='پیوست فاکتور', required=False)
    notes      = forms.CharField(label='توضیحات', required=False,
                                 widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))

    # ---------- __init__: فیلتر آیتم‌ها بر اساس نوع ----------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        itype = None
        if hasattr(self, 'data') and self.data:
            itype = self.data.get('item_type_filter')
        if not itype:
            itype = (self.initial.get('item_type_filter') or self.fields['item_type_filter'].initial)

        qs = MaterialItem.objects.all().order_by('name')
        if itype == 'material':
            qs = qs.filter(item_type='material')
        elif itype == 'tool':
            qs = qs.filter(item_type='tool')
        self.fields['item'].queryset = qs

    # ---------- Helpers ----------
    def _qty_to_base(self, qty: Decimal, from_uom: str, base_uom: str, pack_size: int|None) -> Decimal:
        """
        تبدیل مقدار ورودی به «واحد پایه آیتم».
        g<->kg ، ml<->l ، pcs<->box
        """
        if from_uom == base_uom:
            return qty
        # g/kg
        if base_uom == 'g' and from_uom == 'kg':
            return qty * Decimal('1000')
        if base_uom == 'kg' and from_uom == 'g':
            return qty / Decimal('1000')
        # ml/l
        if base_uom == 'ml' and from_uom == 'l':
            return qty * Decimal('1000')
        if base_uom == 'l' and from_uom == 'ml':
            return qty / Decimal('1000')
        # pcs/box
        if base_uom == 'pcs' and from_uom == 'box':
            if not pack_size:
                raise forms.ValidationError('برای تبدیل "باکس به عدد" باید در کارت آیتم، pack_size (تعداد در هر باکس) مشخص باشد.')
            return qty * Decimal(str(pack_size))
        if base_uom == 'box' and from_uom == 'pcs':
            if not pack_size:
                raise forms.ValidationError('برای تبدیل "عدد به باکس" باید در کارت آیتم، pack_size مشخص باشد.')
            return qty / Decimal(str(pack_size))

        raise forms.ValidationError(f'واحد ورودی ({from_uom}) با واحد پایه آیتم ({base_uom}) سازگار نیست.')

    def _unit_price_to_base(self, price_per_input_uom: Decimal, from_uom: str, base_uom: str, pack_size: int|None) -> Decimal:
        """
        تبدیل «قیمت واحدِ (از منظر کاربر)» به «قیمت هر واحد پایه آیتم».
        مثال: اگر پایه=گرم و قیمتِ ورودی برای 1kg است → تقسیم بر 1000.
        """
        if from_uom == base_uom:
            return price_per_input_uom
        # g/kg
        if base_uom == 'g' and from_uom == 'kg':
            return price_per_input_uom / Decimal('1000')
        if base_uom == 'kg' and from_uom == 'g':
            return price_per_input_uom * Decimal('1000')
        # ml/l
        if base_uom == 'ml' and from_uom == 'l':
            return price_per_input_uom / Decimal('1000')
        if base_uom == 'l' and from_uom == 'ml':
            return price_per_input_uom * Decimal('1000')
        # pcs/box
        if base_uom == 'pcs' and from_uom == 'box':
            if not pack_size:
                raise forms.ValidationError('برای تبدیل قیمت باکس→عدد، pack_size لازم است.')
            return price_per_input_uom / Decimal(str(pack_size))
        if base_uom == 'box' and from_uom == 'pcs':
            if not pack_size:
                raise forms.ValidationError('برای تبدیل قیمت عدد→باکس، pack_size لازم است.')
            return price_per_input_uom * Decimal(str(pack_size))

        raise forms.ValidationError(f'قیمتِ واحدِ ورودی ({from_uom}) قابل تبدیل به واحد پایه ({base_uom}) نیست.')

    # ---------- Cleaners ----------
    def clean_qty_in(self):
        return _fa_to_en_decimal(self.cleaned_data.get('qty_in'))

    def clean_unit_cost(self):
        v = self.cleaned_data.get('unit_cost')
        return _fa_to_en_decimal(v) if v not in (None, '') else None

    def clean_total_cost(self):
        v = self.cleaned_data.get('total_cost')
        return _fa_to_en_decimal(v) if v not in (None, '') else None

    def clean(self):
        cleaned = super().clean()

        # Shade الزامی فقط برای آیتم‌های رنگ‌دار
        item = cleaned.get('item')
        shade = (cleaned.get('shade_code') or '').strip()
        if item and getattr(item, 'shade_enabled', False) and not shade:
            self.add_error('shade_code', 'برای آیتم‌های دارای رنگ، وارد کردن Shade الزامی است.')

        # مقدار و واحد ورودی
        qty_in = cleaned.get('qty_in')
        input_uom = cleaned.get('input_uom') or ''
        if not item:
            return cleaned

        base_uom = item.uom  # واحد پایه آیتم (g/ml/pcs)
        pack_size = item.pack_size or None

        # قیمت‌گذاری
        price_mode = cleaned.get('price_mode') or 'unit'
        unit_cost_in = cleaned.get('unit_cost')     # ممکن است None باشد
        total_cost_in = cleaned.get('total_cost')   # ممکن است None باشد

        # تبدیل مقدار به واحد پایه
        qty_in_base = self._qty_to_base(qty_in, input_uom, base_uom, pack_size)

        # تعیین قیمت هر واحد پایه
        if price_mode == 'unit':
            if unit_cost_in is None:
                self.add_error('unit_cost', 'در حالت "قیمت واحد"، وارد کردن قیمت واحد الزامی است.')
                return cleaned
            unit_cost_base = self._unit_price_to_base(unit_cost_in, input_uom, base_uom, pack_size)
            total_cost_calc = (qty_in_base * unit_cost_base)
        else:  # 'total'
            if total_cost_in is None:
                self.add_error('total_cost', 'در حالت "قیمت کل"، وارد کردن قیمت کل الزامی است.')
                return cleaned
            unit_cost_base = (total_cost_in / qty_in_base)
            total_cost_calc = total_cost_in

        # ذخیره برای استفاده در save()
        cleaned['__qty_in_base'] = qty_in_base
        cleaned['__unit_cost_base'] = unit_cost_base
        cleaned['__total_cost_calc'] = total_cost_calc
        cleaned['__base_uom'] = base_uom
        return cleaned

    def clean_attachment(self):
        f = self.cleaned_data.get('attachment')
        if f and not isinstance(f, UploadedFile):
            raise forms.ValidationError('فایل نامعتبر است.')
        return f

    # ---------- ذخیره ----------
    def save(self, created_by: str = ''):
        """
        خروجی: (lot, move)
        - نرمال‌سازی: qty_in → «واحد پایه‌ی آیتم»، unit_cost → «قیمت هر واحد پایه»
        """
        if not self.is_valid():
            raise ValueError("فرم معتبر نیست.")
        cd = self.cleaned_data

        lot = MaterialLot.objects.create(
            item=cd['item'],
            lot_code=cd.get('lot_code') or "",
            vendor=cd.get('vendor') or "",
            purchase_date=cd['purchase_date'],
            start_use_date=cd.get('start_use_date'),
            end_use_date=cd.get('end_use_date'),
            expire_date=cd.get('expire_date'),
            qty_in=cd['__qty_in_base'],             # ← مقدار برحسب واحد پایه آیتم
            unit_cost=cd['__unit_cost_base'],       # ← قیمت هر واحد پایه آیتم
            currency=cd.get('currency') or "IRR",
            shade_code=cd.get('shade_code') or "",
            invoice_no=cd.get('invoice_no') or "",
            attachment=cd.get('attachment'),
            notes=cd.get('notes') or "",
        )

        move = StockMovement.objects.create(
            item=cd['item'],
            lot=lot,
            movement_type='purchase',
            qty=cd['__qty_in_base'],                # مثبت، در واحد پایه
            unit_cost_effective=cd['__unit_cost_base'],
            happened_at=cd['purchase_date'],
            reason='purchase',
            created_by=created_by or '',
        )
        return lot, move


class MaterialIssueForm(forms.Form):
    """فرم ثبت مصرف متریال برای یک سفارش (حرکت issue)."""
    order = forms.ModelChoiceField(
        queryset=Order.objects.all().order_by('-id'),
        label='سفارش',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    item = forms.ModelChoiceField(
        queryset=MaterialItem.objects.filter(is_active=True).order_by('name'),
        label='آیتم متریال',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    happened_at = JalaliDateField(label='تاریخ مصرف', required=True, widget=AdminJalaliDateWidget)
    qty = forms.CharField(label='مقدار مصرف (مثبت بنویسید)', widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr'}))
    lot = forms.ModelChoiceField(
        queryset=MaterialLot.objects.all().order_by('-purchase_date'),
        label='لات (اختیاری)',
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    comment = forms.CharField(label='توضیح', required=False,
                              widget=forms.TextInput(attrs={'class': 'form-control'}))

    def __init__(self, *args, **kwargs):
        order = kwargs.pop('order', None)
        super().__init__(*args, **kwargs)
        if order is not None:
            self.fields['order'].initial = order.pk

    def clean_qty(self):
        return _fa_to_en_decimal(self.cleaned_data.get('qty'))

    def save(self, created_by: str = ''):
        """
        خروجی: move (StockMovement issue)
        - qty ورودی را مثبت می‌گیریم، خود حرکت آن را منفی ذخیره خواهد کرد (طبق save()).
        - unit_cost_effective به طور خودکار از میانگین لحظه‌ای آیتم ست می‌شود.
        """
        if not self.is_valid():
            raise ValueError("فرم معتبر نیست.")
        cd = self.cleaned_data
        move = StockMovement.objects.create(
            item=cd['item'],
            lot=cd.get('lot'),
            movement_type='issue',
            qty=cd['qty'],  # مثبت داده شده؛ در save() به منفی تبدیل و اعتبارسنجی می‌شود
            happened_at=cd['happened_at'],
            order=cd['order'],
            product_code='',  # در صورت نیاز بعداً با Product لینک می‌کنیم
            reason=cd.get('comment') or '',
            created_by=created_by or '',
        )
        return move


from .models import Equipment, Repair

class RepairForm(forms.Form):
    equipment = forms.ModelChoiceField(
        queryset=Equipment.objects.filter(is_active=True).order_by('name'),
        label='تجهیز',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    title  = forms.CharField(label='عنوان خرابی/سرویس', widget=forms.TextInput(attrs={'class': 'form-control'}))
    vendor = forms.CharField(label='تکنسین/شرکت', required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    amount = forms.CharField(label='مبلغ', widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr'}))

    occurred_date = JalaliDateField(label='تاریخ وقوع', required=True, widget=AdminJalaliDateWidget)
    paid_date     = JalaliDateField(label='تاریخ پرداخت (اختیاری)', required=False, widget=AdminJalaliDateWidget)
    payment_method = forms.ChoiceField(
        choices=[('', '— انتخاب کنید —')] + list(Repair.PayMethod.choices),
        required=False, label='روش پرداخت',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    note = forms.CharField(label='یادداشت', required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))
    attachment = forms.FileField(label='پیوست', required=False)

    def __init__(self, *args, **kwargs):
        eq = kwargs.pop('equipment', None)
        super().__init__(*args, **kwargs)
        if eq:
            self.fields['equipment'].initial = eq.pk

    def clean_amount(self):
        return _fa_to_en_decimal(self.cleaned_data.get('amount'))

    def save(self):
        cd = self.cleaned_data
        obj = Repair.objects.create(
            equipment=cd['equipment'],
            title=cd['title'],
            vendor=cd.get('vendor') or '',
            amount=cd['amount'],
            occurred_date=cd['occurred_date'],
            paid_date=cd.get('paid_date') or cd['occurred_date'],
            payment_method=cd.get('payment_method') or '',
            attachment=cd.get('attachment'),
            note=cd.get('note') or '',
        )
        return obj
