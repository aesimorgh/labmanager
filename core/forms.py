from django import forms
from .models import Patient, Order
import django_jalali.forms as jforms


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = ['first_name', 'last_name', 'phone', 'email', 'address']


class OrderForm(forms.ModelForm):
    # استفاده از jDateField با ویجت jDateInput برای تاریخ شمسی
    due_date = jforms.jDateField(widget=jforms.jDateInput())

    class Meta:
        model = Order
        fields = [
            'patient', 'doctor', 'order_type', 'shade',
            'price', 'status', 'due_date', 'notes'
        ]
