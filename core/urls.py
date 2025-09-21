# core/urls.py
from django.urls import path
from . import views

app_name = 'core'  # namespace برای آینده

urlpatterns = [
    path('', views.home, name='home'),  # صفحه اصلی با فرم‌ها و لیست‌ها
    path('accounting/', views.accounting_report, name='accounting_report'),  # گزارش مالی/حسابداری
]
