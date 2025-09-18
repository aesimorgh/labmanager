# core/forms.py
from decimal import Decimal, InvalidOperation
from django import forms
from jalali_date.fields import JalaliDateField
from jalali_date.widgets import AdminJalaliDateWidget
from .models import Patient, Order, Material, Payment

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
        fields = ['name', 'phone', 'email', 'address', 'birth_date']  # ← تغییر داده شد


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

    # 🆕 نام بیمار (متنی)
    patient_name = forms.CharField(
        label="نام بیمار",
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'مثال: علی رضایی'})
    )

    # 🆕 تعداد واحد
    unit_count = forms.IntegerField(
        label="تعداد واحد",
        min_value=1,
        initial=1,
        required=True,
        widget=forms.NumberInput(attrs={'dir': 'ltr'})
    )

    # 🆕 قیمت به ازای هر واحد
    price = forms.CharField(
        label='قیمت به ازای هر واحد (تومان)',
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': 'مثال: 120000 یا ۱۲۳٬۴۵۶',
            'inputmode': 'decimal',
            'dir': 'ltr',
        })
    )

    # 🛑 فیلد total_price حذف شد چون دیگر در مدل نیست

    class Meta:
        model = Order
        fields = [
            'patient_name', 'doctor',
            'order_type', 'unit_count', 'shade',
            'price', 'serial_number',
            'status', 'order_date', 'due_date', 'notes'
        ]

    def clean_price(self):
        raw = self.cleaned_data.get('price', '')
        if raw is None:
            raise forms.ValidationError('قیمت الزامی است.')
        # تبدیل ارقام فارسی به انگلیسی و حذف جداکننده‌ها
        persian = '۰۱۲۳۴۵۶۷۸۹'
        english = '0123456789'
        for p, e in zip(persian, english):
            raw = raw.replace(p, e)
        raw = raw.replace(',', '').replace('٬', '').strip()
        try:
            value = Decimal(raw)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError('قیمت باید عددی باشد.')
        if value < 0:
            raise forms.ValidationError('قیمت نمی‌تواند منفی باشد.')
        return value


# -----------------------------
# Material Form
# -----------------------------
class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = ['name', 'quantity', 'unit']


# -----------------------------
# Payment Form
# -----------------------------
class PaymentForm(forms.ModelForm):
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

    class Meta:
        model = Payment
        fields = ['order', 'amount', 'method', 'payment_date', 'date']



















































