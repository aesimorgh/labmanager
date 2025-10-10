# core/forms.py
from decimal import Decimal, InvalidOperation
import re

from django import forms
from django.core.exceptions import ValidationError
from jalali_date.fields import JalaliDateField
from jalali_date.widgets import AdminJalaliDateWidget
from .models import OrderEvent
from .models import Patient, Order, Material, Accounting


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
# Order Form
# -----------------------------
class OrderForm(forms.ModelForm):
    order_date = JalaliDateField(
        label="تاریخ سفارش", required=False,
        widget=AdminJalaliDateWidget(attrs={'class': 'jalali_date'})
    )
    due_date = JalaliDateField(
        label="تاریخ تحویل", required=False,
        widget=AdminJalaliDateWidget(attrs={'class': 'jalali_date'})
    )

    # فیلدهای نمایشی/ورودی
    patient_name = forms.CharField(
        label="نام بیمار", required=True,
        widget=forms.TextInput(attrs={'placeholder': 'مثال: علی رضایی'})
    )
    doctor = forms.CharField(
        label="پزشک", required=True,
        widget=forms.TextInput(attrs={'placeholder': 'مثال: دکتر محمدی'})
    )
    order_type = forms.ChoiceField(label="نوع سفارش", choices=Order.ORDER_TYPES, required=True)
    unit_count = forms.IntegerField(
        label="تعداد واحد", min_value=1, initial=1, required=True,
        widget=forms.NumberInput(attrs={'dir': 'ltr'})
    )
    shade = forms.CharField(label="رنگ", required=False, widget=forms.TextInput(attrs={'placeholder': 'مثال: A2'}))
    price = forms.CharField(  # به‌صورت رشته برای نرمال‌سازی ورودی فارسی/کاما
        label="قیمت به ازای هر واحد (تومان)", required=True,
        widget=forms.TextInput(attrs={'dir': 'ltr', 'placeholder': 'مثال: 100000'})
    )
    serial_number = forms.CharField(label="شماره سریال", required=False)
    status = forms.ChoiceField(label="وضعیت", choices=Order.STATUS_CHOICES, required=True)
    notes = forms.CharField(label="یادداشت", required=False, widget=forms.Textarea(attrs={'rows': 2}))

    class Meta:
        model = Order
        fields = [
            'patient_name', 'doctor', 'order_type', 'unit_count',
            'shade', 'price', 'serial_number', 'status',
            'order_date', 'due_date', 'notes',
            'teeth_fdi',  # ← حالا مدل‌بیس و رسمی
        ]
        widgets = {
            'teeth_fdi': forms.HiddenInput(),  # ← مخفی ولی متصل به مدل
        }

    @staticmethod
    def _normalize_jalali_input(val: str) -> str:
        if val is None:
            return ''
        s = str(val).strip()
        if not s:
            return ''
        trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        return s.translate(trans).replace('/', '-')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # نرمال‌سازی ورودی تاریخ‌ها پیش از ولیدیشن
        if self.is_bound:
            data = self.data.copy()
            od_key = self.add_prefix('order_date')
            dd_key = self.add_prefix('due_date')
            if od_key in data:
                data[od_key] = self._normalize_jalali_input(data.get(od_key, ''))
            if dd_key in data:
                data[dd_key] = self._normalize_jalali_input(data.get(dd_key, ''))

            # اگر teeth_fdi خالی بود، از order-teeth_fdi (فرانت) پر کن و نرمال‌سازی ارقام/کاما انجام بده
            tf_key = self.add_prefix('teeth_fdi')
            if not str(data.get(tf_key, '')).strip():
                alt = data.get('order-teeth_fdi', '')
                if alt:
                    s = str(alt).strip()
                    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
                    s = s.translate(trans).replace('،', ',').replace(' ', '')
                    data[tf_key] = s

            self.data = data

        # مقدار اولیه نام بیمار در حالت ویرایش
        if getattr(self, 'instance', None) and getattr(self.instance, 'patient', None):
            self.fields['patient_name'].initial = self.instance.patient.name

    def clean_price(self):
        raw = self.cleaned_data.get('price', '')
        if raw in (None, ''):
            raise ValidationError("لطفاً مقدار قیمت را وارد کنید.")
        s = str(raw).strip()
        trans = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
        s = s.translate(trans).replace('٫', '.').replace('٬', '').replace('،', '').replace(',', '').replace(' ', '')
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
        instance = super().save(commit=False)

        # patient از روی نام
        name = self.cleaned_data.get('patient_name', '')
        name = name.strip() if isinstance(name, str) else ''
        if name:
            patient, _ = Patient.objects.get_or_create(name=name)
            instance.patient = patient

        # price از clean_price آمده
        price_value = self.cleaned_data.get('price')
        if price_value is not None:
            instance.price = price_value

        # گارد سروری: خط «دندان‌ها: …» را در notes جایگزین/اضافه کن
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
        fields = ['event_type', 'happened_at', 'direction', 'stage', 'notes', 'attachment']
        widgets = {
            'happened_at': forms.TextInput(attrs={
                'class': 'form-control jalali_date',
                'placeholder': 'تاریخ وقوع'
            }),
            'notes': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        }
























































