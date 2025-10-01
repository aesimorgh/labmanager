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
