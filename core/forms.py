# core/forms.py
from decimal import Decimal, InvalidOperation
import re

from django import forms
from django.core.exceptions import ValidationError
from jalali_date.fields import JalaliDateField
from jalali_date.widgets import AdminJalaliDateWidget

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
        label="تاریخ سفارش",
        required=False,
        widget=AdminJalaliDateWidget(attrs={'class': 'jalali_date'})
    )
    due_date = JalaliDateField(
        label="تاریخ تحویل",
        required=False,
        widget=AdminJalaliDateWidget(attrs={'class': 'jalali_date'})
    )

    # فیلدهای مورد استفاده در فرم ادمین (و home.html)
    patient_name = forms.CharField(
        label="نام بیمار",
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'مثال: علی رضایی'})
    )
    doctor = forms.CharField(
        label="پزشک",
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'مثال: دکتر محمدی'})
    )
    order_type = forms.ChoiceField(
        label="نوع سفارش",
        choices=Order.ORDER_TYPES,
        required=True
    )
    unit_count = forms.IntegerField(
        label="تعداد واحد",
        min_value=1,
        initial=1,
        required=True,
        widget=forms.NumberInput(attrs={'dir': 'ltr'})
    )
    shade = forms.CharField(
        label="رنگ",
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'مثال: A2'})
    )

    # تبدیل price به CharField در فرم تا بتوانیم ورودی‌های فارسی/با کاما را نرمال کنیم
    price = forms.CharField(
        label="قیمت به ازای هر واحد (تومان)",
        required=True,
        widget=forms.TextInput(attrs={'dir': 'ltr', 'placeholder': 'مثال: 100000'})
    )

    serial_number = forms.CharField(
        label="شماره سریال",
        required=False
    )
    status = forms.ChoiceField(
        label="وضعیت",
        choices=Order.STATUS_CHOICES,
        required=True
    )
    notes = forms.CharField(
        label="یادداشت",
        required=False,
        widget=forms.Textarea(attrs={'rows': 2})
    )

    class Meta:
        model = Order
        fields = [
            'patient_name', 'doctor', 'order_type', 'unit_count',
            'shade', 'price', 'serial_number', 'status',
            'order_date', 'due_date', 'notes'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # اگر در حالت ویرایش هستیم، مقدار اولیه patient_name را از instance.patient قرار بده
        if getattr(self, 'instance', None) and getattr(self.instance, 'patient', None):
            self.fields['patient_name'].initial = self.instance.patient.name

    def clean_price(self):
        """
        نرمال‌سازی رشته ورودی قیمت:
        - تبدیل ارقام فارسی/عربی به لاتین
        - حذف کاما/حروف جداکننده هزار و فاصله‌ها
        - تبدیل جداکننده ده‌دهی عربی '٫' به '.'
        سپس تبدیل به Decimal و اعتبارسنجی.
        """
        raw = self.cleaned_data.get('price', '')
        if raw in (None, ''):
            raise ValidationError("لطفاً مقدار قیمت را وارد کنید.")

        s = str(raw).strip()

        # تبدیل ارقام فارسی و عربی به لاتین
        trans = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
        s = s.translate(trans)

        # تبدیل جداکننده‌های اعشاری عربی به نقطه و حذف جداکننده‌های هزار و علائم فارسی/عربی
        s = s.replace('٫', '.')
        s = s.replace('٬', '')   # Arabic thousands separator
        s = s.replace('،', '')   # Persian comma
        s = s.replace(',', '')   # standard comma
        s = s.replace(' ', '')

        # حذف هر کاراکتر ناخواسته (بجز ارقام، نقطه و منفی)
        s = re.sub(r'[^0-9.\-]', '', s)

        # اطمینان از اینکه حداکثر یک نقطه وجود دارد
        if s.count('.') > 1:
            raise ValidationError("لطفاً یک عدد معتبر وارد کنید.")

        try:
            value = Decimal(s)
        except (InvalidOperation, ValueError):
            raise ValidationError("لطفاً یک عدد معتبر وارد کنید.")

        if value < 0:
            raise ValidationError("قیمت نمی‌تواند منفی باشد.")

        # برگرداندن Decimal؛ این مقدار به فیلد مدل اختصاص خواهد یافت
        return value

    def save(self, commit=True):
        """
        قبل از ذخیره:
        - از patient_name یک Patient پیدا یا بساز و به instance.patient اختصاص بده
        - مقدار price (که در clean_price به Decimal تبدیل شده) را به instance.price اختصاص بده
        """
        instance = super().save(commit=False)

        # ساخت یا یافتن Patient از روی نام
        name = self.cleaned_data.get('patient_name', '')
        name = name.strip() if isinstance(name, str) else ''
        if name:
            patient, created = Patient.objects.get_or_create(name=name)
            instance.patient = patient

        # مقدار price (Decimal) را به نمونه اختصاص می‌دهیم
        price_value = self.cleaned_data.get('price')
        if price_value is not None:
            instance.price = price_value

        if commit:
            instance.save()
            # ذخیره m2m اگر وجود دارد
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

























































