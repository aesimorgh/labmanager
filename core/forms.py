# core/forms.py
from decimal import Decimal, InvalidOperation
import re

from django import forms
from django.core.exceptions import ValidationError
from jalali_date.fields import JalaliDateField
from jalali_date.widgets import AdminJalaliDateWidget
from .models import OrderEvent
from .models import Patient, Order, Material, Accounting
from .models import Doctor, Product

# -----------------------------
# Patient Form
# -----------------------------
class PatientForm(forms.ModelForm):
    birth_date = JalaliDateField(
        label="تاریخ تولد",
        required=False,
        widget=AdminJalaliDateWidget(attrs={'class': 'jalali_date'})
    )

    class Meta:
        model = Patient
        fields = ['name', 'phone', 'email', 'address', 'birth_date']


# -----------------------------
# Order Form (FINAL, clean)
# -----------------------------
class OrderForm(forms.ModelForm):
    # تاریخ‌ها با ویجت جلالی
    order_date = JalaliDateField(
        label="تاریخ سفارش", required=False,
        widget=AdminJalaliDateWidget(attrs={'class': 'jalali_date'})
    )
    due_date = JalaliDateField(
        label="تاریخ تحویل", required=False,
        widget=AdminJalaliDateWidget(attrs={'class': 'jalali_date'})
    )

    # فیلدهای ورودی
    patient_name = forms.CharField(
        label="نام بیمار", required=True,
        widget=forms.TextInput(attrs={'placeholder': 'مثال: علی رضایی'})
    )

    # دکتر از جدول Doctor (Dropdown) — در مدل Order به‌صورت رشته ذخیره می‌شود
    doctor = forms.ModelChoiceField(
        label="پزشک", required=True,
        queryset=Doctor.objects.all(),
        widget=forms.Select
    )

    # نوع سفارش از Product (Dropdown) — کُد محصول در Order.order_type ذخیره می‌شود
    order_type = forms.ChoiceField(
        label="نوع سفارش", required=True,
        choices=[],  # در __init__ پر می‌شود
        widget=forms.Select
    )

    unit_count = forms.IntegerField(
        label="تعداد واحد", min_value=1, initial=1, required=True,
        widget=forms.NumberInput(attrs={'dir': 'ltr'})
    )
    shade = forms.CharField(
        label="رنگ", required=False,
        widget=forms.TextInput(attrs={'placeholder': 'مثال: A2'})
    )
    # قیمت را متنی می‌گیریم تا ارقام فارسی/کاما پاک شود
    price = forms.CharField(
        label="قیمت به ازای هر واحد (تومان)", required=True,
        widget=forms.TextInput(attrs={'dir': 'ltr', 'placeholder': 'مثال: 100000'})
    )
    serial_number = forms.CharField(label="شماره سریال", required=False)

    # چون در تب ساخت سفارش این فیلد را نشان نمی‌دهی، required=False تا گیر ندهد
    status = forms.ChoiceField(label="وضعیت", choices=Order.STATUS_CHOICES, required=False)

    notes = forms.CharField(label="یادداشت", required=False, widget=forms.Textarea(attrs={'rows': 2}))

    class Meta:
        model = Order
        fields = [
            'patient_name', 'doctor', 'order_type', 'unit_count',
            'shade', 'price', 'serial_number', 'status',
            'order_date', 'due_date', 'notes',
            'teeth_fdi',   # متصل به مدل
        ]
        widgets = {
            'teeth_fdi': forms.HiddenInput(),
        }

    @staticmethod
    def _normalize_jalali_input(val: str) -> str:
        """ورودی جلالی را نرمال می‌کند (ارقام فارسی/عربی→انگلیسی و '/'→'-')."""
        if val is None:
            return ''
        s = str(val).strip()
        if not s:
            return ''
        trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        return s.translate(trans).replace('/', '-')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # نرمال‌سازی تاریخ‌ها روی POST (بدون تغییر UI)
        if self.is_bound:
            data = self.data.copy()
            od_key = self.add_prefix('order_date')
            dd_key = self.add_prefix('due_date')
            if od_key in data:
                data[od_key] = self._normalize_jalali_input(data.get(od_key, ''))
            if dd_key in data:
                data[dd_key] = self._normalize_jalali_input(data.get(dd_key, ''))
            self.data = data

        # مقدار اولیه نام بیمار از instance.patient
        if getattr(self, 'instance', None) and getattr(self.instance, 'patient', None):
            self.fields['patient_name'].initial = self.instance.patient.name

        # پر کردن choices نوع سفارش از Product (فقط فعال‌ها)
        try:
            prods = Product.objects.filter(is_active=True).order_by('name').values_list('code', 'name')
            self.fields['order_type'].choices = [('', '— انتخاب کنید —')] + list(prods)
        except Exception:
            self.fields['order_type'].choices = [('', '— انتخاب کنید —')]

        # مقدار اولیهٔ dropdownها از مقدار ذخیره‌شده
        if getattr(self.instance, 'order_type', None):
            self.fields['order_type'].initial = self.instance.order_type
        if getattr(self.instance, 'doctor', None):
            self.fields['doctor'].initial = Doctor.objects.filter(name=self.instance.doctor).first()

    def clean_doctor(self):
        """ModelChoiceField → نام دکتر برای ذخیره در CharField مدل."""
        d = self.cleaned_data.get('doctor')
        return d.name if d else ''

    def clean_order_type(self):
        """ChoiceField(Product.code) → همان code ذخیره می‌شود."""
        code = (self.cleaned_data.get('order_type') or '').strip()
        if not code:
            raise ValidationError("نوع سفارش را انتخاب کنید.")
        # اگر Order.ORDER_TYPES را سخت‌گیرانه می‌خواهی، این 3 خط را باز کن:
        # allowed = {c for c, _ in Order.ORDER_TYPES}
        # if code not in allowed:
        #     raise ValidationError("نوع سفارش انتخاب‌شده معتبر نیست.")
        return code

    def clean_price(self):
        """نرمال‌سازی قیمت به Decimal."""
        raw = self.cleaned_data.get('price', '')
        if raw in (None, ''):
            raise ValidationError("لطفاً مقدار قیمت را وارد کنید.")
        s = str(raw).strip()
        trans = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
        s = s.translate(trans)
        s = s.replace('٫', '.').replace('٬', '').replace('،', '').replace(',', '').replace(' ', '')
        s = re.sub(r'[^0-9.\-]', '', s)
        if s.count('.') > 1:
            raise ValidationError("لطفاً یک عدد معتبر وارد کنید.")
        try:
            value = Decimal(s)
        except (InvalidOperation, ValueError):
            raise ValidationError("لطفاً یک عدد معتبر وارد کنید.")
        if value < 0:
            raise ValidationError("قیمت نمی‌تواند منفی باشد.")
        return value

    def save(self, commit=True):
        """بیمار را می‌سازد/پیدا می‌کند، قیمت Decimal را می‌گذارد، و خط «دندان‌ها: …» را در notes به‌روز می‌کند."""
        instance = super().save(commit=False)

        # بیمار از روی نام
        name = (self.cleaned_data.get('patient_name') or '').strip()
        if name:
            patient, _ = Patient.objects.get_or_create(name=name)
            instance.patient = patient

        # قیمت Decimal
        price_value = self.cleaned_data.get('price')
        if price_value is not None:
            instance.price = price_value

        # اگر status در فرم نبود، بگذار همان مقدار مدل بماند (یا این‌جا دستی ست کن)
        # instance.status = instance.status or 'pending'

        # هم‌ترازی notes با teeth_fdi
        codes = (self.cleaned_data.get('teeth_fdi') or '').strip()
        if codes:
            codes_csv = ', '.join([c.strip() for c in codes.split(',') if c.strip()])
            txt = instance.notes or ''
            txt = re.sub(r'(^|\n)\s*دندان‌ها\s*:\s*[^\n]*\n?', r'\1', txt or '')
            if txt and not txt.endswith('\n'):
                txt += '\n'
            instance.notes = (txt or '') + f'دندان‌ها: {codes_csv}'

        if commit:
            instance.save()
            if hasattr(self, 'save_m2m'):
                self.save_m2m()
        return instance


