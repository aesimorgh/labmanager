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

    # فیلدهای مورد استفاده در home.html
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
    # اصلاح فیلد price برای حذف Spinbox
    price = forms.DecimalField(
        label="قیمت به ازای هر واحد (تومان)",
        min_value=0,
        decimal_places=2,
        max_digits=12,
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
        fields = '__all__'

























































