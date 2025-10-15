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

     # <<< این سه خط را اضافه کن >>>
    path('transfer/', views.transfer_gate, name='transfer_gate'),
    path('api/doctors', views.api_doctors, name='api_doctors'),
    path('api/orders-by-doctor', views.api_orders_by_doctor, name='api_orders_by_doctor'),
    # <<< پایان اضافه >>>
    # گزارش مالی/حسابداری
    path('accounting/', views.accounting_report, name='accounting_report'),

    # جزئیات و اقدامات سفارش
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
    path('orders/<int:order_id>/add-event/', views.add_order_event, name='add_order_event'),
    path('orders/<int:order_id>/deliver/', views.deliver_order, name='deliver_order'),
    path('orders/<int:order_id>/edit/', views.order_edit, name='order_edit'),
    # ✅ داشبورد برنامه (TemplateView ساده)
    path('dashboard/', views.dashboard, name='dashboard'),
    path('transfer/', TemplateView.as_view(template_name='core/transfer_gate.html'), name='transfer_gate'),
    path('workbench/', views.workbench, name='workbench'),
    path('stages/<int:stage_id>/start/', views.stage_start_now, name='stage_start_now'),
    path('stages/<int:stage_id>/done/',  views.stage_done_today, name='stage_done_today'),
        # --- APIs برای پنل ورود/خروج ---
    path('api/doctors', views.api_doctors, name='api_doctors'),
    path('api/orders-by-doctor', views.api_orders_by_doctor, name='api_orders_by_doctor'),
    path('api/order-stages', views.api_order_stages, name='api_order_stages'),
]