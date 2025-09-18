# core/urls.py
from django.urls import path
from . import views

app_name = 'core'  # اضافه کردن namespace برای آینده

urlpatterns = [
    path('', views.home, name='home'),  # صفحه اصلی با فرم‌ها و لیست‌ها
]
