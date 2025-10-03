from django import forms
from billing.models import LabProfile


class LabProfileForm(forms.ModelForm):
    class Meta:
        model = LabProfile
        fields = [
            "name",
            "slogan",
            "logo_file",
            "logo_static_path",
            "card_no",
            "iban",
            "account_name",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "نام لابراتوار"}),
            "slogan": forms.TextInput(attrs={"class": "form-control", "placeholder": "شعار (اختیاری)"}),
            "logo_static_path": forms.TextInput(attrs={"class": "form-control", "placeholder": "مثلاً: img/academy-logo.png"}),
            "card_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "شماره کارت"}),
            "iban": forms.TextInput(attrs={"class": "form-control", "placeholder": "شماره شبا"}),
            "account_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "به نام"}),
        }

    def clean(self):
        data = super().clean()
        # اگر فایل لوگو نداریم، مسیر استاتیک می‌تونه جایگزین باشه (هر دو خالی هم مجازه)
        # اینجا فقط اعتبارسنجی سبک می‌گذاریم، بدون اجبار اضافی.
        return data