# -----------------------------
# Material Form
# -----------------------------
class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = '__all__'


# -----------------------------
# Payment Form
# -----------------------------
class AccountingForm(forms.ModelForm):
    payment_date = JalaliDateField(
        label="تاریخ پرداخت",
        required=False,
        widget=AdminJalaliDateWidget(attrs={'class': 'jalali_date'})
    )
    date = JalaliDateField(
        label="تاریخ ثبت",
        required=False,
        widget=AdminJalaliDateWidget(attrs={'class': 'jalali_date'})
    )

    amount = forms.DecimalField(
        label="مبلغ پرداخت",
        max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={
            'dir': 'ltr',
            'inputmode': 'decimal',
            'step': 'any',
            'min': '0',
        })
    )

    class Meta:
        model = Accounting
        fields = '__all__'


# -----------------------------
# Order Event Form
# -----------------------------
class OrderEventForm(forms.ModelForm):
    class Meta:
        model = OrderEvent
        fields = ['event_type', 'happened_at', 'direction', 'stage_instance', 'stage', 'notes', 'attachment']
        widgets = {
            'happened_at': forms.TextInput(attrs={
                'class': 'form-control jalali_date',
                'placeholder': 'تاریخ وقوع'
            }),
            'notes': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        order = kwargs.pop('order', None)
        super().__init__(*args, **kwargs)

        # محدودکردن گزینه‌های نوع رویداد (همان که قبلاً گذاشتیم) — اگر از قبل داری، همین را نگه دار
        self.fields['event_type'].choices = [
            ('', '— انتخاب کنید —'),
            (OrderEvent.EventType.SENT_TO_CLINIC,        'ارسال به مطب'),
            (OrderEvent.EventType.RECEIVED_IN_LAB,       'دریافت از مطب'),
            (OrderEvent.EventType.SENT_TO_DIGITAL,       'ارسال به لابراتوار دیجیتال'),
            (OrderEvent.EventType.RECEIVED_FROM_DIGITAL, 'دریافت از لابراتوار دیجیتال'),
            (OrderEvent.EventType.FINAL_SHIPMENT,        'ارسال نهایی'),
        ]

        # مرحلهٔ مرتبط (اختیاری) — فقط مراحل همین سفارش
        from .models import StageInstance  # import محلی برای جلوگیری از چرخه
        if order is not None:
            self.fields['stage_instance'] = forms.ModelChoiceField(
                queryset=StageInstance.objects.filter(order=order).order_by('order_index', 'id'),
                required=False,
                label='مرحله مرتبط (اختیاری)',
                widget=forms.Select(attrs={'class': 'form-select'})
            )
        else:
            # اگر به هر دلیل order پاس داده نشد، فیلد را اختیاری و خالی نگه می‌داریم
            self.fields['stage_instance'] = forms.ModelChoiceField(
                queryset=StageInstance.objects.none(), required=False,
                label='مرحله مرتبط (اختیاری)', widget=forms.Select(attrs={'class': 'form-select'})
            )

        # ظاهر تمیز
        self.fields['event_type'].widget.attrs.update({'class': 'form-select'})
        self.fields['direction'].widget.attrs.update({'class': 'form-select'})
        self.fields['stage'].widget.attrs.update({'class': 'form-control', 'placeholder': 'علت (متنی؛ در صورت نیاز)'})
        self.fields['attachment'].widget.attrs.update({'class': 'form-control'})























































