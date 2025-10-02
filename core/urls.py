# core/urls.py
from django.urls import path
from django.views.generic import TemplateView  # NEW: برای داشبورد ساده
from . import views

app_name = 'core'  # namespace برای آینده

urlpatterns = [
    # ریشه: همان پنل کاربری فعلی (فرم ثبت سفارش + لیست سفارش‌ها)
    path('', views.home, name='home'),

    # ✅ تب «سفارش‌ها» → همان پنل کاربری فعلی
    path('orders/', views.home, name='orders_home'),

    # گزارش مالی/حسابداری
    path('accounting/', views.accounting_report, name='accounting_report'),

    # جزئیات و اقدامات سفارش
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
    path('orders/<int:order_id>/add-event/', views.add_order_event, name='add_order_event'),
    path('orders/<int:order_id>/deliver/', views.deliver_order, name='deliver_order'),

    # ✅ داشبورد برنامه (TemplateView ساده)
    path('dashboard/', views.dashboard, name='dashboard'),

]