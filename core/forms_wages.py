# core/forms_wages.py
from django import forms
from jalali_date.fields import JalaliDateField
from jalali_date.widgets import AdminJalaliDateWidget
from decimal import Decimal, InvalidOperation
from .models import StageWorkLog, StageInstance, StageTemplate, Order, Technician

class StageWorkLogPublicForm(forms.ModelForm):
    started_at  = JalaliDateField(label='تاریخ شروع',  widget=AdminJalaliDateWidget, required=False)
    finished_at = JalaliDateField(label='تاریخ پایان', widget=AdminJalaliDateWidget, required=False)

    class Meta:
        model = StageWorkLog
        fields = (
            "order", "stage_inst", "stage_tpl", "technician",
            "started_at", "finished_at",
            "quantity", "unit_wage", "status", "note",
        )
        widgets = {
            "order": forms.HiddenInput(),
            "stage_inst": forms.HiddenInput(),
            "stage_tpl": forms.HiddenInput(),
            "note": forms.TextInput(attrs={"placeholder": "توضیح (اختیاری)"}),
        }

    def clean_quantity(self):
        raw = self.cleaned_data.get("quantity")
        try:
            q = Decimal(str(raw or "0"))
        except (InvalidOperation, ValueError):
            raise forms.ValidationError("تعداد واحد نامعتبر است.")
        if q <= 0:
            raise forms.ValidationError("تعداد واحد باید بیشتر از صفر باشد.")
        return q

    def clean(self):
        cleaned = super().clean()
        order = cleaned.get("order")
        inst  = cleaned.get("stage_inst")
        tpl   = cleaned.get("stage_tpl")

        # حداقل یکی از stage_inst / stage_tpl باید مشخص باشد
        if not inst and not tpl:
            raise forms.ValidationError("مرحلهٔ سفارش مشخص نشده است.")
        # اگر فقط stage_inst آمده و tpl خالی است، بگذار مدل در save() خودش پر کند

        # امنیت: stage_inst باید متعلق به همین order باشد
        if inst and order and inst.order_id != order.id:
            raise forms.ValidationError("مرحله انتخاب‌شده متعلق به این سفارش نیست.")
        return cleaned


class WagePayoutNewForm(forms.Form):
    """
    فرم انتخاب تکنسین و بازهٔ جلالی برای ساخت پیش‌نمایش تسویه.
    """
    technician = forms.ModelChoiceField(
        queryset=Technician.objects.filter(is_active=True).order_by("name"),
        label="تکنسین",
    )
    period_start_j = JalaliDateField(
        label="تاریخ شروع بازه",
        widget=AdminJalaliDateWidget,
        required=False,
    )
    period_end_j = JalaliDateField(
        label="تاریخ پایان بازه",
        widget=AdminJalaliDateWidget,
        required=False,
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("period_start_j")
        end   = cleaned.get("period_end_j")

        # حداقل یکی از تاریخ‌ها باید وارد شود
        if not start and not end:
            raise forms.ValidationError("حداقل یکی از «تاریخ شروع» یا «تاریخ پایان» را وارد کنید.")

        # تاریخ پایان نباید قبل از شروع باشد
        if start and end and end < start:
            raise forms.ValidationError("تاریخ پایان نمی‌تواند قبل از تاریخ شروع باشد.")

        return cleaned


class WagePayoutConfirmForm(forms.Form):
    """
    فرم تأیید تسویه:
    - تکنسین و بازه به‌صورت hidden دوباره ارسال می‌شوند.
    - کاربر فقط کسورات/پاداش/توضیح/مرجع پرداخت را ویرایش می‌کند.
    """
    technician_id = forms.IntegerField(widget=forms.HiddenInput())
    period_start_j = forms.CharField(widget=forms.HiddenInput(), required=False)
    period_end_j   = forms.CharField(widget=forms.HiddenInput(), required=False)

    deductions_total = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        initial=Decimal('0.00'),
        label="جمع کسورات",
    )
    bonus_total = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        initial=Decimal('0.00'),
        label="جمع پاداش",
    )
    note = forms.CharField(
        max_length=200,
        required=False,
        label="توضیحات",
        widget=forms.TextInput(attrs={"placeholder": "توضیحات تسویه (اختیاری)"}),
    )
    payment_ref = forms.CharField(
        max_length=120,
        required=False,
        label="مرجع/شماره سند پرداخت",
    )

    def clean_deductions_total(self):
        v = self.cleaned_data.get("deductions_total") or Decimal('0.00')
        if v < 0:
            raise forms.ValidationError("کسورات نمی‌تواند منفی باشد.")
        return v

    def clean_bonus_total(self):
        v = self.cleaned_data.get("bonus_total") or Decimal('0.00')
        if v < 0:
            raise forms.ValidationError("پاداش نمی‌تواند منفی باشد.")
        return v
