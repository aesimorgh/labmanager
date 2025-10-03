from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages

from billing.models import LabProfile
from .forms import LabProfileForm


def home(request):
    # رکورد موجود یا None
    instance = LabProfile.objects.first()

    if request.method == "POST":
        form = LabProfileForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            lab_profile = form.save()
            messages.success(request, "تنظیمات لابراتوار ذخیره شد.")
            # برای جلوگیری از resubmit روی refresh
            return redirect(reverse("settings_app:settings_home"))
        else:
            messages.error(request, "بررسی کنید: بعضی فیلدها نیاز به اصلاح دارند.")
        lab_profile = instance  # برای سازگاری با context
    else:
        form = LabProfileForm(instance=instance)
        lab_profile = instance

    context = {
        "lab_profile": lab_profile,   # برای نمایش read-only بالای کارت (همانی که قبلاً اضافه کردیم)
        "form": form,                 # در گام بعد UI فرم را از این استفاده می‌کنیم
    }
    return render(request, 'settings_app/home.html', context)

